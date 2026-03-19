from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from apps.api.services.rag_result_assembly import finalize_rag_result
from apps.core.rag_filter_policy import CollectionFilterPolicyContext, resolve_collection_server_filter
from apps.core.rag_types import RagResult


@dataclass(frozen=True)
class BaseOrchestrationRequest:
    """비JOIN 기본 검색 실행에 필요한 입력을 모은 request 객체다."""
    mode: str
    plan_mode: str
    base_route: str
    relation: Any
    action: str
    output_type: Optional[str]
    query_text: str
    keywords: List[str]
    query_intent: Any
    target_cols: List[str]
    topk_dense: int
    topk_lex_cand: int
    topk_lex: int
    sparse_vector_name_eff: Optional[str]
    sparse_topk_eff: int
    sparse_weight_eff: float
    vector_names: List[str]
    pre_vecs: Dict[str, Any]
    fallback_emb: Dict[str, Any]
    w_dense_map: Dict[str, float]
    lex_w_eff: float
    hinted_limit: int
    ctx_hard_limit: int
    timings: Dict[str, Any]
    t_all0: float
    stack: str
    rerank_spec: Optional[Dict[str, Any]]
    preset: Any
    title_match_mode: str
    title_terms: List[str]
    lookup_title_filter_policy: str
    people_terms: Optional[List[str]]
    people_ids: Optional[List[str]]
    org_terms: Optional[List[str]]
    org_role: Optional[str]
    intent_payload: Any
    per_col_stats_log_tier: str = "normal"
    perf_followup_join_ids_count: int = 0


@dataclass(frozen=True)
class BaseOrchestrationRuntime:
    """기본 orchestration이 외부에 위임하는 retrieval/rerank/helper 포트 집합이다."""
    qdr: Any
    logger: Any
    retrieve_collections_fn: Callable[..., Any]
    get_relation_route_fn: Callable[[Any], Any]
    build_tag_only_filter_fn: Callable[[List[str]], Any]
    and_filter_fn: Callable[[Any, Any], Any]
    build_project_id_filter_fn: Callable[[List[str], List[str]], Any]
    with_org_must_gate_fn: Callable[..., Any]
    named_vectors_in_collection_fn: Callable[[Any, str], Any]
    call_dense_retrieve_hybrid_multi_fn: Callable[..., Dict[str, Any]]
    validate_lookup_join_hybrid_metrics_fn: Callable[..., None]
    apply_dense_threshold_fn: Callable[..., None]
    ensure_collection_mark_fn: Callable[[List[Any], str], None]
    log_kv_fn: Callable[..., None]
    log_section_fn: Callable[..., None]
    log_top_points_fn: Callable[..., None]
    serialize_filter_for_log_fn: Callable[[Any], Any]
    resolve_sparse_hits_metric_fn: Callable[..., Any]
    record_col_timings_fn: Callable[..., None]
    rank_source_factory: Callable[..., Any]
    use_dense_score_weight_fn: Callable[[], bool]
    dense_score_weight_fn: Callable[[List[Any]], float]
    rrf_merge_fn: Callable[..., List[Any]]
    dedup_by_doc_id_fn: Callable[[List[Any]], List[Any]]
    timing_put_fn: Callable[[Dict[str, Any], str, Any], None]
    finalize_rag_result_fn: Callable[..., RagResult]
    resolve_env_topn_fn: Callable[..., int]
    prepare_title_post_rerank_fn: Callable[..., Dict[str, Any]]
    final_rerank_fn: Callable[..., List[Any]]
    resolve_effective_min_reranked_fn: Callable[..., Any]
    has_explicit_identifiers_fn: Callable[[Any], bool]
    enforce_reranked_contract_fn: Callable[..., Any]
    hydrate_reranked_payloads_fn: Callable[..., None]
    coerce_int_fn: Callable[..., int]
    get_attr_fn: Callable[..., Any]
    hydrate_points_payload_fn: Callable[..., None]
    soft_title_contains_fn: Callable[..., bool]
    aggregation_builder_fn: Callable[..., Optional[Dict[str, Any]]]
    payload_get_fn: Callable[..., Any]
    hit_key_fn: Callable[..., Any]
    title_match_mode_contains: str


@dataclass(frozen=True)
class BaseFilterPolicyInputs:
    """컬렉션 서버 필터 결정을 위해 필요한 입력 묶음이다."""
    relation_lookup_enforce: bool
    lookup_filter_enabled: bool
    title_filter_server_applied: bool
    planner_org_filter_present: bool
    org_role: Optional[str]
    org_terms: List[str]
    people_terms: List[str]
    people_ids: List[str]
    has_perf_ids: bool
    pjt_ids: List[str]
    pjt_nos: List[str]
    people_filter: Any
    participant_org_filter: Any
    org_filter: Any
    title_filter: Any
    project_tag_filter: Any
    perf_tag_filter: Any
    perf_type_filter: Any
    year_range_filter: Any
    perf_followup_filter: Any
    col_project: str
    col_perf: str


@dataclass(frozen=True)
class BaseSearchPolicyInputs:
    """search 모드 전용 필터 적용 정책을 모은 입력 객체다."""
    search_filter_enabled: bool
    search_filter_signal: Any
    search_filter_conf_ok: bool
    title_filter_server_applied: bool


@dataclass(frozen=True)
class BaseOrchestrationOutcome:
    """기본 검색 결과와 perf followup 사용 여부를 함께 반환하는 outcome 객체다."""
    result: RagResult
    followup_used: bool
    followup_join_ids_count: int


def execute_base_orchestration(*, request: BaseOrchestrationRequest, runtime: BaseOrchestrationRuntime, filter_inputs: BaseFilterPolicyInputs, search_policy: BaseSearchPolicyInputs) -> BaseOrchestrationOutcome:
    """JOIN이 아닌 기본 검색 경로의 retrieval, merge, rerank, result assembly를 실행한다.

    컬렉션별 서버 필터를 만들고, hybrid 검색 결과를 합친 뒤 contract를 만족하는 최종 RAG 결과로 조립한다.
    """
    perf_followup_join_ids_count = int(request.perf_followup_join_ids_count or 0)
    runtime.log_kv_fn(
        "RAG.RETRIEVE",
        tier="debug",
        execution_mode=request.mode,
        execution_base_route=request.base_route,
        execution_output_type=request.output_type,
        execution_relation=request.relation,
        planner_mode_hint=request.plan_mode,
        strategy_source="execution_request",
        target_cols=list(request.target_cols or []),
    )

    filter_policy_context = CollectionFilterPolicyContext(
        mode=request.mode,
        base_route=request.base_route,
        relation=request.relation,
        relation_lookup_enforce=filter_inputs.relation_lookup_enforce,
        lookup_filter_enabled=filter_inputs.lookup_filter_enabled,
        title_filter_server_applied=filter_inputs.title_filter_server_applied,
        planner_org_filter_present=filter_inputs.planner_org_filter_present,
        org_role=filter_inputs.org_role,
        org_terms=list(filter_inputs.org_terms or []),
        people_terms=list(filter_inputs.people_terms or []),
        people_ids=list(filter_inputs.people_ids or []),
        has_perf_ids=bool(filter_inputs.has_perf_ids),
        pjt_ids=list(filter_inputs.pjt_ids or []),
        pjt_nos=list(filter_inputs.pjt_nos or []),
        people_filter=filter_inputs.people_filter,
        participant_org_filter=filter_inputs.participant_org_filter,
        org_filter=filter_inputs.org_filter,
        title_filter=filter_inputs.title_filter,
        project_tag_filter=filter_inputs.project_tag_filter,
        perf_tag_filter=filter_inputs.perf_tag_filter,
        perf_type_filter=filter_inputs.perf_type_filter,
        year_range_filter=filter_inputs.year_range_filter,
        perf_followup_filter=filter_inputs.perf_followup_filter,
        col_project=filter_inputs.col_project,
        col_perf=filter_inputs.col_perf,
    )

    def _server_filter_for_col(col: str) -> Any:
        """현재 컬렉션에 적용할 서버 필터를 filter policy에 위임해 계산한다."""
        return resolve_collection_server_filter(
            col=col,
            context=filter_policy_context,
            get_relation_route_fn=runtime.get_relation_route_fn,
            build_tag_only_filter_fn=runtime.build_tag_only_filter_fn,
            and_filter_fn=runtime.and_filter_fn,
            build_project_id_filter_fn=runtime.build_project_id_filter_fn,
            with_org_must_gate_fn=lambda base_filter: runtime.with_org_must_gate_fn(base_filter, col=col),
            log_lookup_ids_empty_fn=lambda **kwargs: runtime.log_kv_fn("RAG.LOOKUP.IDS_MAP.EMPTY", build_project_id_filter="skip", tier="debug", **kwargs),
            log_project_key_filter_fn=lambda **kwargs: runtime.log_kv_fn("RAG.LOOKUP.PROJECT_KEY_FILTER", tier="debug", **kwargs),
        )

    t0 = time.time()
    sr_by_col, per_col_stats = runtime.retrieve_collections_fn(
        target_cols=request.target_cols,
        qdr=runtime.qdr,
        vector_names=request.vector_names,
        pre_vecs=request.pre_vecs,
        fallback_emb=request.fallback_emb,
        named_vectors_in_collection_fn=runtime.named_vectors_in_collection_fn,
        logger=runtime.logger,
        server_filter_for_col_fn=_server_filter_for_col,
        mode=request.mode,
        plan_mode=request.plan_mode,
        q=request.query_text,
        kws=request.keywords,
        preset=request.preset,
        sparse_vector_name_eff=request.sparse_vector_name_eff,
        sparse_topk_eff=request.sparse_topk_eff,
        sparse_weight_eff=request.sparse_weight_eff,
        topk_dense=request.topk_dense,
        topk_lex_cand=request.topk_lex_cand,
        topk_lex=request.topk_lex,
        call_dense_retrieve_hybrid_multi_fn=runtime.call_dense_retrieve_hybrid_multi_fn,
        validate_lookup_join_hybrid_metrics_fn=runtime.validate_lookup_join_hybrid_metrics_fn,
        apply_dense_threshold_fn=runtime.apply_dense_threshold_fn,
        ensure_collection_mark_fn=runtime.ensure_collection_mark_fn,
        log_kv_fn=runtime.log_kv_fn,
        log_section_fn=runtime.log_section_fn,
        log_top_points_fn=runtime.log_top_points_fn,
        serialize_filter_for_log_fn=runtime.serialize_filter_for_log_fn,
        resolve_sparse_hits_metric_fn=runtime.resolve_sparse_hits_metric_fn,
        record_col_timings_fn=runtime.record_col_timings_fn,
        action=request.action,
        base_route=request.base_route,
        relation=request.relation,
        search_filter_enabled=search_policy.search_filter_enabled,
        search_filter_signal=search_policy.search_filter_signal,
        search_filter_conf_ok=search_policy.search_filter_conf_ok,
        title_filter_server_applied=search_policy.title_filter_server_applied,
        lex_w_eff=request.lex_w_eff,
        col_project=filter_inputs.col_project,
        col_perf=filter_inputs.col_perf,
        timings=request.timings,
    )
    runtime.timing_put_fn(request.timings, "phase.dense_search", time.time() - t0)

    sources: List[Any] = []
    for col, sr in sr_by_col.items():
        hybrid_points = sr.get("hybrid") or []
        if hybrid_points:
            sources.append(runtime.rank_source_factory(name=f"{col}:hybrid", weight=1.0, points=hybrid_points))
            continue
        for vname, lst in (sr.get("dense") or {}).items():
            base_weight = float(request.w_dense_map.get(vname, 1.0))
            score_weight = runtime.dense_score_weight_fn(lst or []) if runtime.use_dense_score_weight_fn() else 1.0
            sources.append(runtime.rank_source_factory(name=f"{col}:{vname}", weight=base_weight * score_weight, points=lst or []))
        sources.append(runtime.rank_source_factory(name=f"{col}:lex", weight=float(request.sparse_weight_eff), points=sr.get("lexical") or []))

    runtime.log_section_fn("RAG.RETRIEVE", per_col_stats, tier=request.per_col_stats_log_tier)
    t0 = time.time()
    merged_rrf = runtime.rrf_merge_fn(sources, rrf_k=int(os.getenv("RAG_RRF_K", "60")), keep=int(os.getenv("RAG_MERGED_KEEP", "1200")))
    merged_rrf = runtime.dedup_by_doc_id_fn(merged_rrf)
    runtime.timing_put_fn(request.timings, "phase.rrf_merge", time.time() - t0)
    runtime.log_top_points_fn("RAG.MERGED_RRF.TOP", merged_rrf, topn=int(os.getenv("RAG_LOG_TOPN_MERGED", "10")), tier="debug")

    result = runtime.finalize_rag_result_fn(
        merged_rrf=merged_rrf,
        query_intent=request.query_intent,
        keywords=request.keywords,
        lex_w_eff=request.lex_w_eff,
        base_route=request.base_route,
        mode=request.mode,
        rerank_spec=request.rerank_spec,
        preset=request.preset,
        ctx_hard_limit=request.ctx_hard_limit,
        hinted_limit=request.hinted_limit,
        timings=request.timings,
        qdr=runtime.qdr,
        intent_payload=request.intent_payload,
        people_terms=request.people_terms,
        people_ids=request.people_ids,
        query_text=request.query_text,
        org_terms=request.org_terms,
        org_role=request.org_role,
        sources=sources,
        action=request.action,
        output_type=request.output_type,
        stack=request.stack,
        plan_mode=request.plan_mode,
        relation=request.relation,
        t_all0=request.t_all0,
        logger=runtime.logger,
        log_kv=runtime.log_kv_fn,
        timing_put=lambda key, value: runtime.timing_put_fn(request.timings, key, value),
        hit_key=runtime.hit_key_fn,
        payload_get=runtime.payload_get_fn,
        resolve_env_topn=runtime.resolve_env_topn_fn,
        log_top_points=runtime.log_top_points_fn,
        prepare_title_post_rerank_fn=runtime.prepare_title_post_rerank_fn,
        final_rerank_fn=runtime.final_rerank_fn,
        dedup_by_doc_id_fn=runtime.dedup_by_doc_id_fn,
        resolve_effective_min_reranked_fn=runtime.resolve_effective_min_reranked_fn,
        has_explicit_identifiers=runtime.has_explicit_identifiers_fn,
        enforce_reranked_contract_fn=runtime.enforce_reranked_contract_fn,
        hydrate_reranked_payloads_fn=runtime.hydrate_reranked_payloads_fn,
        coerce_int=runtime.coerce_int_fn,
        get_attr=runtime.get_attr_fn,
        hydrate_points_payload=runtime.hydrate_points_payload_fn,
        soft_title_contains=runtime.soft_title_contains_fn,
        title_match_mode=request.title_match_mode,
        title_match_mode_contains=runtime.title_match_mode_contains,
        title_terms=request.title_terms,
        lookup_title_filter_policy=request.lookup_title_filter_policy,
        aggregation_builder=runtime.aggregation_builder_fn,
    )
    return BaseOrchestrationOutcome(result=result, followup_used=bool(perf_followup_join_ids_count), followup_join_ids_count=perf_followup_join_ids_count)
