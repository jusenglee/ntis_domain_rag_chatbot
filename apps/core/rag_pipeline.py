# -*- coding: utf-8 -*-
"""Main RAG execution pipeline for SEARCH, LOOKUP, and JOIN.\n\nThis module remains large, but `apps/core/*` is the source of truth. Runtime behavior should be described from this module outward.\n"""
from __future__ import annotations
from collections.abc import Mapping
import os
import re
import time
from dataclasses import fields, replace
from typing import Any, Dict, Iterable, List, Optional, Tuple
from apps.core.pipeline_steps import NormalizedIntent
from apps.core.schemas import (
    ExecutionContext,
    QueryPlan,
    StrategySpec,
    build_query_plan as _build_query_plan_policy,
    default_target_collections as _default_target_collections_policy,
    default_target_collections_for_route as _default_target_collections_for_route_policy,
    select_mode_policy as _select_mode_policy_policy,
)
from apps.core.settings import (
    DEFAULT_MODEL_NAME,
    logger,
    get_ctx_token_budget,
    get_model_max_output_tokens,
    RAG_COLLECTION_ALLOWLIST,
)
from apps.core.rag_types import RagResult
from apps.core.rag_store import build_rag_objects
from apps.core.retrieval import (
    normalize_query,
    dense_retrieve_hybrid_multi,
    build_context_mixed,
    _payload_get,
)
from apps.core.planner_locking import resolve_planner_locked_plan
from apps.core.rag_strategy_guard import (
    derive_planner_locks,
    normalize_strategy_target_cols,
    strategy_consistency_or_violation,
    strategy_field_diff,
    strategy_must_match_or_violation,
    validate_project_key_exclusive,
)
from apps.core.rag_compile_runtime import assemble_runtime_compile_policy
from apps.core.rag_execution_policy import resolve_join_execution_policy
from apps.core.rag_collection_retrieval import retrieve_collections
from apps.core.rag_base_orchestration import (
    BaseFilterPolicyInputs,
    BaseOrchestrationRequest,
    BaseSearchPolicyInputs,
    execute_base_orchestration,
)
from apps.core.rag_runtime_prelude import (
    RuntimePreludeRequest,
    RuntimePreludeRuntime,
    build_runtime_prelude,
)
from apps.core.rag_dense_runtime_support import build_dense_runtime_support
from apps.core.rag_executor_support import build_executor_support
from apps.core.rag_rerank_support import RerankSupportRuntime, build_final_rerank_fn, soft_title_contains, _flatten_ids_from_intent
from apps.core.rag_runtime_observability import build_runtime_observability
from apps.core.rag_dispatch_runtime import build_dispatch_runtime_support
from apps.core.rag_runtime_safety import (
    build_people_superlative_aggregation as _build_people_superlative_aggregation,
    debug_force_join_keys_enabled as _debug_force_join_keys_enabled,
    get_ctx_hard_limit as _get_ctx_hard_limit,
    with_org_must_gate as _with_org_must_gate_base,
)
from apps.core.rag_join_orchestration import (
    JoinOrchestrationRequest,
    PerfFollowupRequest,
    execute_join_orchestration,
)
from apps.api.services.context_build_policy import (
    build_context_with_output_type,
    normalize_output_type,
    resolve_output_fieldset, _pick_first,
)
from apps.api.services.rag_result_assembly import (
    assemble_join_rag_result,
    finalize_rag_result,
    resolve_effective_min_reranked,
)
from apps.api.services.rag_postprocess_policy import (
    hydrate_reranked_payloads,
    prepare_title_post_rerank,
)

# -------------------------
# core/runtime imports
# -------------------------
from apps.core.rag_constants import (
    COL_SUPPORT,
    COL_PROJECT,
    COL_PERF,
    TAG_PJT_INFO,
    TAG_PJT_MP,
    TAG_PJT_ORG,
    normalize_perf_types,
)
from apps.core.query_intent import (
    get_relation_route,
    relation_target_collections,
    normalize_categories,
    normalize_org_terms,
    pick_perf_tag_filters,
)
from apps.core.rag_search_policy import (
    SearchPreset as _SearchPreset,
    build_search_preset as _build_search_preset,
    build_topk_spec as _build_topk_spec,
    resolve_sparse_vector_name as _resolve_sparse_vector_name,
    SEARCH_POLICY_VERSION,
    build_strategy_key,
    build_rerank_spec as _build_rerank_spec,
    named_vectors_in_collection as _named_vectors_in_collection,
)
from apps.core.planner_contract import (
    planner_contract_mode,
    validate_planner_contract,
    normalize_stats_policy_value,
    StrategyViolation,
)
from apps.core.rag_rank_runtime import (
    dedup_by_doc_id as _dedup_by_doc_id,
    RankSource as _RankSource,
    hit_key as _hit_key,
    resolve_collection as _resolve_collection,
    rrf_merge as _rrf_merge,
    classify_tag_family as _classify_tag_family,
    normalize_tag_value as _normalize_tag_value,
    split_tag_filters_by_family as _split_tag_filters_by_family, PROJECT_TAGS_NORM, PERF_TAGS_NORM,
)
from apps.core.result_contract import enforce_reranked_contract as _enforce_reranked_contract
from apps.core.log_keys import (
    LOG_KEY_CHANGED_BY,
    LOG_KEY_FORCE_FALLBACK_CHAT,
    LOG_KEY_PLANNER_INVALID_FALLBACK,
    LOG_KEY_POLICY_MODE,
    LOG_KEY_EXECUTION_MODE,
    LOG_KEY_STRATEGY_MUTATION_STAGE,
    LOG_KEY_STRICT_STRATEGY_CONSISTENCY,
    CHANGED_BY_EXECUTOR,
)
from apps.core.rag_join_runtime import (
    extract_join_keys as _extract_join_keys,
    is_valid_join_key as _is_valid_join_key,
    normalize_relation_hint as _normalize_relation_hint,
)
from apps.core.filters import (
    build_tag_only_filter as _build_tag_only_filter,
    build_collection_join_filter,
    build_perf_filter_by_pjt_id,
    build_year_range_filter,
    build_perf_type_filter,
    and_filter as _and_filter, build_org_filter, build_prtcp_org_nested_filter, build_people_filter,
    make_match_any,
    build_project_id_filter,
    build_title_exact_filter,
    build_title_text_filter,
    TITLE_MATCH_MODE_EXACT,
    TITLE_MATCH_MODE_TEXT,
    TITLE_MATCH_MODE_CONTAINS,
    validate_resolved_join_keys,
    JoinFilterInput,
    PeopleFilterInput, OrgFilterInput,
)

try:
    from qdrant_client.http import models as qmodels
except Exception:
    qmodels = None


"""Main runtime helpers for the RAG dispatcher."""

_observability = build_runtime_observability(
    get_meta_fn=lambda payload: _get_meta(payload),
    resolve_collection_fn=lambda point, payload: _resolve_collection(point, payload),
    logger_obj=logger,
)

_clip_text = _observability.clip_text_fn
_merge_log_fields = _observability.merge_log_fields_fn
log_section = _observability.log_section_fn
log_kv = _observability.log_kv_fn
log_top_points = _observability.log_top_points_fn
_resolve_env_topn = _observability.resolve_env_topn_fn
_init_timings = _observability.init_timings_fn
_timing_put = _observability.timing_put_fn
_record_col_timings = _observability.record_col_timings_fn
_clean_one_line = _observability.clean_one_line_fn

executor_support = build_executor_support()

_get_meta = executor_support.get_meta_fn
_count_missing_join_keys = executor_support.count_missing_join_keys_fn
_resolve_group_pjt_ids = executor_support.resolve_group_pjt_ids_fn
_payload_title = executor_support.payload_title_fn
_serialize_filter_for_log = executor_support.serialize_filter_for_log_fn

dense_runtime_support = build_dense_runtime_support(
    dense_retrieve_hybrid_multi_fn=dense_retrieve_hybrid_multi,
    strategy_violation_cls=StrategyViolation,
    log_kv_fn=log_kv,
)

_PrecomputedEmbedding = dense_runtime_support.precomputed_embedding_cls
_call_dense_retrieve_hybrid_multi = dense_runtime_support.call_dense_retrieve_hybrid_multi_fn
_validate_lookup_join_hybrid_metrics = dense_runtime_support.validate_lookup_join_hybrid_metrics_fn
_resolve_sparse_hits_metric = dense_runtime_support.resolve_sparse_hits_metric_fn
_ensure_collection_mark = dense_runtime_support.ensure_collection_mark_fn
_apply_dense_threshold = dense_runtime_support.apply_dense_threshold_fn
_use_dense_score_weight = dense_runtime_support.use_dense_score_weight_fn
_dense_score_weight = dense_runtime_support.dense_score_weight_fn


# -------------------------
# Plan
# -------------------------
def _has_any_ids(it: NormalizedIntent) -> bool:
    """정규화 intent에 어떤 형태로든 식별자가 들어왔는지 본다."""
    ids_map = getattr(it, "ids_map", None)
    if isinstance(ids_map, dict) and any(v for v in ids_map.values() if v):
        return True
    ids_flat = getattr(it, "ids_flat", None)
    if isinstance(ids_flat, list) and len(ids_flat) > 0:
        return True
    # backward compat
    legacy = getattr(it, "ids", None)
    if isinstance(legacy, dict) and any(v for v in legacy.values() if v):
        return True
    return False

def _has_explicit_identifiers(it: NormalizedIntent) -> bool:
    """사용자가 식별자 조회를 명시한 질의인지 판정한다."""
    if bool(getattr(it, "is_id_query", False)):
        return True
    if getattr(it, "action", None) in ("id_exact", "id_fuzzy"):
        return True
    ids_map = getattr(it, "ids_map", None) or {}
    if isinstance(ids_map, dict):
        for values in ids_map.values():
            if values:
                return True
    ids_flat = getattr(it, "ids_flat", None) or []
    if isinstance(ids_flat, list) and ids_flat:
        return True
    return False

def _has_relation_join_ids(it: NormalizedIntent) -> bool:
    """JOIN 또는 followup seed로 쓸 수 있는 식별자가 있는지 확인한다."""
    ids_map = getattr(it, "ids_map", None) or {}
    if not isinstance(ids_map, dict):
        return False
    if any(ids_map.get(key) for key in ("pjt_id", "pjt_no")):
        return True
    perf_id_keys = (
        "doi",
        "issn",
        "eissn",
        "pissn",
        "patent_reg_no",
        "patent_app_no",
        "paper_id",
        "perf_id",
        "rst_id",
    )
    return any(ids_map.get(key) for key in perf_id_keys)


def _select_mode_policy(it: NormalizedIntent) -> Tuple[str, str]:
    """mode 선택 정책의 실제 구현을 schema 계층에 위임한다."""
    return _select_mode_policy_policy(it)


def _build_plan(
        it: NormalizedIntent,
        *,
        preferred_mode: Optional[str] = None,
        preferred_mode_source: Optional[str] = None,
) -> Tuple[QueryPlan, str]:
    """정규화 intent와 선택된 mode를 바탕으로 실행 계획을 만든다."""
    return _build_query_plan_policy(
        it,
        preferred_mode=preferred_mode,
        preferred_mode_source=preferred_mode_source,
    )


def _default_target_collections() -> List[str]:
    """planner가 컬렉션을 잠그지 않았을 때의 기본 조회 대상을 돌려준다."""
    return list(_default_target_collections_policy())


def _assert_allowlist_only(*, target_cols: List[str], allow_cols: List[str], source: str) -> None:
    """planner가 고른 target_cols가 런타임 allowlist를 넘지 않도록 강제한다."""
    normalized_targets = normalize_strategy_target_cols(target_cols)
    normalized_allow = normalize_strategy_target_cols(allow_cols)
    if not normalized_allow:
        return
    disallowed = [col for col in normalized_targets if col not in set(normalized_allow)]
    if not disallowed:
        return

    log_kv(
        "RAG.STRATEGY.ALLOWLIST",
        policy="validation_only",
        source=source,
        target_cols=normalized_targets,
        allow_cols=normalized_allow,
        disallowed_cols=disallowed,
     tier="debug")
    raise StrategyViolation(
        error_code="PLANNER_TARGET_COLS_ALLOWLIST_VIOLATION",
        reason=(
            "planner target_cols contains disallowed collections"
            f"(target_cols={normalized_targets}, allowlist={normalized_allow}, disallowed={disallowed})"
        ),
    )


def _diff_filter_spec(
        *,
        planner_filter_spec: Dict[str, Any],
        executed_filter_spec: Dict[str, Any],
        list_match_mode: str = "exact",
) -> Dict[str, Any]:
    """planner filter와 executor filter의 의미 차이를 비교해 로그용 diff를 만든다.
    
    executor가 planner 계약을 약화하거나 다른 필드를 섞었는지 추적할 때 사용한다."""

    list_mode = str(list_match_mode or "exact").strip().lower()
    if list_mode not in {"exact", "subset"}:
        list_mode = "exact"

    def _semantic_subset_equal(planner_val: Any, exec_val: Any) -> bool:
        """subset ?? ??? ??? planner ??? executor ??? ????? ????."""
        # Planner values may describe only a required subtree when subset matching is enabled.
        if isinstance(planner_val, Mapping):
            if not isinstance(exec_val, Mapping):
                return False
            for sub_key, sub_planner_val in planner_val.items():
                if sub_key not in exec_val:
                    return False
                if not _semantic_subset_equal(sub_planner_val, exec_val.get(sub_key)):
                    return False
            return True

        if isinstance(planner_val, list):
            if not isinstance(exec_val, list):
                return False
            if list_mode == "exact":
                if len(planner_val) != len(exec_val):
                    return False
                return all(_semantic_subset_equal(p, e) for p, e in zip(planner_val, exec_val))

            matched = [False] * len(exec_val)
            for p in planner_val:
                found = False
                for idx, e in enumerate(exec_val):
                    if matched[idx]:
                        continue
                    if _semantic_subset_equal(p, e):
                        matched[idx] = True
                        found = True
                        break
                if not found:
                    return False
            return True

        return planner_val == exec_val

    planner_keys = sorted(str(k) for k in (planner_filter_spec or {}).keys())
    changed: Dict[str, Dict[str, Any]] = {}
    for key in planner_keys:
        planner_val = planner_filter_spec.get(key)
        exec_val = executed_filter_spec.get(key)
        if not _semantic_subset_equal(planner_val, exec_val):
            changed[key] = {"planner": planner_val, "executed": exec_val, LOG_KEY_CHANGED_BY: CHANGED_BY_EXECUTOR}
    return {
        "planner_keys": planner_keys,
        "list_match_mode": list_mode,
        "changed": changed,
    }

# -------------------------
# 2-hop JOIN helpers
# -------------------------
def _extract_quoted_terms(q: str) -> List[str]:
    """따옴표 구문만 떄어 후단 제목 매칭이나 보조 해석에 재사용한다."""
    if not q:
        return []
    out = re.findall(r'"([^"]+)"', q) + re.findall(r"'([^']+)'", q)
    return [t.strip() for t in out if t.strip()]

# -------------------------
# Main
# -------------------------
def _run_rag_with_vectors(
        *,
        query: str,
        model_name: str,
        intent_payload: Any = None,
        stack: str,
        vector_names: List[str],
        w_dense_map: Dict[str, float],
        lexical_field_weights: Optional[Dict[str, float]] = None,
        sparse_vector_name: Optional[str] = None,
        sparse_topk: Optional[int] = None,
        sparse_weight: Optional[float] = None,
        domain_hint: Optional[str] = None,
) -> RagResult:
    """RAG 실행의 실제 진입점이다.
    
    runtime prelude로 계약을 고정한 뒤, JOIN이면 2-hop orchestration으로,
    아니면 base orchestration으로 보낸다. raw retrieval 결과는 여기서 바로 prompt에
    넣지 않고, 후단 context builder를 거쳐 output_type별 prompt view로 변환한다."""
    t_all0 = time.time()
    timings: Dict[str, Any] = _init_timings()

    q = normalize_query(query)
    if not q:
        _timing_put(timings, "phase.total", 0.0)
        _timing_put(timings, "info.contract_fail_reason", "empty_query")
        return RagResult(
            stack=stack, keywords=[], hits=[], reranked_hits=[], context="", refs=[],
            timings=timings,
        )

    # shared objects
    t0 = time.time()
    resources = build_rag_objects()
    qdr = resources.qdrant_client
    _timing_put(timings, "phase.stack_init", time.time() - t0)
    ctx_hard_limit = _get_ctx_hard_limit(model_name=model_name)

    fallback_emb: Dict[str, Any] = {
        "e5i_qa": resources.embed_e5i,
        "e5_qa": resources.embed_e5,
    }

    final_rerank_fn = build_final_rerank_fn(
        runtime=RerankSupportRuntime(
            payload_get_fn=_payload_get,
            get_meta_fn=_get_meta,
            payload_title_fn=_payload_title,
            clip_text_fn=_clip_text,
            log_kv_fn=log_kv,
            log_section_fn=log_section,
        )
    )

    effective_allow = list(RAG_COLLECTION_ALLOWLIST or [])
    hinted_cols: List[str] = []
    hinted_limit = 0

    prelude = build_runtime_prelude(
        request=RuntimePreludeRequest(
            query=q,
            model_name=model_name,
            intent_payload=intent_payload,
            domain_hint=domain_hint,
            stack=stack,
            lexical_field_weights=lexical_field_weights,
            sparse_vector_name=sparse_vector_name,
            sparse_topk=sparse_topk,
            sparse_weight=sparse_weight,
            effective_allow=list(effective_allow or []),
            hinted_limit=hinted_limit,
            hinted_cols=list(hinted_cols or []),
            w_dense_map=dict(w_dense_map or {}),
        ),
        runtime=RuntimePreludeRuntime(
            logger=logger,
            log_kv_fn=log_kv,
            log_section_fn=log_section,
            timing_put_fn=_timing_put,
            diff_filter_spec_fn=_diff_filter_spec,
            flatten_ids_from_intent_fn=_flatten_ids_from_intent,
            has_relation_join_ids_fn=_has_relation_join_ids,
        ),
        timings=timings,
    )

    q = prelude.query_text
    kws = list(prelude.keywords or [])
    it = prelude.intent_item
    ctx = prelude.context_state
    plan = prelude.plan
    strategy = prelude.strategy
    mode = prelude.mode
    action = prelude.action
    base_route = prelude.base_route
    relation = prelude.relation
    target_collections = list(prelude.target_collections or [])
    planner_limit = int(prelude.planner_limit or 0)
    hinted_limit = int(prelude.hinted_limit or 0)
    compiled_strategy = prelude.compiled_strategy
    planner_filter_spec = dict(prelude.planner_filter_spec or {})
    resolved_join_key_mode = prelude.resolved_join_key_mode
    planner_raw_join_key_mode = prelude.planner_raw_join_key_mode
    preset = prelude.preset
    lex_w_eff = dict(prelude.lex_w_eff or {})
    sparse_vector_name_eff = prelude.sparse_vector_name_eff
    sparse_topk_eff = int(prelude.sparse_topk_eff or 0)
    sparse_weight_eff = float(prelude.sparse_weight_eff or 0.0)
    topk_spec = dict(prelude.topk_spec or {})
    rerank_spec = dict(prelude.rerank_spec or {})
    topk_dense = int(prelude.topk_dense or 0)
    topk_lex_cand = int(prelude.topk_lex_cand or 0)
    topk_lex = int(prelude.topk_lex or 0)
    use_dense_threshold_policy = bool(prelude.use_dense_threshold_policy)
    min_dense_score_policy = float(prelude.min_dense_score_policy or 0.0)
    title_terms = list(prelude.title_terms or [])
    title_match_mode = str(prelude.title_match_mode or '')
    title_filter = prelude.title_filter
    title_filter_server_applied = bool(prelude.title_filter_server_applied)
    lookup_title_filter_policy = str(prelude.lookup_title_filter_policy or '')
    lookup_filter_policy = str(prelude.lookup_filter_policy or '')
    search_filter_signal = bool(prelude.search_filter_signal)
    search_filter_conf_ok = bool(prelude.search_filter_conf_ok)
    search_filter_enabled = bool(prelude.search_filter_enabled)
    lookup_filter_enabled = bool(prelude.lookup_filter_enabled)
    relation_lookup_enforce = bool(prelude.relation_lookup_enforce)
    join_hop1_lookup_filter_enabled = bool(prelude.join_hop1_lookup_filter_enabled)
    search_filter_server_policy = str(prelude.search_filter_server_policy or '')
    org_terms = list(prelude.org_terms or [])
    org_role = prelude.org_role
    people_terms = list(prelude.people_terms or [])
    people_ids = list(prelude.people_ids or [])
    gender_terms = list(prelude.gender_terms or [])
    people_org_terms = list(prelude.people_org_terms or [])
    people_min_should = prelude.people_min_should
    people_match_mode = prelude.people_match_mode
    people_promote_one_must = bool(prelude.people_promote_one_must)
    people_filter = prelude.people_filter
    participant_org_filter = prelude.participant_org_filter
    org_filter = prelude.org_filter
    planner_org_filter_present = bool(prelude.planner_org_filter_present)
    project_tag_filter = prelude.project_tag_filter
    perf_tag_filter = prelude.perf_tag_filter
    year_range_filter = prelude.year_range_filter
    perf_type_filter = prelude.perf_type_filter

    normalized_ids_map = dict(getattr(ctx, "ids_map", None) or {})
    normalized_pjt_ids = [str(x).strip() for x in (normalized_ids_map.get("pjt_id") or []) if str(x).strip()]
    normalized_pjt_nos = [str(x).strip() for x in (normalized_ids_map.get("pjt_no") or []) if str(x).strip()]

    def _with_org_must_gate(base_filter: Any, *, col: str, mode_override: Optional[str] = None) -> Any:
        """?? ??? ???? ??? ???? must gate? ????."""
        return _with_org_must_gate_base(
            base_filter,
            col=col,
            org_role=org_role,
            org_filter=org_filter,
            participant_org_filter=participant_org_filter,
            col_project=COL_PROJECT,
            and_filter_fn=_and_filter,
        )

    dispatch_runtime = build_dispatch_runtime_support(
        qdr=qdr,
        resources=resources,
        vector_names=vector_names,
        timings=timings,
        fallback_emb=fallback_emb,
        use_dense_threshold_policy=use_dense_threshold_policy,
        min_dense_score_policy=min_dense_score_policy,
        col_project=COL_PROJECT,
        col_perf=COL_PERF,
        col_support=COL_SUPPORT,
        tag_pjt_info=TAG_PJT_INFO,
        tag_pjt_mp=TAG_PJT_MP,
        tag_pjt_org=TAG_PJT_ORG,
        rag_collection_allowlist=RAG_COLLECTION_ALLOWLIST,
        project_tags_norm=PROJECT_TAGS_NORM,
        perf_tags_norm=PERF_TAGS_NORM,
        normalize_tag_value_fn=_normalize_tag_value,
        named_vectors_in_collection_fn=_named_vectors_in_collection,
        get_relation_route_fn=get_relation_route,
        build_tag_only_filter_fn=_build_tag_only_filter,
        and_filter_fn=_and_filter,
        build_project_id_filter_fn=build_project_id_filter,
        build_people_filter_fn=build_people_filter,
        build_collection_join_filter_fn=build_collection_join_filter,
        with_org_must_gate_fn=_with_org_must_gate,
        validate_resolved_join_keys_fn=validate_resolved_join_keys,
        retrieve_collections_fn=retrieve_collections,
        call_dense_retrieve_hybrid_multi_fn=_call_dense_retrieve_hybrid_multi,
        validate_lookup_join_hybrid_metrics_fn=_validate_lookup_join_hybrid_metrics,
        apply_dense_threshold_fn=_apply_dense_threshold,
        ensure_collection_mark_fn=_ensure_collection_mark,
        resolve_sparse_hits_metric_fn=_resolve_sparse_hits_metric,
        record_col_timings_fn=_record_col_timings,
        rank_source_factory=_RankSource,
        use_dense_score_weight_fn=_use_dense_score_weight,
        dense_score_weight_fn=_dense_score_weight,
        rrf_merge_fn=_rrf_merge,
        dedup_by_doc_id_fn=_dedup_by_doc_id,
        timing_put_fn=_timing_put,
        finalize_rag_result_fn=finalize_rag_result,
        resolve_env_topn_fn=_resolve_env_topn,
        prepare_title_post_rerank_fn=prepare_title_post_rerank,
        final_rerank_fn=final_rerank_fn,
        resolve_effective_min_reranked_fn=resolve_effective_min_reranked,
        has_explicit_identifiers_fn=_has_explicit_identifiers,
        enforce_reranked_contract_fn=_enforce_reranked_contract,
        hydrate_reranked_payloads_fn=hydrate_reranked_payloads,
        soft_title_contains_fn=soft_title_contains,
        aggregation_builder_fn=_build_people_superlative_aggregation,
        payload_get_fn=_payload_get,
        hit_key_fn=_hit_key,
        title_match_mode_contains=TITLE_MATCH_MODE_CONTAINS,
        serialize_filter_for_log_fn=_serialize_filter_for_log,
        diff_filter_spec_fn=_diff_filter_spec,
        merge_log_fields_fn=_merge_log_fields,
        extract_join_keys_fn=_extract_join_keys,
        resolve_group_pjt_ids_fn=_resolve_group_pjt_ids,
        count_missing_join_keys_fn=_count_missing_join_keys,
        debug_force_join_keys_enabled_fn=_debug_force_join_keys_enabled,
        get_meta_fn=_get_meta,
        pick_first_fn=_pick_first,
        people_filter_input_factory=PeopleFilterInput,
        join_filter_input_factory=JoinFilterInput,
        context_builder_fn=build_context_with_output_type,
        logger_obj=logger,
        log_kv_fn=log_kv,
        log_section_fn=log_section,
        log_top_points_fn=log_top_points,
        point_summary_get_meta_fn=_get_meta,
        point_summary_resolve_collection_fn=_resolve_collection,
        precomputed_embedding_cls=_PrecomputedEmbedding,
    )

    _hydrate_points = dispatch_runtime.hydrate_points_fn
    _get_attr = dispatch_runtime.get_attr_fn
    _category_to_base_route = dispatch_runtime.category_to_base_route_fn
    _normalize_target_collections = dispatch_runtime.normalize_target_collections_fn
    _coerce_int = dispatch_runtime.coerce_int_fn
    _get_pre_vecs = dispatch_runtime.get_pre_vecs_fn

    pre_vecs = _get_pre_vecs(q)

    def _maybe_followup_perf_hop_from_project() -> List[str]:
        """strict JOIN? ?? ?? project ???? perf followup seed? ????."""
        target_cols = list(target_collections or _default_target_collections())
        return dispatch_runtime.resolve_perf_followup_join_ids_fn(
            request=PerfFollowupRequest(
                relation=relation,
                ids_map=(getattr(it, "ids_map", None) or {}),
                mode=mode,
                target_cols=target_cols,
                query_text=q,
                keywords=kws,
                year_range_filter=year_range_filter,
                topk_dense=topk_dense,
                ctx_hard_limit=ctx_hard_limit,
                vector_names=vector_names,
                sparse_vector_name_eff=sparse_vector_name_eff,
                sparse_weight_eff=sparse_weight_eff,
                w_dense_map=w_dense_map,
                fallback_emb=fallback_emb,
                action=action,
                base_route=base_route,
                intent_item=it,
                lex_w_eff=lex_w_eff,
            ),
            preset=preset,
        )

    # -------------------------
    # JOIN mode (2-hop)
    # -------------------------
    if mode == "join" and relation:
        join_outcome = execute_join_orchestration(
            request=JoinOrchestrationRequest(
                relation=relation,
                action=action,
                mode=mode,
                base_route=base_route,
                query_text=q,
                keywords=kws,
                output_type=plan.output_type,
                planner_limit=int(planner_limit or 0),
                resolved_join_key_mode=resolved_join_key_mode,
                planner_raw_join_key_mode=planner_raw_join_key_mode,
                join_execution_policy=resolve_join_execution_policy(
                    relation=relation,
                    mode=plan.mode,
                    action=action,
                    join_key_mode=resolved_join_key_mode,
                    seed_join_pjt_ids=list(normalized_pjt_ids),
                    seed_join_pjt_nos=list(normalized_pjt_nos),
                    has_people_org_gate=bool(people_terms or people_ids or org_terms),
                ),
                compiled_strategy=compiled_strategy,
                planner_filter_spec=planner_filter_spec,
                context_state=ctx,
                people_terms=people_terms,
                people_ids=people_ids,
                org_terms=org_terms,
                org_role=org_role,
                people_org_terms=people_org_terms,
                gender_terms=gender_terms,
                people_min_should=people_min_should,
                people_match_mode=people_match_mode,
                people_promote_one_must=people_promote_one_must,
                lookup_filter_policy=lookup_filter_policy,
                join_hop1_lookup_filter_enabled=join_hop1_lookup_filter_enabled,
                people_filter=people_filter,
                participant_org_filter=participant_org_filter,
                org_filter=org_filter,
                project_tag_filter=project_tag_filter,
                perf_tag_filter=perf_tag_filter,
                year_range_filter=year_range_filter,
                perf_type_filter=perf_type_filter,
                topk_dense=topk_dense,
                sparse_vector_name_eff=sparse_vector_name_eff,
                sparse_weight_eff=sparse_weight_eff,
                ctx_hard_limit=ctx_hard_limit,
                vector_names=vector_names,
                w_dense_map=w_dense_map,
                fallback_emb=fallback_emb,
                lex_w_eff=lex_w_eff,
                t_all0=t_all0,
                stack=stack,
                timings=timings,
                intent_item=it,
                target_keep_hop2=int(os.getenv("RAG_HOP2_KEEP", "10")),
            ),
            runtime=dispatch_runtime.build_join_runtime_fn(preset=preset),
        )
        strategy = replace(
            strategy,
            join_key_source=join_outcome.join_key_source,
            hop1_mode=join_outcome.hop1_mode,
            join_compile_selection=join_outcome.join_compile_selection,
            hop2_key_strategy=join_outcome.hop2_key_strategy,
            resolved_runtime_key_kind=join_outcome.resolved_runtime_key_kind,
            join_keys_used_count=join_outcome.join_keys_used_count,
        )
        ctx.strategy = strategy
        return join_outcome.result

    # -------------------------
    # Base (SEARCH / LOOKUP) federated
    # -------------------------
    # collection list
    target_cols = list(target_collections or _default_target_collections())
    perf_followup_join_ids = _maybe_followup_perf_hop_from_project()
    perf_followup_filter = (
        build_perf_filter_by_pjt_id(
            perf_followup_join_ids,
            q,
            apply_query_tag_inference=False,
        )
        if perf_followup_join_ids
        else None
    )

    base_outcome = execute_base_orchestration(
        request=BaseOrchestrationRequest(
            mode=mode,
            plan_mode=plan.mode,
            base_route=base_route,
            relation=relation,
            action=action,
            output_type=plan.output_type,
            query_text=q,
            keywords=kws,
            query_intent=it,
            target_cols=target_cols,
            topk_dense=topk_dense,
            topk_lex_cand=topk_lex_cand,
            topk_lex=topk_lex,
            sparse_vector_name_eff=sparse_vector_name_eff,
            sparse_topk_eff=sparse_topk_eff,
            sparse_weight_eff=sparse_weight_eff,
            vector_names=vector_names,
            pre_vecs=pre_vecs,
            fallback_emb=fallback_emb,
            w_dense_map=w_dense_map,
            lex_w_eff=lex_w_eff,
            hinted_limit=hinted_limit,
            ctx_hard_limit=ctx_hard_limit,
            timings=timings,
            t_all0=t_all0,
            stack=stack,
            rerank_spec=rerank_spec,
            preset=preset,
            title_match_mode=title_match_mode,
            title_terms=title_terms,
            lookup_title_filter_policy=lookup_title_filter_policy,
            people_terms=people_terms,
            people_ids=people_ids,
            org_terms=org_terms,
            org_role=org_role,
            intent_payload=intent_payload,
            perf_followup_join_ids_count=len(perf_followup_join_ids),
        ),
        runtime=dispatch_runtime.build_base_runtime_fn(),
        filter_inputs=BaseFilterPolicyInputs(
            relation_lookup_enforce=relation_lookup_enforce,
            lookup_filter_enabled=lookup_filter_enabled,
            title_filter_server_applied=title_filter_server_applied,
            planner_org_filter_present=planner_org_filter_present,
            org_role=org_role,
            org_terms=list(org_terms or []),
            people_terms=list(people_terms or []),
            people_ids=list(people_ids or []),
            has_perf_ids=bool(any((getattr(it, "ids_map", {}) or {}).get(key) for key in ("doi", "issn", "eissn", "pissn", "patent_reg_no", "patent_app_no", "paper_id", "perf_id", "rst_id"))),
            pjt_ids=list(normalized_pjt_ids),
            pjt_nos=list(normalized_pjt_nos),
            people_filter=people_filter,
            participant_org_filter=participant_org_filter,
            org_filter=org_filter,
            title_filter=title_filter,
            project_tag_filter=project_tag_filter,
            perf_tag_filter=perf_tag_filter,
            perf_type_filter=perf_type_filter,
            year_range_filter=year_range_filter,
            perf_followup_filter=perf_followup_filter,
            col_project=COL_PROJECT,
            col_perf=COL_PERF,
        ),
        search_policy=BaseSearchPolicyInputs(
            search_filter_enabled=search_filter_enabled,
            search_filter_signal=search_filter_signal,
            search_filter_conf_ok=search_filter_conf_ok,
            title_filter_server_applied=title_filter_server_applied,
        ),
    )
    if base_outcome.followup_used:
        strategy = replace(
            strategy,
            join_key_source="followup",
            hop1_mode="lookup",
            join_compile_selection="planner_contract",
            hop2_key_strategy="pjt_id_in",
            resolved_runtime_key_kind="pjt_id",
            join_keys_used_count=base_outcome.followup_join_ids_count,
        )
        ctx.strategy = strategy
    return base_outcome.result

# -------------------------
# Public entry
# -------------------------
def run_rag_once(
        query: str,
        model_name: str = DEFAULT_MODEL_NAME,
        intent_payload: Any = None,
) -> RagResult:
    """기본 vector 설정으로 단일 RAG 실행을 수행하는 공개 엔트리포인트다."""
    domain_hint: Optional[str] = None
    vector_names_env = os.getenv("RAG_VECTOR_NAMES", "e5i_qa,e5_qa")
    vector_names = [v.strip() for v in vector_names_env.split(",") if v.strip()]
    w_dense_map = {
        "e5i_qa": 1.0,
        "e5_qa": 0.8,
        "e5i": 1.0,
        "e5": 0.8,
    }
    return _run_rag_with_vectors(
        query=query,
        model_name=model_name,
        intent_payload=intent_payload,
        stack="M",
        vector_names=vector_names or ["e5i_qa", "e5_qa"],
        w_dense_map=w_dense_map,
        lexical_field_weights=None,
        domain_hint=domain_hint,
    )

def run_rag_ab_compare(
        query: str,
        model_name: str = DEFAULT_MODEL_NAME,
        intent_payload: Any = None,
) -> Dict[str, RagResult]:
    """현재는 단일 스택 결과만 감싼 간이 비교 엔트리포인트다."""
    res_m = run_rag_once(query=query, model_name=model_name, intent_payload=intent_payload)
    return {"M": res_m}


