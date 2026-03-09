from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from rag_parts.pipeline_steps import normalize_intent
from rag_parts.planner_contract import StrategyCompiler, StrategyViolation, validate_planner_contract
from rag_parts.query_intent import QueryIntent, classify_query


GOLDEN_MIN_CASES = [
    {
        "name": "id_detail",
        "query": "1711015550 과제 상세",
        "expected": {
            "action": "id_exact",
            "base_route": "project",
            "relation": None,
            "ids_map": {"pjt_id": ["1711015550"]},
        },
        "planner_output": {
            "mode": "lookup",
            "relation": None,
            "target_cols": ["ntis_project_v1"],
            "planner_filter_spec": {"qdrant_filter": {"must": [{"key": "pjt_id", "match_any": ["1711015550"]}]},},
        },
        "hop": {"hop1": None, "hop2": None},
    },
    {
        "name": "project_perf_relation",
        "query": "PJT_NO 동일과제 성과",
        "expected": {
            "action": "relation",
            "base_route": "project",
            "relation": ("project", "perf"),
        },
        "planner_output": {
            "mode": "join",
            "relation": ("project", "perf"),
            "target_cols": ["ntis_project_v1", "ntis_perf_v1"],
            "planner_filter_spec": {
                "join_hop1_filter": {"must": [{"key": "pjt_no", "match_any": ["PJT-2023-0001"]}]},
                "join_filter": {"must": [{"key": "pjt_no", "match_any": ["PJT-2023-0001"]}]},
            },
        },
        "hop": {"hop1": "project", "hop2": "perf"},
    },
    {
        "name": "people_org_mixed",
        "query": "김재수 참여 과제",
        "expected": {
            "action": "list",
            "base_route": "project",
            "relation": None,
        },
        "planner_output": {
            "mode": "lookup",
            "relation": None,
            "target_cols": ["ntis_project_v1"],
            "planner_filter_spec": {
                "qdrant_filter": {
                    "should": [
                        {"key": "participant_researcher_name", "match_any": ["김재수"]},
                        {"key": "participant_org_name", "match_any": ["ETRI"]},
                    ],
                    "min_should": 1,
                }
            },
        },
        "hop": {"hop1": None, "hop2": None},
    },
    {
        "name": "year_perf_type",
        "query": "2021~2023 ETRI 논문 통계",
        "expected": {
            "action": "stats",
            "base_route": "perf",
            "relation": None,
        },
        "planner_output": {
            "mode": "lookup",
            "relation": None,
            "target_cols": ["ntis_perf_v1"],
            "planner_filter_spec": {
                "qdrant_filter": {
                    "must": [
                        {"key": "year", "range": {"gte": 2021, "lte": 2023}},
                        {"key": "tag", "match_any": ["IRD_NAI_RI_PAPER"]},
                    ]
                }
            },
        },
        "hop": {"hop1": None, "hop2": None},
    },
    {
        "name": "followup_reference",
        "query": "김재수 과제의 논문",
        "expected": {
            "action": "relation",
            "base_route": "project",
            "relation": ("project", "perf"),
        },
        "planner_output": {
            "mode": "join",
            "relation": ("project", "perf"),
            "target_cols": ["ntis_project_v1", "ntis_perf_v1"],
            "planner_filter_spec": {
                "join_hop1_filter": {"must": [{"key": "participant_researcher_name", "match_any": ["김재수"]}]},
                "join_filter": {"must": [{"key": "tag", "match_any": ["IRD_NAI_RI_PAPER"]}]},
            },
        },
        "hop": {"hop1": "project", "hop2": "perf"},
    },
]


def _compile(case: dict) -> tuple[object, object]:
    po = case["planner_output"]
    compiled = StrategyCompiler.compile(
        mode=po["mode"],
        relation=po["relation"],
        target_cols=po["target_cols"],
        fallback_target_cols=po["target_cols"],
        planner_filter_spec=po["planner_filter_spec"],
        topk_spec={},
        rerank_spec={},
        search_filter_signal=True,
        search_filter_conf_ok=True,
        lookup_filter_policy_hint="hard",
        lookup_title_filter_policy_hint="soft",
        detail_lookup_request=False,
        title_text_match_supported=False,
    )
    hop1 = compiled.hop1_spec["collection"] if compiled.hop1_spec else None
    hop2 = compiled.hop2_spec["collection"] if compiled.hop2_spec else None
    return hop1, hop2


@pytest.mark.parametrize("case", GOLDEN_MIN_CASES, ids=[c["name"] for c in GOLDEN_MIN_CASES])
def test_golden_query_stages(case: dict) -> None:
    intent = classify_query(case["query"], [])
    normalized = normalize_intent(intent, query=case["query"], keywords=[])

    assert normalized.action == case["expected"]["action"]
    assert normalized.base_route == case["expected"]["base_route"]
    assert normalized.relation == case["expected"]["relation"]
    if "ids_map" in case["expected"]:
        assert normalized.ids_map == case["expected"]["ids_map"]

    hop1, hop2 = _compile(case)
    assert hop1 == case["hop"]["hop1"]
    assert hop2 == case["hop"]["hop2"]


def _apply_policy(strict: bool, planner_like_intent: QueryIntent) -> str:
    violations = validate_planner_contract(
        mode="join",
        head=planner_like_intent.base_route,
        relation=planner_like_intent.relation,
        target_cols=["ntis_project_v1", "ntis_perf_v1"],
        ids_map=planner_like_intent.ids_map,
        relation_target_cols=("ntis_project_v1", "ntis_perf_v1"),
        join_key_mode=planner_like_intent.join_key_mode,
    )
    if violations and strict:
        raise StrategyViolation(violations[0].error_code, violations[0].reason, violations=violations)
    if violations:
        corrected = replace(planner_like_intent, relation=None, join_key_mode=None, action="id_exact")
        return corrected.action
    return planner_like_intent.action


def test_policy_strict_vs_compat_on_same_input() -> None:
    invalid_join = QueryIntent(
        base_route="perf",
        relation=("project", "perf"),
        intent="id",
        action="relation",
        is_id_query=True,
        long_query=False,
        rare_ratio=0.0,
        ids_map={"pjt_id": ["1711015550"]},
        join_key_mode="group",
    )

    with pytest.raises(StrategyViolation) as strict_error:
        _apply_policy(strict=True, planner_like_intent=invalid_join)
    assert strict_error.value.error_code == "PLANNER_JOIN_KEY_MODE_IDS_MISMATCH"

    compat_action = _apply_policy(strict=False, planner_like_intent=invalid_join)
    assert compat_action == "id_exact"
