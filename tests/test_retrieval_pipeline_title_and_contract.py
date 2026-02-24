from __future__ import annotations

import ast
from pathlib import Path
import sys
from typing import Any, Dict, Mapping

sys.path.append(str(Path(__file__).resolve().parents[1]))

import rag_parts.filters as filters
from rag_parts.planner_contract import StrategyCompiler
from rag_parts.result_contract import enforce_reranked_contract


def _load_title_helpers() -> Dict[str, Any]:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="rag_pipeline.py")

    targets = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("normalize_for_title_match", "_soft_title_contains")
    ]
    module = ast.Module(body=targets, type_ignores=[])
    ast.fix_missing_locations(module)

    ns: Dict[str, Any] = {
        "Any": Any,
        "Mapping": Mapping,
        "re": __import__("re"),
        "unicodedata": __import__("unicodedata"),
    }
    exec(compile(module, filename="rag_pipeline.py", mode="exec"), ns)
    return ns


def _load_validate_lookup_join_hybrid_metrics() -> Any:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="rag_pipeline.py")

    target = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_validate_lookup_join_hybrid_metrics"
    )
    module = ast.Module(body=[target], type_ignores=[])
    ast.fix_missing_locations(module)

    class _StrategyViolation(RuntimeError):
        def __init__(self, error_code: str, reason: str):
            super().__init__(f"{error_code}: {reason}")
            self.error_code = error_code
            self.reason = reason

    ns: Dict[str, Any] = {
        "Any": Any,
        "Mapping": Mapping,
        "StrategyViolation": _StrategyViolation,
        "log_kv": lambda *args, **kwargs: None,
    }
    exec(compile(module, filename="rag_pipeline.py", mode="exec"), ns)
    return ns["_validate_lookup_join_hybrid_metrics"], _StrategyViolation


def _contains_title_match_any(obj: Any) -> bool:
    if obj is None:
        return False
    if isinstance(obj, dict):
        if obj.get("key") == "title_text":
            match = obj.get("match")
            if isinstance(match, dict) and ("any" in match or "any_values" in match):
                return True
        return any(_contains_title_match_any(v) for v in obj.values())

    key = getattr(obj, "key", None)
    if key == "title_text":
        match = getattr(obj, "match", None)
        if match is not None and (hasattr(match, "any") or hasattr(match, "any_values")):
            return True

    if hasattr(obj, "model_dump"):
        try:
            return _contains_title_match_any(obj.model_dump())
        except Exception:
            pass

    for attr in ("must", "should", "must_not", "min_should"):
        if hasattr(obj, attr) and _contains_title_match_any(getattr(obj, attr)):
            return True

    if isinstance(obj, (list, tuple, set)):
        return any(_contains_title_match_any(v) for v in obj)

    return False


def test_soft_policy_compiled_filter_excludes_title_match_any() -> None:
    compiled = StrategyCompiler.compile(
        mode="lookup",
        relation=None,
        target_cols=["ntis_project_v1"],
        fallback_target_cols=["ntis_project_v1"],
        planner_filter_spec={
            "qdrant_filter": {
                "must": [
                    {"key": "tag", "match": {"any": ["IRD_NAI_PJT_INFO"]}},
                ]
            }
        },
        topk_spec={},
        rerank_spec={},
        search_filter_signal=True,
        search_filter_conf_ok=True,
        lookup_filter_policy_hint="hard",
        lookup_title_filter_policy_hint="soft",
        detail_lookup_request=True,
        title_text_match_supported=False,
    )

    assert compiled.lookup_title_filter_policy == "soft"
    assert compiled.title_match_mode == "CONTAINS"
    assert _contains_title_match_any(compiled.qdrant_filter) is False


def test_hard_policy_detail_lookup_includes_title_exact_filter(monkeypatch) -> None:
    compiled = StrategyCompiler.compile(
        mode="lookup",
        relation=None,
        target_cols=["ntis_project_v1"],
        fallback_target_cols=["ntis_project_v1"],
        planner_filter_spec={"qdrant_filter": None},
        topk_spec={},
        rerank_spec={},
        search_filter_signal=True,
        search_filter_conf_ok=True,
        lookup_filter_policy_hint="hard",
        lookup_title_filter_policy_hint="hard",
        detail_lookup_request=True,
        title_text_match_supported=False,
    )

    assert compiled.lookup_title_filter_policy == "hard"
    assert compiled.title_match_mode == "EXACT"

    dummy_qmodels = type("Dummy", (), {
        "FieldCondition": staticmethod(lambda **kwargs: {"kind": "field", **kwargs}),
        "MatchAny": staticmethod(lambda **kwargs: {"kind": "match_any", **kwargs}),
        "MatchValue": staticmethod(lambda **kwargs: {"kind": "match_value", **kwargs}),
        "Filter": staticmethod(lambda **kwargs: {"kind": "filter", **kwargs}),
    })
    monkeypatch.setattr(filters, "qmodels", dummy_qmodels)

    title_filter = filters.build_title_exact_filter(["국가과학기술지식정보서비스"])
    assert title_filter is not None
    assert _contains_title_match_any(title_filter) is True


def test_soft_title_contains_accepts_each_term() -> None:
    ns = _load_title_helpers()
    fn = ns["_soft_title_contains"]

    payload = {
        "title_text": "국가과학기술지식정보서비스(총괄) Construction of NTIS",
    }

    assert fn(payload, ["국가과학기술지식정보서비스"]) is True
    assert fn(payload, ["Construction of NTIS"]) is True


def test_soft_title_policy_does_not_hard_filter_merged_pool() -> None:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    start = source.index("    # final rerank")
    end = source.index(
        "    _timing_put(timings, \"info.title_post_filter_applied\"",
        start,
    )
    block = source[start:end]

    assert "title_post_filter_applied = True" not in block
    assert "merged_rrf = [" not in block


def test_validate_lookup_join_hybrid_metrics_no_raise_when_strict_false() -> None:
    validate_fn, _ = _load_validate_lookup_join_hybrid_metrics()

    validate_fn(
        mode="lookup",
        contract_scope="lookup:ntis_project_v1",
        timings={
            "dense_queries": 0.0,
            "lexical_scored": 0.0,
            "hybrid_once_hits": 0.0,
        },
        strict=False,
    )


def test_zero_hit_contract_returns_reason_meta_without_raise(monkeypatch) -> None:
    monkeypatch.setenv("RAG_FORCE_FALLBACK_CHAT", "1")

    meta: Dict[str, Any] = {}

    reason = enforce_reranked_contract(
        reranked=[],
        min_reranked=0,
        min_final_avg=0.0,
        min_final_max=0.0,
        score_topn=5,
        timing_put=lambda key, value: meta.__setitem__(key, value),
    )

    assert reason == "no_reranked"
    assert meta.get("info.contract_fail_reason") == "no_reranked"

    monkeypatch.delenv("RAG_FORCE_FALLBACK_CHAT", raising=False)
