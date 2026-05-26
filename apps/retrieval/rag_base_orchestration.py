from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from apps.evidence.rag_result_assembly import apply_structured_result_constraint, finalize_rag_result
from apps.platform.rag_constants import COL_PERF, COL_PROJECT, TAG_PJT_INFO, TAG_PJT_MP, TAG_PJT_ORG
from apps.platform.rag_types import RagResult
from apps.platform.settings import RAG_COLLECTION_ALLOWLIST, logger
from apps.planner.planner_contract import StrategyViolation
from apps.planner.query_intent import get_relation_route
from apps.retrieval.filters import and_filter, build_project_id_filter, build_tag_only_filter
from apps.retrieval.rag_collection_retrieval import retrieve_collections
from apps.retrieval.rag_dense_runtime_support import build_dense_runtime_support
from apps.retrieval.rag_executor_support import get_meta, serialize_filter_for_log
from apps.retrieval.rag_filter_policy import CollectionFilterPolicyContext, resolve_collection_server_filter
from apps.retrieval.rag_hydration_runtime import hydrate_points_payload
from apps.retrieval.rag_rank_runtime import PERF_TAGS_NORM, PROJECT_TAGS_NORM, RankSource, dedup_by_doc_id, normalize_tag_value, resolve_collection, rrf_merge
from apps.retrieval.rag_runtime_observability import log_kv, log_section, log_top_points as log_top_points_runtime, record_col_timings, timing_put
from apps.retrieval.rag_runtime_safety import with_org_must_gate
from apps.retrieval.rag_search_policy import named_vectors_in_collection
from apps.retrieval.retrieval import dense_retrieve_hybrid_multi


@dataclass(frozen=True)
class BaseOrchestrationRequest:
    """JOIN이 아닌 기본 검색 경로 실행에 필요한 입력 묶음."""

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
    title_match_mode_contains: str
    title_terms: List[str]
    lookup_title_filter_policy: str
    people_terms: Optional[List[str]]
    people_ids: Optional[List[str]]
    org_terms: Optional[List[str]]
    people_org_terms: Optional[List[str]]
    org_role: Optional[str]
    intent_payload: Any
    per_col_stats_log_tier: str = "normal"
    perf_followup_join_ids_count: int = 0


@dataclass(frozen=True)
class BaseFilterPolicyInputs:
    """컬렉션별 서버 필터 결정을 위한 입력 묶음."""

    relation_lookup_enforce: bool
    lookup_filter_enabled: bool
    title_filter_server_applied: bool
    planner_org_filter_present: bool
    anchor_strict_conf_ok: bool
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
    """search 모드 전용 서버 필터 적용 판단 입력."""

    search_filter_enabled: bool
    search_filter_signal: Any
    search_filter_conf_ok: bool
    title_filter_server_applied: bool


@dataclass(frozen=True)
class BaseOrchestrationOutcome:
    """기본 검색 결과와 follow-up 사용 여부를 함께 반환한다."""

    result: RagResult
    followup_used: bool
    followup_join_ids_count: int


DENSE_SUPPORT = build_dense_runtime_support(
    dense_retrieve_hybrid_multi=dense_retrieve_hybrid_multi,
    strategy_violation_type=StrategyViolation,
    log_kv=log_kv,
)


def _hydrate_points(points: List[Any], *, qdr: Any, chunk_size: int = 128) -> None:
    hydrate_points_payload(
        qdr,
        list(points or []),
        normalize_tag_value=normalize_tag_value,
        project_tags_norm=PROJECT_TAGS_NORM,
        perf_tags_norm=PERF_TAGS_NORM,
        col_project=COL_PROJECT,
        col_perf=COL_PERF,
        tag_pjt_info=TAG_PJT_INFO,
        tag_pjt_mp=TAG_PJT_MP,
        tag_pjt_org=TAG_PJT_ORG,
        rag_collection_allowlist=RAG_COLLECTION_ALLOWLIST,
        chunk_size=chunk_size,
    )


def _log_top_points(title: str, points: List[Any], *, topn: int = None, level: str = "info", tier: str = "debug") -> None:
    log_top_points_runtime(
        title,
        list(points or []),
        get_meta=get_meta,
        resolve_collection=resolve_collection,
        topn=topn,
        level=level,
        tier=tier,
        logger_obj=logger,
    )


def _should_enable_retrieval_backfill(request: BaseOrchestrationRequest) -> bool:
    output_type = str(request.output_type or "").strip().lower()
    if request.mode != "lookup":
        return False
    if output_type not in {"list", "relation", "comparison", "series", "stats"}:
        return False
    if int(request.hinted_limit or 0) <= 0:
        return False
    return bool(request.people_terms or request.people_ids or request.org_terms or request.people_org_terms)


def _expand_retrieval_candidate_budget(*, topk_dense: int, topk_lex_cand: int, topk_lex: int, sparse_topk_eff: int) -> tuple[int, int, int, int]:
    multiplier = max(2, int(os.getenv("RAG_RETRIEVAL_BACKFILL_MULTIPLIER", os.getenv("RAG_COUNT_BACKFILL_MULTIPLIER", "2"))))
    max_dense = max(16, int(os.getenv("RAG_RETRIEVAL_BACKFILL_MAX_DENSE", os.getenv("RAG_COUNT_BACKFILL_MAX_DENSE", "128"))))
    max_lex_cand = max(240, int(os.getenv("RAG_RETRIEVAL_BACKFILL_MAX_LEX_CAND", os.getenv("RAG_COUNT_BACKFILL_MAX_LEX_CAND", "4800"))))
    max_lex = max(120, int(os.getenv("RAG_RETRIEVAL_BACKFILL_MAX_LEX", os.getenv("RAG_COUNT_BACKFILL_MAX_LEX", "960"))))
    max_sparse = max(180, int(os.getenv("RAG_RETRIEVAL_BACKFILL_MAX_SPARSE", os.getenv("RAG_COUNT_BACKFILL_MAX_SPARSE", "720"))))
    return (
        min(max_dense, max(1, int(topk_dense or 0)) * multiplier),
        min(max_lex_cand, max(1, int(topk_lex_cand or 0)) * multiplier),
        min(max_lex, max(1, int(topk_lex or 0)) * multiplier),
        min(max_sparse, max(1, int(sparse_topk_eff or 0)) * multiplier),
    )


def _preview_structured_result_count(merged_rrf: List[Any], *, qdr: Any, request: BaseOrchestrationRequest) -> int:
    preview_limit = min(
        len(merged_rrf),
        max(
            int(request.hinted_limit or 0) * 4,
            int(os.getenv("RAG_RETRIEVAL_BACKFILL_PREVIEW_MIN", os.getenv("RAG_COUNT_BACKFILL_PREVIEW_MIN", "40"))),
        ),
    )
    if preview_limit <= 0:
        return 0
    preview_points = list(merged_rrf[:preview_limit])
    _hydrate_points(preview_points, qdr=qdr)
    filtered, _ = apply_structured_result_constraint(
        preview_points,
        people_terms=list(request.people_terms or []),
        people_ids=list(request.people_ids or []),
        org_terms=list(request.org_terms or []),
        people_org_terms=list(request.people_org_terms or []),
        org_role=request.org_role,
    )
    return len(filtered)


def execute_base_orchestration(
    *,
    request: BaseOrchestrationRequest,
    qdr: Any,
    filter_inputs: BaseFilterPolicyInputs,
    search_policy: BaseSearchPolicyInputs,
) -> BaseOrchestrationOutcome:
    """JOIN이 아닌 기본 검색 경로의 retrieval, merge, rerank, result assembly를 실행한다."""

    perf_followup_join_ids_count = int(request.perf_followup_join_ids_count or 0)
    log_kv(
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
        anchor_strict_conf_ok=filter_inputs.anchor_strict_conf_ok,
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

    def server_filter_for_col(col: str) -> Any:
        return resolve_collection_server_filter(
            col=col,
            context=filter_policy_context,
            get_relation_route=get_relation_route,
            build_tag_only_filter=build_tag_only_filter,
            and_filter=and_filter,
            build_project_id_filter=build_project_id_filter,
            with_org_must_gate=lambda base_filter: with_org_must_gate(base_filter, col=col),
            log_lookup_ids_empty=lambda **kwargs: log_kv("RAG.LOOKUP.IDS_MAP.EMPTY", build_project_id_filter="skip", tier="debug", **kwargs),
            log_project_key_filter=lambda **kwargs: log_kv("RAG.LOOKUP.PROJECT_KEY_FILTER", tier="debug", **kwargs),
        )

    topk_dense = int(request.topk_dense)
    topk_lex_cand = int(request.topk_lex_cand)
    topk_lex = int(request.topk_lex)
    sparse_topk_eff = int(request.sparse_topk_eff)
    should_backfill = _should_enable_retrieval_backfill(request)
    max_backfill_iters = max(0, int(os.getenv("RAG_RETRIEVAL_BACKFILL_MAX_ITERS", os.getenv("RAG_COUNT_BACKFILL_MAX_ITERS", "2"))))
    backfill_iteration = 0
    current_filtered_count = 0
    exhausted_backfill = False
    sources: List[Any] = []
    merged_rrf: List[Any] = []
    per_col_stats: Dict[str, Dict[str, float]] = {}

    if should_backfill:
        log_kv(
            "RAG.RETRIEVAL_BACKFILL.START",
            tier="debug",
            requested_count=int(request.hinted_limit or 0),
            topk_dense=topk_dense,
            topk_lex_cand=topk_lex_cand,
            topk_lex=topk_lex,
            sparse_topk=sparse_topk_eff,
            people_terms=list(request.people_terms or []),
            people_org_terms=list(request.people_org_terms or []),
            org_role=request.org_role,
        )

    while True:
        t0 = time.time()
        sr_by_col, per_col_stats = retrieve_collections(
            target_cols=request.target_cols,
            qdr=qdr,
            vector_names=request.vector_names,
            pre_vecs=request.pre_vecs,
            fallback_emb=request.fallback_emb,
            named_vectors_in_collection=named_vectors_in_collection,
            logger=logger,
            server_filter_for_col=server_filter_for_col,
            mode=request.mode,
            plan_mode=request.plan_mode,
            q=request.query_text,
            kws=request.keywords,
            preset=request.preset,
            sparse_vector_name_eff=request.sparse_vector_name_eff,
            sparse_topk_eff=sparse_topk_eff,
            sparse_weight_eff=request.sparse_weight_eff,
            topk_dense=topk_dense,
            topk_lex_cand=topk_lex_cand,
            topk_lex=topk_lex,
            call_dense_retrieve_hybrid_multi=DENSE_SUPPORT.call_dense_retrieve_hybrid_multi,
            validate_lookup_join_hybrid_metrics=DENSE_SUPPORT.validate_lookup_join_hybrid_metrics,
            apply_dense_threshold=DENSE_SUPPORT.apply_dense_threshold,
            ensure_collection_mark=DENSE_SUPPORT.ensure_collection_mark,
            log_kv=log_kv,
            log_section=log_section,
            log_top_points=_log_top_points,
            serialize_filter_for_log=serialize_filter_for_log,
            resolve_sparse_hits_metric=DENSE_SUPPORT.resolve_sparse_hits_metric,
            record_col_timings=record_col_timings,
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
        timing_put(request.timings, "phase.dense_search", time.time() - t0)

        sources = []
        for col, sr in sr_by_col.items():
            hybrid_points = sr.get("hybrid") or []
            if hybrid_points:
                sources.append(RankSource(name=f"{col}:hybrid", weight=1.0, points=hybrid_points))
                continue
            for vname, lst in (sr.get("dense") or {}).items():
                base_weight = float(request.w_dense_map.get(vname, 1.0))
                score_weight = DENSE_SUPPORT.dense_score_weight(lst or []) if DENSE_SUPPORT.use_dense_score_weight() else 1.0
                sources.append(RankSource(name=f"{col}:{vname}", weight=base_weight * score_weight, points=lst or []))
            sources.append(RankSource(name=f"{col}:lex", weight=float(request.sparse_weight_eff), points=sr.get("lexical") or []))

        log_section("RAG.RETRIEVE", per_col_stats, tier=request.per_col_stats_log_tier)
        t0 = time.time()
        merged_rrf = rrf_merge(sources, rrf_k=int(os.getenv("RAG_RRF_K", "60")), keep=int(os.getenv("RAG_MERGED_KEEP", "1200")))
        merged_rrf = dedup_by_doc_id(merged_rrf)
        timing_put(request.timings, "phase.rrf_merge", time.time() - t0)
        _log_top_points("RAG.MERGED_RRF.TOP", merged_rrf, topn=int(os.getenv("RAG_LOG_TOPN_MERGED", "10")), tier="debug")

        if not should_backfill:
            break

        current_filtered_count = _preview_structured_result_count(
            merged_rrf,
            qdr=qdr,
            request=request,
        )
        log_kv(
            "RAG.RETRIEVAL_BACKFILL.ITERATION",
            tier="debug",
            iteration=backfill_iteration,
            requested_count=int(request.hinted_limit or 0),
            merged_count=len(merged_rrf or []),
            filtered_count=int(current_filtered_count),
            topk_dense=topk_dense,
            topk_lex_cand=topk_lex_cand,
            topk_lex=topk_lex,
            sparse_topk=sparse_topk_eff,
        )
        if current_filtered_count >= int(request.hinted_limit or 0):
            break
        if backfill_iteration >= max_backfill_iters:
            exhausted_backfill = True
            break
        topk_dense, topk_lex_cand, topk_lex, sparse_topk_eff = _expand_retrieval_candidate_budget(
            topk_dense=topk_dense,
            topk_lex_cand=topk_lex_cand,
            topk_lex=topk_lex,
            sparse_topk_eff=sparse_topk_eff,
        )
        backfill_iteration += 1
        log_kv(
            "RAG.RETRIEVAL_BACKFILL.EXPAND",
            tier="debug",
            iteration=backfill_iteration,
            requested_count=int(request.hinted_limit or 0),
            filtered_count=int(current_filtered_count),
            topk_dense=topk_dense,
            topk_lex_cand=topk_lex_cand,
            topk_lex=topk_lex,
            sparse_topk=sparse_topk_eff,
        )

    if should_backfill:
        log_kv(
            "RAG.RETRIEVAL_BACKFILL.END",
            tier="debug",
            requested_count=int(request.hinted_limit or 0),
            filtered_count=int(current_filtered_count),
            merged_count=len(merged_rrf or []),
            exhausted=int(exhausted_backfill),
            iterations=int(backfill_iteration),
        )

    result = finalize_rag_result(
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
        qdr=qdr,
        intent_payload=request.intent_payload,
        people_terms=request.people_terms,
        people_ids=request.people_ids,
        people_org_terms=request.people_org_terms,
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
        title_match_mode=request.title_match_mode,
        title_match_mode_contains=request.title_match_mode_contains,
        title_terms=request.title_terms,
        lookup_title_filter_policy=request.lookup_title_filter_policy,
    )
    followup_used = request.base_route == "project" and request.mode != "join" and bool(perf_followup_join_ids_count)
    return BaseOrchestrationOutcome(
        result=result,
        followup_used=followup_used,
        followup_join_ids_count=perf_followup_join_ids_count,
    )
