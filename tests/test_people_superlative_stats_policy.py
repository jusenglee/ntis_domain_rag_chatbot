from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional
import re
import time
import unicodedata
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from rag_parts.pipeline_steps import NormalizedIntent
from rag_parts.planner_contract import normalize_stats_policy_value


def _load_people_agg_fn():
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="rag_pipeline.py")
    wanted = {
        "_normalize_person_group_key",
        "_resolve_people_agg_candidate_limit",
        "_extract_year_from_payload",
        "_build_people_superlative_aggregation",
    }
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    ns: Dict[str, Any] = {
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "Mapping": Mapping,
        "re": re,
        "time": time,
        "unicodedata": unicodedata,
        "normalize_stats_policy_value": normalize_stats_policy_value,
        "_payload_get": lambda payload, key: (payload.get("meta_basic") or {}).get(key.split(".")[-1]) if isinstance(payload, dict) else None,
        "_pjt_id": lambda payload: payload.get("pjt_id") if isinstance(payload, dict) else None,
        "_classify_tag_family": lambda tag: "perf" if str(tag).startswith("IRD_NAI_RI_") else "project",
        "_normalize_tag_value": lambda tag: str(tag or "").strip(),
        "NormalizedIntent": NormalizedIntent,
    }
    exec(compile(module, filename="rag_pipeline.py", mode="exec"), ns)
    return ns["_build_people_superlative_aggregation"]


def _intent(**kwargs):
    base = dict(
        action="stats",
        base_route="people",
        relation=None,
        is_id_query=False,
        wants_rank=True,
    )
    base.update(kwargs)
    return NormalizedIntent(**base)


def test_people_aggregation_applies_default_window_and_meta() -> None:
    build_agg = _load_people_agg_fn()
    point = SimpleNamespace(
        payload={
            "stan_yr": "2024",
            "prtcp_mp": [{"hm_id": "P-1", "hm_nm": "홍길동"}],
            "meta_basic": {"pjt_id": "PJ-1"},
            "tag": "IRD_NAI_PJT_INFO",
        }
    )
    agg = build_agg(reranked=[point], intent=_intent(), hinted_limit=0, policy_limit=10)
    assert agg is not None
    assert agg["metric"] == "project_participation_count"
    assert agg["meta"]["stats"]["metric_applied"] == "project_participation_count"


def test_people_aggregation_enforces_year_window_filter() -> None:
    build_agg = _load_people_agg_fn()
    inside = SimpleNamespace(
        payload={
            "stan_yr": "2024",
            "prtcp_mp": [{"hm_id": "P-1", "hm_nm": "홍길동"}],
            "meta_basic": {"pjt_id": "PJ-1"},
            "tag": "IRD_NAI_PJT_INFO",
        }
    )
    outside = SimpleNamespace(
        payload={
            "stan_yr": "2018",
            "prtcp_mp": [{"hm_id": "P-2", "hm_nm": "김철수"}],
            "meta_basic": {"pjt_id": "PJ-2"},
            "tag": "IRD_NAI_PJT_INFO",
        }
    )
    agg = build_agg(
        reranked=[inside, outside],
        intent=_intent(year_from="2024", year_to="2024", top_k=5),
        hinted_limit=0,
        policy_limit=10,
    )
    assert agg is not None
    assert [item["hm_nm"] for item in agg["rank_items"]] == ["홍길동"]
    assert agg["window_docs"] == 1
    assert agg["meta"]["stats"]["window_applied"] == {"from": "2024", "to": "2024"}


def test_people_aggregation_deduplicates_duplicate_participants_by_project() -> None:
    build_agg = _load_people_agg_fn()
    point = SimpleNamespace(
        payload={
            "stan_yr": "2024",
            "meta_basic": {"pjt_id": "PJ-1"},
            "tag": "IRD_NAI_PJT_INFO",
            "prtcp_mp": [
                {"hm_id": "P-1", "hm_nm": "홍길동"},
                {"hm_id": "P-1", "hm_nm": "홍길동"},
            ],
        }
    )

    agg = build_agg(reranked=[point], intent=_intent(top_k=5), hinted_limit=0, policy_limit=10)
    assert agg is not None
    assert len(agg["rank_items"]) == 1
    assert agg["rank_items"][0]["project_participation_count"] == 1


def test_people_aggregation_prefers_hm_id_as_group_key() -> None:
    build_agg = _load_people_agg_fn()
    points = [
        SimpleNamespace(
            payload={
                "stan_yr": "2024",
                "meta_basic": {"pjt_id": "PJ-1"},
                "tag": "IRD_NAI_PJT_INFO",
                "prtcp_mp": [{"hm_id": "P-1", "hm_nm": "홍길동"}],
            }
        ),
        SimpleNamespace(
            payload={
                "stan_yr": "2024",
                "meta_basic": {"pjt_id": "PJ-2"},
                "tag": "IRD_NAI_PJT_INFO",
                "prtcp_mp": [{"hm_id": "P-1", "hm_nm": "홍 길 동"}],
            }
        ),
    ]

    agg = build_agg(reranked=points, intent=_intent(top_k=5), hinted_limit=0, policy_limit=10)
    assert agg is not None
    assert len(agg["rank_items"]) == 1
    assert agg["rank_items"][0]["hm_id"] == "P-1"
    assert agg["rank_items"][0]["project_participation_count"] == 2


def test_people_aggregation_stable_tie_break_sorting() -> None:
    build_agg = _load_people_agg_fn()
    points = [
        SimpleNamespace(
            payload={
                "stan_yr": "2024",
                "meta_basic": {"pjt_id": "PJ-1"},
                "tag": "IRD_NAI_PJT_INFO",
                "prtcp_mp": [
                    {"hm_id": "P-2", "hm_nm": "가나다"},
                    {"hm_id": "P-1", "hm_nm": "가나다"},
                ],
            }
        )
    ]

    agg = build_agg(reranked=points, intent=_intent(top_k=5), hinted_limit=0, policy_limit=10)
    assert agg is not None
    assert [item["hm_id"] for item in agg["rank_items"]] == ["P-1", "P-2"]


def test_people_aggregation_applies_recent_n_year_window() -> None:
    build_agg = _load_people_agg_fn()
    current_year = time.gmtime().tm_year
    points = [
        SimpleNamespace(
            payload={
                "stan_yr": str(current_year),
                "prtcp_mp": [{"hm_id": "P-1", "hm_nm": "홍길동"}],
                "meta_basic": {"pjt_id": "PJ-1"},
                "tag": "IRD_NAI_PJT_INFO",
            }
        ),
        SimpleNamespace(
            payload={
                "stan_yr": str(current_year - 5),
                "prtcp_mp": [{"hm_id": "P-2", "hm_nm": "김철수"}],
                "meta_basic": {"pjt_id": "PJ-2"},
                "tag": "IRD_NAI_PJT_INFO",
            }
        ),
    ]

    agg = build_agg(reranked=points, intent=_intent(window_years=1, top_k=5), hinted_limit=0, policy_limit=10)
    assert agg is not None
    assert agg["window_years"]["from"] == str(current_year)
    assert agg["window_years"]["to"] == str(current_year)
    assert [item["hm_id"] for item in agg["rank_items"]] == ["P-1"]
