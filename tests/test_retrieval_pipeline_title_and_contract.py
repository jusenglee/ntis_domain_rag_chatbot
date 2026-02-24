from __future__ import annotations

import ast
from pathlib import Path
import sys
from typing import Any, Dict, Mapping

sys.path.append(str(Path(__file__).resolve().parents[1]))

import rag_parts.filters as filters
from rag_parts.planner_contract import StrategyCompiler
from rag_parts.search_preset import build_search_preset
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

    targets = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("_resolve_sparse_hits_metric", "_validate_lookup_join_hybrid_metrics")
    ]
    module = ast.Module(body=targets, type_ignores=[])
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




def _load_lookup_contract_helpers() -> Dict[str, Any]:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="rag_pipeline.py")

    targets = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("_has_any_ids", "_has_explicit_identifiers", "_resolve_sparse_hits_metric", "_resolve_effective_min_reranked")
    ]
    module = ast.Module(body=targets, type_ignores=[])
    ast.fix_missing_locations(module)

    ns: Dict[str, Any] = {
        "Any": Any,
        "Mapping": Mapping,
        "NormalizedIntent": Any,
        "os": __import__("os"),
    }
    exec(compile(module, filename="rag_pipeline.py", mode="exec"), ns)
    return ns

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


def _contains_title_match_any_for_keys(obj: Any, keys: set[str]) -> bool:
    if obj is None:
        return False
    if isinstance(obj, dict):
        if obj.get("key") in keys:
            match = obj.get("match")
            if isinstance(match, dict) and ("any" in match or "any_values" in match):
                return True
        return any(_contains_title_match_any_for_keys(v, keys) for v in obj.values())

    key = getattr(obj, "key", None)
    if key in keys:
        match = getattr(obj, "match", None)
        if match is not None and (hasattr(match, "any") or hasattr(match, "any_values")):
            return True

    if hasattr(obj, "model_dump"):
        try:
            return _contains_title_match_any_for_keys(obj.model_dump(), keys)
        except Exception:
            pass

    for attr in ("must", "should", "must_not", "min_should"):
        if hasattr(obj, attr) and _contains_title_match_any_for_keys(getattr(obj, attr), keys):
            return True

    if isinstance(obj, (list, tuple, set)):
        return any(_contains_title_match_any_for_keys(v, keys) for v in obj)

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




def test_hard_policy_title_filter_matches_title1_title2_only_payload(monkeypatch) -> None:
    dummy_qmodels = type("Dummy", (), {
        "FieldCondition": staticmethod(lambda **kwargs: {"kind": "field", **kwargs}),
        "MatchAny": staticmethod(lambda **kwargs: {"kind": "match_any", **kwargs}),
        "MatchValue": staticmethod(lambda **kwargs: {"kind": "match_value", **kwargs}),
        "Filter": staticmethod(lambda **kwargs: {"kind": "filter", **kwargs}),
    })
    monkeypatch.setattr(filters, "qmodels", dummy_qmodels)

    title_filter = filters.build_title_exact_filter(["NTIS", "지식정보서비스"])
    assert title_filter is not None
    min_should = title_filter.get("min_should")
    assert min_should in (1, {"min_count": 1}, {"count": 1})
    assert _contains_title_match_any_for_keys(title_filter, {"title_text", "title1", "title2"}) is True

    should = title_filter.get("should") or []
    keys = {cond.get("key") for cond in should if isinstance(cond, dict)}
    assert {"title_text", "title1", "title2"}.issubset(keys)

    payload = {"title1": "국가과학기술", "title2": "지식정보서비스"}
    terms = {"NTIS", "지식정보서비스"}
    payload_values = {str(payload.get("title1", "")).strip(), str(payload.get("title2", "")).strip()}
    assert terms.intersection(payload_values)


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


def test_validate_lookup_join_hybrid_metrics_no_raise_when_paths_active_but_zero_hits() -> None:
    validate_fn, _ = _load_validate_lookup_join_hybrid_metrics()

    validate_fn(
        mode="join",
        contract_scope="join:ntis_project_v1",
        timings={
            "dense_queries": 0.0,
            "lexical_scored": 0.0,
            "hybrid_once_hits": 0.0,
        },
        strict=True,
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


def test_lookup_join_validation_calls_use_explicit_non_strict_policy() -> None:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")

    assert 'contract_scope=f"join_hop1:{hop1_col}",' in source
    assert 'timings=local_timings_h1,\n                strict=False,' in source

    assert 'contract_scope=f"join_hop2:{hop2_col}",' in source
    assert 'timings=local_timings_h2,\n                strict=False,' in source

    assert 'contract_scope=f"{plan.mode}:{col}",' in source
    assert 'timings=local_timings,\n                strict=False,' in source


def test_resolve_sparse_hits_metric_prefers_lexical_scored() -> None:
    ns = _load_lookup_contract_helpers()
    fn = ns["_resolve_sparse_hits_metric"]

    assert fn({"lexical_scored": 3.0, "sparse_hits": 1.0}) == 3.0
    assert fn({"sparse_hits": 2.0}) == 2.0
    assert fn({}) == 0.0


def test_effective_min_reranked_relaxes_for_lookup_id(monkeypatch) -> None:
    ns = _load_lookup_contract_helpers()
    fn = ns["_resolve_effective_min_reranked"]

    intent = type("Intent", (), {"is_id_query": True, "action": "detail", "ids_map": {"pjt_id": ["1711134317"]}, "ids_flat": ["1711134317"]})()
    monkeypatch.setenv("RAG_MIN_RERANKED_LOOKUP_ID", "1")

    assert fn(intent=intent, mode="lookup", base_route="project", preset_min_reranked=4) == (1, "lookup_id_query")


def test_effective_min_reranked_keeps_default_for_non_id_lookup() -> None:
    ns = _load_lookup_contract_helpers()
    fn = ns["_resolve_effective_min_reranked"]

    intent = type("Intent", (), {"is_id_query": False, "action": "detail", "ids_map": {}, "ids_flat": []})()
    assert fn(intent=intent, mode="lookup", base_route="project", preset_min_reranked=4) == (4, "none")


def test_effective_min_reranked_clamps_by_hinted_limit_for_lookup_detail_id() -> None:
    ns = _load_lookup_contract_helpers()
    resolve_fn = ns["_resolve_effective_min_reranked"]

    intent = type("Intent", (), {"is_id_query": True, "action": "detail", "ids_map": {"pjt_id": ["1711134317"]}, "ids_flat": ["1711134317"]})()
    effective_min, reason = resolve_fn(
        intent=intent,
        mode="lookup",
        base_route="project",
        preset_min_reranked=4,
        hinted_limit=1,
    )
    assert effective_min == 1
    assert reason == "hinted_limit,lookup_id_query"


def test_lookup_detail_id_single_hit_does_not_raise_contract_violation(monkeypatch) -> None:
    ns = _load_lookup_contract_helpers()
    resolve_fn = ns["_resolve_effective_min_reranked"]

    intent = type("Intent", (), {"is_id_query": True, "action": "detail", "ids_map": {"pjt_id": ["1711134317"]}, "ids_flat": ["1711134317"]})()
    effective_min, _ = resolve_fn(
        intent=intent,
        mode="lookup",
        base_route="project",
        preset_min_reranked=4,
        hinted_limit=1,
    )

    one_hit = [object()]
    reason = enforce_reranked_contract(
        reranked=one_hit,
        min_reranked=effective_min,
        min_final_avg=0.0,
        min_final_max=0.0,
        score_topn=5,
        timing_put=lambda *_a, **_k: None,
    )
    assert reason is None


def test_detail_with_id_query_uses_id_exact_like_preset(monkeypatch) -> None:
    monkeypatch.setenv("RAG_TOPK_DENSE_ID_EXACT", "8")
    monkeypatch.setenv("RAG_TOPK_DENSE", "25")

    intent = type(
        "Intent",
        (),
        {
            "action": "detail",
            "is_id_query": True,
            "base_route": "project",
            "relation": None,
            "org_terms": [],
        },
    )()

    preset = build_search_preset(intent)
    assert preset.top_k_dense == 8
    assert preset.stop_if_top1_confident is True
