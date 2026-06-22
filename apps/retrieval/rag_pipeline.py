# -*- coding: utf-8 -*-
"""SEARCH, LOOKUP, JOIN을 실행하는 주 RAG 파이프라인.

모듈 크기는 크지만 retrieval 실행 기준 진실원은 여전히 이 패키지 아래에 있고,
runtime 동작 설명도 transport 계층이 아니라 여기서 바깥으로 퍼져 나가야 한다.
"""
from __future__ import annotations
from collections.abc import Mapping
import os
import re
import time
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple
from apps.platform.pipeline_steps import NormalizedIntent
from apps.platform.schemas import (
    QueryPlan,
    build_query_plan as _build_query_plan_policy,
    default_target_collections as _default_target_collections_policy,
    select_mode_policy as _select_mode_policy_policy,
)
from apps.platform.settings import (
    DEFAULT_MODEL_NAME,
    logger,
    RAG_COLLECTION_ALLOWLIST,
)
from apps.platform.rag_types import RagResult
from apps.retrieval.rag_store import build_rag_objects
from apps.retrieval.retrieval import (
    normalize_query,
    dense_retrieve_hybrid_multi,
    _payload_get,
)
from apps.retrieval.rag_strategy_guard import (
    normalize_strategy_target_cols,
)
from apps.retrieval.rag_execution_policy import resolve_join_execution_policy
from apps.retrieval.rag_base_orchestration import (
    BaseFilterPolicyInputs,
    BaseOrchestrationRequest,
    BaseSearchPolicyInputs,
    execute_base_orchestration,
)
from apps.retrieval.rag_runtime_prelude import (
    RuntimePreludeRequest,
    run_runtime_prelude,
)
from apps.retrieval.rag_dense_runtime_support import build_dense_runtime_support
from apps.retrieval.rag_executor_support import count_missing_join_keys, get_meta, payload_title, resolve_group_pjt_ids, serialize_filter_for_log
from apps.retrieval.rag_rerank_support import build_final_rerank
from apps.retrieval.rag_runtime_observability import clean_one_line, clip_text, init_timings, log_kv, log_section, log_top_points as runtime_log_top_points, merge_log_fields, point_summary as runtime_point_summary, record_col_timings, resolve_env_topn, timing_put
from apps.retrieval.rag_dispatch_runtime import build_join_runtime, resolve_perf_followup_join_ids_for_request
from apps.retrieval.rag_hydration_runtime import hydrate_points_payload
from apps.retrieval.rag_runtime_safety import (
    build_multi_hop_bundle_payload as _build_multi_hop_bundle_payload,
    build_project_series_payload as _build_project_series_payload,
    debug_force_join_keys_enabled as _debug_force_join_keys_enabled,
    get_ctx_hard_limit as _get_ctx_hard_limit,
    with_org_must_gate as _with_org_must_gate_base,
)
from apps.retrieval.rag_join_orchestration import (
    JoinOrchestrationRequest,
    PerfFollowupRequest,
    execute_join_orchestration,
)
from apps.evidence.context_build_policy import (
    build_context_with_output_type,
)

# -------------------------
# core/runtime 관련 import
# -------------------------
from apps.platform.rag_constants import (
    COL_PROJECT,
    COL_PERF,
    TAG_PJT_INFO,
    TAG_PJT_MP,
    TAG_PJT_ORG,
)
from apps.planner.query_intent import (
    get_relation_route,
)
from apps.retrieval.rag_search_policy import (
    named_vectors_in_collection as _named_vectors_in_collection,
)
from apps.planner.planner_contract import (
    StrategyViolation,
)


def _extract_intent_payload_version(intent_payload: Any) -> Optional[str]:
    """transport payload에 선언된 버전이 있으면 정규화해 반환한다."""
    if intent_payload is None:
        return None
    if isinstance(intent_payload, Mapping):
        value = intent_payload.get("intent_payload_version")
    else:
        value = getattr(intent_payload, "intent_payload_version", None)
    return str(value or "").strip().lower() or None


def _validate_intent_payload_version(intent_payload: Any) -> None:
    """intent payload가 들어온 경우 `v3` transport wrapper만 허용한다."""
    if intent_payload is None:
        return
    version = _extract_intent_payload_version(intent_payload)
    if version != "v3":
        raise ValueError(f"intent_payload_version must be 'v3', got {version!r}")


def _pick_first(*values: Any) -> str:
    """Return the first non-empty value as a trimmed string."""
    for value in values:
        if value is None:
            continue
        text = value.strip() if isinstance(value, str) else str(value).strip()
        if text:
            return text
    return ""


from apps.retrieval.rag_rank_runtime import (
    dedup_by_doc_id as _dedup_by_doc_id,
    RankSource as _RankSource,
    resolve_collection as _resolve_collection,
    rrf_merge as _rrf_merge,
    normalize_tag_value as _normalize_tag_value,
    PROJECT_TAGS_NORM, PERF_TAGS_NORM,
)
from apps.platform.log_keys import (
    LOG_KEY_CHANGED_BY,
    CHANGED_BY_EXECUTOR,
)
from apps.retrieval.rag_join_runtime import (
    extract_join_keys as _extract_join_keys,
)
from apps.retrieval.filters import (
    build_tag_only_filter as _build_tag_only_filter,
    build_collection_join_filter,
    build_perf_filter_by_pjt_id,
    and_filter as _and_filter, build_people_filter,
    build_project_id_filter,
    TITLE_MATCH_MODE_CONTAINS,
    validate_resolved_join_keys,
    JoinFilterInput,
    PeopleFilterInput,
)

try:
    from qdrant_client.http import models as qmodels
except Exception:
    qmodels = None


"""RAG dispatcher가 공유하는 주 runtime 헬퍼 초기화 구간."""


def log_top_points(title: str, points: List[Any], *, topn: int = None, level: str = "info", tier: str = "debug") -> None:
    runtime_log_top_points(
        title,
        list(points or []),
        get_meta=get_meta,
        resolve_collection=_resolve_collection,
        topn=topn,
        level=level,
        tier=tier,
        logger_obj=logger,
    )


def _point_summary(point: Any) -> Dict[str, Any]:
    return runtime_point_summary(point, get_meta=get_meta, resolve_collection=_resolve_collection)


_clip_text = clip_text
_merge_log_fields = merge_log_fields
_resolve_env_topn = resolve_env_topn
_init_timings = init_timings
_timing_put = timing_put
_record_col_timings = record_col_timings
_clean_one_line = clean_one_line

_get_meta = get_meta
_count_missing_join_keys = count_missing_join_keys
_resolve_group_pjt_ids = resolve_group_pjt_ids
_payload_title = payload_title
_serialize_filter_for_log = serialize_filter_for_log

dense_runtime_support = build_dense_runtime_support(
    dense_retrieve_hybrid_multi=dense_retrieve_hybrid_multi,
    strategy_violation_type=StrategyViolation,
    log_kv=log_kv,
)

_PrecomputedEmbedding = dense_runtime_support.precomputed_embedding_type
_call_dense_retrieve_hybrid_multi = dense_runtime_support.call_dense_retrieve_hybrid_multi
_validate_lookup_join_hybrid_metrics = dense_runtime_support.validate_lookup_join_hybrid_metrics
_resolve_sparse_hits_metric = dense_runtime_support.resolve_sparse_hits_metric
_ensure_collection_mark = dense_runtime_support.ensure_collection_mark
_apply_dense_threshold = dense_runtime_support.apply_dense_threshold
_use_dense_score_weight = dense_runtime_support.use_dense_score_weight
_dense_score_weight = dense_runtime_support.dense_score_weight


# -------------------------
# 계획 조립
# -------------------------
def _has_any_ids(it: NormalizedIntent) -> bool:
    """intent 안에 식별자 성격의 단서가 하나라도 있는지 검사한다."""
    ids_map = getattr(it, "ids_map", None)
    if isinstance(ids_map, dict) and any(v for v in ids_map.values() if v):
        return True
    ids_flat = getattr(it, "ids_flat", None)
    if isinstance(ids_flat, list) and len(ids_flat) > 0:
        return True
    if bool(getattr(it, "is_exact_key_query", False)):
        return True
    # 하위 호환 입력도 계속 허용한다.
    legacy = getattr(it, "ids", None)
    if isinstance(legacy, dict) and any(v for v in legacy.values() if v):
        return True
    return False

def _has_explicit_identifiers(it: NormalizedIntent) -> bool:
    """query가 명시적 식별자 조회인지 판단할 단서를 모아 본다."""
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
    if bool(getattr(it, "is_exact_key_query", False)):
        return True
    if bool(getattr(it, "has_project_candidate_key", False) or getattr(it, "has_perf_candidate_key", False)):
        return True
    return False

def _has_relation_join_ids(it: NormalizedIntent) -> bool:
    """JOIN 실행에 바로 쓸 수 있는 프로젝트/성과 seed가 있는지 본다."""
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
    """정책 모듈이 결정한 mode 선택 결과를 그대로 위임 반환한다."""
    return _select_mode_policy_policy(it)


def _build_plan(
        it: NormalizedIntent,
        *,
        preferred_mode: Optional[str] = None,
        preferred_mode_source: Optional[str] = None,
) -> Tuple[QueryPlan, str]:
    """intent와 선호 mode를 바탕으로 실행용 `QueryPlan`을 만든다."""
    return _build_query_plan_policy(
        it,
        preferred_mode=preferred_mode,
        preferred_mode_source=preferred_mode_source,
    )


def _default_target_collections() -> List[str]:
    """planner가 컬렉션을 주지 않았을 때 쓸 기본 조회 대상을 반환한다."""
    return list(_default_target_collections_policy())


def _assert_allowlist_only(*, target_cols: List[str], allow_cols: List[str], source: str) -> None:
    """planner 또는 runtime이 선택한 target_cols가 allowlist를 넘지 않게 강제한다."""
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
    """planner filter와 executor filter의 의미 차이를 계산한다.

    실행 단계가 planner가 선언한 제약을 어떻게 보존했는지 로그로 남기기 위한 diff이며,
    subset 비교가 허용된 경우에는 planner 요구사항이 executor 결과에 포함되는지만 본다.
    """

    list_mode = str(list_match_mode or "exact").strip().lower()
    if list_mode not in {"exact", "subset"}:
        list_mode = "exact"

    def _semantic_subset_equal(planner_val: Any, exec_val: Any) -> bool:
        """planner 값이 executor 값의 의미상 부분집합인지 검사한다."""
        # subset 비교가 허용된 경우 planner는 필수 서브트리만 기술할 수 있다.
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
# 2-hop JOIN 헬퍼
# -------------------------
def _extract_quoted_terms(q: str) -> List[str]:
    """질의에서 따옴표로 감싼 구를 추출해 exact-title 힌트로 쓴다."""
    if not q:
        return []
    out = re.findall(r'"([^"]+)"', q) + re.findall(r"'([^']+)'", q)
    return [t.strip() for t in out if t.strip()]

# -------------------------
# 본 실행 경로
# -------------------------
def _run_rag_with_vectors(
        *,
        query: str,
        model_name: str,
        intent_payload: Any = None,

        request_overrides: Optional[Dict[str, Any]] = None,

        stack: str,
        vector_names: List[str],
        w_dense_map: Dict[str, float],
        lexical_field_weights: Optional[Dict[str, float]] = None,
        sparse_vector_name: Optional[str] = None,
        sparse_topk: Optional[int] = None,
        sparse_weight: Optional[float] = None,
        domain_hint: Optional[str] = None,
) -> RagResult:
    """SEARCH, LOOKUP, JOIN 공통 RAG 실행 경로를 수행한다.

    runtime prelude로 실행 계약을 확정한 뒤, JOIN이면 2-hop orchestration을,
    그 외에는 base orchestration을 수행한다. raw retrieval payload를 그대로 prompt에 넘기지 않고,
    이후 context builder가 `output_type`에 맞는 prompt view를 만들 수 있는 중간 산출물을 조립한다.
    """
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

    # 공통 runtime 객체를 준비한다.
    t0 = time.time()
    resources = build_rag_objects()
    qdr = resources.qdrant_client
    _timing_put(timings, "phase.stack_init", time.time() - t0)
    ctx_hard_limit = _get_ctx_hard_limit(model_name=model_name)

    fallback_emb: Dict[str, Any] = {
        "e5i_qa": resources.embed_e5i,
        "e5_qa": resources.embed_e5,
    }

    final_rerank = build_final_rerank(
        payload_get=_payload_get,
        get_meta=_get_meta,
        payload_title=_payload_title,
        clip_text=_clip_text,
        log_kv=log_kv,
        log_section=log_section,
    )

    effective_allow = list(RAG_COLLECTION_ALLOWLIST or [])
    hinted_cols: List[str] = []
    hinted_limit = 0

    prelude = run_runtime_prelude(
        request=RuntimePreludeRequest(
            query=q,
            model_name=model_name,
            intent_payload=intent_payload,

            request_overrides=dict(request_overrides or {}),

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
        timings=timings,
        diff_filter_spec=_diff_filter_spec,
        has_relation_join_ids=_has_relation_join_ids,
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
    resolved_runtime_join_mode = prelude.resolved_runtime_join_mode
    assembled_question_analysis_join_mode = prelude.assembled_question_analysis_join_mode
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
        """서버 측 강제가 필요한 컬렉션에만 기관 must-gate를 적용한다."""
        return _with_org_must_gate_base(
            base_filter,
            col=col,
            org_role=org_role,
            org_filter=org_filter,
            participant_org_filter=participant_org_filter,
            col_project=COL_PROJECT,
            and_filter=_and_filter,
        )

    def _get_pre_vecs(query_text: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        try:
            vec = resources.embed_e5i.get_query_embedding(query_text)
            if vec is not None:
                out["e5i_qa"] = _PrecomputedEmbedding(list(vec))
        except Exception:
            _timing_put(timings, "event.embed_precompute_error", 1.0)
        try:
            vec = resources.embed_e5.get_query_embedding(query_text)
            if vec is not None:
                out["e5_qa"] = _PrecomputedEmbedding(list(vec))
        except Exception:
            _timing_put(timings, "event.embed_precompute_error", 1.0)
        return out

    pre_vecs = _get_pre_vecs(q)

    def _maybe_followup_perf_hop_from_project() -> List[str]:
        """strict JOIN이 아닐 때는 project 결과에서 perf follow-up seed를 유도한다."""
        target_cols = list(target_collections or _default_target_collections())
        return resolve_perf_followup_join_ids_for_request(
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
            qdr=qdr,
            log_kv=log_kv,
            get_relation_route=get_relation_route,
            build_tag_only_filter=_build_tag_only_filter,
            and_filter=_and_filter,
            named_vectors_in_collection=_named_vectors_in_collection,
            get_pre_vecs=_get_pre_vecs,
            call_dense_retrieve_hybrid_multi=_call_dense_retrieve_hybrid_multi,
            apply_dense_threshold=lambda search_result, **kwargs: _apply_dense_threshold(
                search_result,
                use_dense_threshold=use_dense_threshold_policy,
                min_dense_score=min_dense_score_policy,
                **kwargs,
            ),
            ensure_collection_mark=_ensure_collection_mark,
            rank_source_factory=_RankSource,
            use_dense_score_weight=_use_dense_score_weight,
            dense_score_weight=_dense_score_weight,
            rrf_merge=_rrf_merge,
            dedup_by_doc_id=_dedup_by_doc_id,
            final_rerank=final_rerank,
            hydrate_points=lambda points, chunk_size=128: hydrate_points_payload(
                qdr,
                list(points or []),
                normalize_tag_value=_normalize_tag_value,
                project_tags_norm=PROJECT_TAGS_NORM,
                perf_tags_norm=PERF_TAGS_NORM,
                col_project=COL_PROJECT,
                col_perf=COL_PERF,
                tag_pjt_info=TAG_PJT_INFO,
                tag_pjt_mp=TAG_PJT_MP,
                tag_pjt_org=TAG_PJT_ORG,
                rag_collection_allowlist=RAG_COLLECTION_ALLOWLIST,
                chunk_size=chunk_size,
            ),
            count_missing_join_keys=_count_missing_join_keys,
            debug_force_join_keys_enabled=_debug_force_join_keys_enabled,
            get_meta=_get_meta,
            pick_first=_pick_first,
            extract_join_keys=_extract_join_keys,
        )

    # -------------------------
    # JOIN 모드 2-hop 실행
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
                resolved_runtime_join_mode=resolved_runtime_join_mode,
                planner_raw_join_key_mode=assembled_question_analysis_join_mode,
                join_execution_policy=resolve_join_execution_policy(
                    relation=relation,
                    mode=plan.mode,
                    action=action,
                    join_key_mode=resolved_runtime_join_mode,
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
                reverse_trace_followup=bool(getattr(prelude, "reverse_trace_followup", False)),
                followup_relation_hint=getattr(prelude, "followup_relation_hint", None),
            ),
            runtime=build_join_runtime(
                qdr=qdr,
                preset=preset,
                log_kv=log_kv,
                log_top_points=log_top_points,
                log_section=log_section,
                timing_put=_timing_put,
                point_summary=_point_summary,
                named_vectors_in_collection=_named_vectors_in_collection,
                get_pre_vecs=_get_pre_vecs,
                call_dense_retrieve_hybrid_multi=_call_dense_retrieve_hybrid_multi,
                validate_lookup_join_hybrid_metrics=_validate_lookup_join_hybrid_metrics,
                apply_dense_threshold=lambda search_result, **kwargs: _apply_dense_threshold(
                    search_result,
                    use_dense_threshold=use_dense_threshold_policy,
                    min_dense_score=min_dense_score_policy,
                    **kwargs,
                ),
                ensure_collection_mark=_ensure_collection_mark,
                rank_source_factory=_RankSource,
                use_dense_score_weight=_use_dense_score_weight,
                dense_score_weight=_dense_score_weight,
                rrf_merge=_rrf_merge,
                dedup_by_doc_id=_dedup_by_doc_id,
                final_rerank=final_rerank,
                hydrate_points=lambda points, chunk_size=128: hydrate_points_payload(
                    qdr,
                    list(points or []),
                    normalize_tag_value=_normalize_tag_value,
                    project_tags_norm=PROJECT_TAGS_NORM,
                    perf_tags_norm=PERF_TAGS_NORM,
                    col_project=COL_PROJECT,
                    col_perf=COL_PERF,
                    tag_pjt_info=TAG_PJT_INFO,
                    tag_pjt_mp=TAG_PJT_MP,
                    tag_pjt_org=TAG_PJT_ORG,
                    rag_collection_allowlist=RAG_COLLECTION_ALLOWLIST,
                    chunk_size=chunk_size,
                ),
                count_missing_join_keys=_count_missing_join_keys,
                debug_force_join_keys_enabled=_debug_force_join_keys_enabled,
                get_meta=_get_meta,
                pick_first=_pick_first,
                build_people_filter=build_people_filter,
                people_filter_input_factory=PeopleFilterInput,
                build_project_id_filter=build_project_id_filter,
                build_collection_join_filter=build_collection_join_filter,
                join_filter_input_factory=JoinFilterInput,
                build_tag_only_filter=_build_tag_only_filter,
                and_filter=_and_filter,
                with_org_must_gate=_with_org_must_gate,
                serialize_filter_for_log=_serialize_filter_for_log,
                diff_filter_spec=_diff_filter_spec,
                merge_log_fields=_merge_log_fields,
                extract_join_keys=_extract_join_keys,
                resolve_group_pjt_ids=_resolve_group_pjt_ids,
                validate_resolved_join_keys=validate_resolved_join_keys,
                get_relation_route=get_relation_route,
                context_builder=build_context_with_output_type,
                payload_get=_payload_get,
                series_builder=_build_project_series_payload,
                multi_hop_bundle_builder=_build_multi_hop_bundle_payload,
            ),
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
    # SEARCH / LOOKUP 기본 연합 검색 실행
    # -------------------------
    # 조회할 컬렉션 목록을 확정한다.
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
            title_match_mode_contains=TITLE_MATCH_MODE_CONTAINS,
            title_terms=title_terms,
            lookup_title_filter_policy=lookup_title_filter_policy,
            people_terms=people_terms,
            people_ids=people_ids,
            org_terms=org_terms,
            people_org_terms=people_org_terms,
            org_role=org_role,
            intent_payload=intent_payload,
            perf_followup_join_ids_count=len(perf_followup_join_ids),
        ),
        qdr=qdr,
        filter_inputs=BaseFilterPolicyInputs(
            relation_lookup_enforce=relation_lookup_enforce,
            lookup_filter_enabled=lookup_filter_enabled,
            title_filter_server_applied=title_filter_server_applied,
            planner_org_filter_present=planner_org_filter_present,
            anchor_strict_conf_ok=bool(getattr(prelude, "anchor_strict_conf_ok", False)),
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
# 공개 진입점
# -------------------------
def run_rag_once(
        query: str,
        model_name: str = DEFAULT_MODEL_NAME,
        intent_payload: Any = None,

        request_overrides: Optional[Dict[str, Any]] = None,

) -> RagResult:
    """기본 vector 설정으로 단일 RAG 실행을 수행하는 공개 진입점이다."""
    _validate_intent_payload_version(intent_payload)
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

        request_overrides=request_overrides,

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

        request_overrides: Optional[Dict[str, Any]] = None,

) -> Dict[str, RagResult]:
    """호환용 AB 비교 인터페이스를 유지하되 현재는 단일 결과만 반환한다."""
    res_m = run_rag_once(query=query, model_name=model_name, intent_payload=intent_payload, request_overrides=request_overrides)
    return {"M": res_m}









