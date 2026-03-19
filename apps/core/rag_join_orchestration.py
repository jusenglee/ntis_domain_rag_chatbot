from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from apps.api.services.rag_result_assembly import assemble_join_rag_result
from apps.core.planner_contract import StrategyViolation
from apps.core.rag_constants import COL_PERF, COL_PROJECT, TAG_PJT_INFO
from apps.core.rag_join_runtime import (
    JoinKeyExtractionResult,
    build_join_hop1_filter,
    build_join_hop2_filter,
    build_join_hop_timing_payload,
    ensure_join_keys_in_payload,
    ensure_join_mode_has_keys,
    raise_on_missing_join_keys,
    resolve_join_compile_selection,
    resolve_join_keys_from_hop1,
)
from apps.core.rag_types import RagResult


@dataclass(frozen=True)
class PerfFollowupRequest:
    """project 조회 뒤 perf를 후속 조회할 때 필요한 입력 묶음이다."""
    relation: Optional[Tuple[str, str]]
    ids_map: Dict[str, Any]
    mode: str
    target_cols: List[str]
    query_text: str
    keywords: List[str]
    year_range_filter: Any
    topk_dense: int
    ctx_hard_limit: int
    vector_names: List[str]
    sparse_vector_name_eff: Optional[str]
    sparse_weight_eff: float
    w_dense_map: Dict[str, float]
    fallback_emb: Dict[str, Any]
    action: str
    base_route: str
    intent_item: Any
    lex_w_eff: float


@dataclass(frozen=True)
class PerfFollowupRuntime:
    """perf followup 경로가 호출할 runtime helper 집합이다."""
    qdr: Any
    preset: Any
    log_kv: Callable[..., None]
    get_relation_route_fn: Callable[[Tuple[str, str]], Any]
    build_tag_only_filter_fn: Callable[[List[str]], Any]
    and_filter_fn: Callable[[Any, Any], Any]
    named_vectors_in_collection_fn: Callable[[Any, str], Any]
    get_pre_vecs_fn: Callable[[str], Dict[str, Any]]
    call_dense_retrieve_hybrid_multi_fn: Callable[..., Dict[str, Any]]
    apply_dense_threshold_fn: Callable[..., None]
    ensure_collection_mark_fn: Callable[[List[Any], str], None]
    rank_source_factory: Callable[..., Any]
    use_dense_score_weight_fn: Callable[[], bool]
    dense_score_weight_fn: Callable[[List[Any]], float]
    rrf_merge_fn: Callable[..., List[Any]]
    dedup_by_doc_id_fn: Callable[[List[Any]], List[Any]]
    final_rerank_fn: Callable[..., List[Any]]
    hydrate_points_fn: Callable[..., None]
    count_missing_join_keys_fn: Callable[..., Dict[str, int]]
    debug_force_join_keys_enabled_fn: Callable[[], bool]
    get_meta_fn: Callable[[dict], dict]
    pick_first_fn: Callable[..., str]
    extract_join_keys_fn: Callable[..., Any]


@dataclass(frozen=True)
class JoinOrchestrationRequest:
    """strict JOIN 실행에 필요한 planner truth, filter, retrieval 설정을 묶는다."""
    relation: Tuple[str, str]
    action: str
    mode: str
    base_route: str
    query_text: str
    keywords: List[str]
    output_type: Optional[str]
    planner_limit: int
    resolved_join_key_mode: str
    planner_raw_join_key_mode: Any
    join_execution_policy: Dict[str, Any]
    compiled_strategy: Any
    planner_filter_spec: Dict[str, Any]
    context_state: Any
    people_terms: List[str]
    people_ids: List[str]
    org_terms: List[str]
    org_role: Optional[str]
    people_org_terms: List[str]
    gender_terms: List[str]
    people_min_should: int
    people_match_mode: str
    people_promote_one_must: bool
    lookup_filter_policy: str
    join_hop1_lookup_filter_enabled: bool
    people_filter: Any
    participant_org_filter: Any
    org_filter: Any
    project_tag_filter: Any
    perf_tag_filter: Any
    year_range_filter: Any
    perf_type_filter: Any
    topk_dense: int
    sparse_vector_name_eff: Optional[str]
    sparse_weight_eff: float
    ctx_hard_limit: int
    vector_names: List[str]
    w_dense_map: Dict[str, float]
    fallback_emb: Dict[str, Any]
    lex_w_eff: float
    t_all0: float
    stack: str
    timings: Dict[str, Any]
    intent_item: Any
    target_keep_hop2: int
    reverse_trace_followup: bool = False
    followup_relation_hint: Optional[str] = None

@dataclass(frozen=True)
class JoinOrchestrationRuntime:
    """JOIN orchestration이 외부 helper에 의존하는 지점을 모은 runtime 포트 집합이다."""
    qdr: Any
    preset: Any
    log_kv: Callable[..., None]
    log_top_points: Callable[..., None]
    log_section: Callable[..., None]
    timing_put_fn: Callable[[Dict[str, Any], str, Any], None]
    point_summary_fn: Callable[[Any], Any]
    named_vectors_in_collection_fn: Callable[[Any, str], Any]
    get_pre_vecs_fn: Callable[[str], Dict[str, Any]]
    call_dense_retrieve_hybrid_multi_fn: Callable[..., Dict[str, Any]]
    validate_lookup_join_hybrid_metrics_fn: Callable[..., None]
    apply_dense_threshold_fn: Callable[..., None]
    ensure_collection_mark_fn: Callable[[List[Any], str], None]
    rank_source_factory: Callable[..., Any]
    use_dense_score_weight_fn: Callable[[], bool]
    dense_score_weight_fn: Callable[[List[Any]], float]
    rrf_merge_fn: Callable[..., List[Any]]
    dedup_by_doc_id_fn: Callable[[List[Any]], List[Any]]
    final_rerank_fn: Callable[..., List[Any]]
    hydrate_points_fn: Callable[..., None]
    count_missing_join_keys_fn: Callable[..., Dict[str, int]]
    debug_force_join_keys_enabled_fn: Callable[[], bool]
    get_meta_fn: Callable[[dict], dict]
    pick_first_fn: Callable[..., str]
    build_people_filter_fn: Callable[..., Any]
    people_filter_input_factory: Callable[..., Any]
    build_project_id_filter_fn: Callable[[List[str], List[str]], Any]
    build_collection_join_filter_fn: Callable[..., Any]
    join_filter_input_factory: Callable[..., Any]
    build_tag_only_filter_fn: Callable[[List[str]], Any]
    and_filter_fn: Callable[[Any, Any], Any]
    with_org_must_gate_fn: Callable[..., Any]
    serialize_filter_for_log_fn: Callable[[Any], Any]
    diff_filter_spec_fn: Callable[..., Dict[str, Any]]
    merge_log_fields_fn: Callable[[dict, dict], dict]
    extract_join_keys_fn: Callable[..., Any]
    resolve_group_pjt_ids_fn: Callable[..., List[str]]
    validate_resolved_join_keys_fn: Callable[..., None]
    get_relation_route_fn: Callable[[Tuple[str, str]], Any]
    context_builder: Callable[..., Any]
    payload_get_fn: Optional[Callable[..., Any]] = None
    series_builder_fn: Optional[Callable[..., Optional[Dict[str, Any]]]] = None


@dataclass(frozen=True)
class JoinOrchestrationOutcome:
    """JOIN 실행 결과와 key 해석 메타를 함께 들고 다니는 반환 객체다."""
    result: RagResult
    join_key_source: str
    hop1_mode: str
    join_compile_selection: str
    hop2_key_strategy: str
    resolved_runtime_key_kind: str
    join_keys_used_count: int

def _build_embedding_map(*, collection: str, query_text: str, vector_names: List[str], fallback_emb: Dict[str, Any], named_vectors_in_collection_fn: Callable[[Any, str], Any], get_pre_vecs_fn: Callable[[str], Dict[str, Any]], qdr: Any) -> Dict[str, Any]:
    """컬렉션이 실제로 가진 named vector만 골라 질의용 embedding 맵을 만든다.

    사전 계산 벡터가 없으면 fallback embedding을 써서 dense 경로를 끊지 않는다.
    """
    vec_avail = named_vectors_in_collection_fn(qdr, collection)
    use_vecs = [v for v in vector_names if (not isinstance(vec_avail, set) or v in vec_avail)]
    pre_vecs = get_pre_vecs_fn(query_text)
    emb_map: Dict[str, Any] = {}
    for vname in use_vecs:
        pe = pre_vecs.get(vname)
        emb_map[vname] = pe if pe is not None else fallback_emb.get(vname)
    return {k: v for k, v in emb_map.items() if v is not None}


def _run_ranked_hop(*, collection: str, query_text: str, keywords: List[str], query_filter: Any, k_base: int, dense_topk: int, sparse_vector_name_eff: Optional[str], sparse_weight_eff: float, vector_names: List[str], w_dense_map: Dict[str, float], fallback_emb: Dict[str, Any], qdr: Any, preset: Any, contract_scope: str, rerank_mode: str, rerank_base_route: str, rerank_keep: int, intent_item: Any, lex_w_eff: float, ctx_hard_limit: int, log_prefix: str, action: str, base_route: str, relation: Any, runtime: Any) -> tuple[list[Any], dict[str, float]]:
    """JOIN hop 하나에 대해 hybrid retrieval, merge, rerank를 일괄 수행한다.

    hybrid 결과가 있으면 그것을 우선하고, 없으면 dense/lexical source를 RRF로 합쳐 같은 후처리 파이프라인으로 보낸다.
    """
    emb_map = _build_embedding_map(collection=collection, query_text=query_text, vector_names=vector_names, fallback_emb=fallback_emb, named_vectors_in_collection_fn=runtime.named_vectors_in_collection_fn, get_pre_vecs_fn=runtime.get_pre_vecs_fn, qdr=qdr)
    local_timings: Dict[str, float] = {}
    search_result = runtime.call_dense_retrieve_hybrid_multi_fn(
        qdr=qdr,
        emb_map=emb_map,
        qtext=query_text,
        kws=keywords,
        collection=collection,
        lexical_fields=preset.lexical_fields,
        sparse_vector_name=sparse_vector_name_eff,
        sparse_topk=min(k_base, 120 if contract_scope == "join_hop2" else 80),
        top_k_dense=dense_topk,
        top_k_lex_cand=k_base,
        top_k_lex=min(k_base, 120 if contract_scope == "join_hop2" else 80),
        query_filter=query_filter,
        timings_out=local_timings,
        require_hybrid_both_sides=True,
        contract_scope=contract_scope,
        violation_on_contract=True,
    )
    validate_fn = getattr(runtime, "validate_lookup_join_hybrid_metrics_fn", None)
    if callable(validate_fn):
        validate_fn(mode="join", contract_scope=f"{contract_scope}:{collection}", timings=local_timings, strict=False)
    runtime.apply_dense_threshold_fn(search_result, log_prefix=log_prefix, col=collection, action=action, base_route=base_route, relation=relation)
    hybrid_points = search_result.get("hybrid") or []
    if hybrid_points:
        runtime.ensure_collection_mark_fn(hybrid_points, collection)
        merged = runtime.dedup_by_doc_id_fn(hybrid_points)
    else:
        runtime.ensure_collection_mark_fn((search_result.get("lexical") or []), collection)
        for _, lst in (search_result.get("dense") or {}).items():
            runtime.ensure_collection_mark_fn(lst or [], collection)
        sources: List[Any] = []
        for vname, lst in (search_result.get("dense") or {}).items():
            base_weight = float(w_dense_map.get(vname, 1.0))
            score_weight = runtime.dense_score_weight_fn(lst or []) if runtime.use_dense_score_weight_fn() else 1.0
            sources.append(runtime.rank_source_factory(name=f"{collection}:{vname}", weight=base_weight * score_weight, points=lst or []))
        sources.append(runtime.rank_source_factory(name=f"{collection}:lex", weight=float(sparse_weight_eff), points=search_result.get("lexical") or []))
        merged = runtime.rrf_merge_fn(sources, rrf_k=int(os.getenv("RAG_RRF_K", "60")), keep=800 if contract_scope == "join_hop2" else 500)
        merged = runtime.dedup_by_doc_id_fn(merged)
    reranked = runtime.final_rerank_fn(merged, it=intent_item, kws=keywords, lex_w=lex_w_eff, base_route=rerank_base_route, mode=rerank_mode, keep=rerank_keep, tag_boost=float(getattr(preset, "tag_boost", 0.0)), tag_mismatch_penalty=float(getattr(preset, "tag_mismatch_penalty", 0.0)))
    reranked = runtime.dedup_by_doc_id_fn(reranked)
    if len(reranked) > ctx_hard_limit:
        reranked = reranked[:ctx_hard_limit]
    return reranked, local_timings


def _reverse_trace_point_payload(point: Any) -> Dict[str, Any]:
    """Return the point payload when available so reverse-trace summaries stay payload-driven."""
    payload = getattr(point, "payload", None)
    return payload if isinstance(payload, dict) else {}


def _summarize_reverse_trace_perf(point: Any, *, payload_get_fn: Optional[Callable[..., Any]]) -> Dict[str, Any]:
    """Build a compact perf summary for reverse-trace evidence."""
    payload = _reverse_trace_point_payload(point)
    payload_get = payload_get_fn or (lambda data, key: data.get(key) if isinstance(data, dict) else None)
    return {
        "doc_id": payload_get(payload, "doc_id") or payload_get(payload, "id"),
        "perf_title": payload_get(payload, "meta_basic.title") or payload_get(payload, "title") or payload_get(payload, "title_text"),
        "perf_type": payload_get(payload, "perf_type") or payload_get(payload, "meta_basic.perf_type") or payload_get(payload, "tag"),
        "published_year": payload_get(payload, "published_year") or payload_get(payload, "meta_basic.pblcn_ymd") or payload_get(payload, "meta_detail.pblcn_ymd"),
        "pjt_id": payload_get(payload, "pjt_id") or payload_get(payload, "meta_basic.pjt_id") or payload_get(payload, "meta_detail.pjt_id"),
        "pjt_no": payload_get(payload, "pjt_no") or payload_get(payload, "meta_basic.pjt_no") or payload_get(payload, "meta_detail.pjt_no"),
    }


def _summarize_reverse_trace_project(point: Any, *, payload_get_fn: Optional[Callable[..., Any]]) -> Dict[str, Any]:
    """Build a compact project summary for reverse-trace evidence."""
    payload = _reverse_trace_point_payload(point)
    payload_get = payload_get_fn or (lambda data, key: data.get(key) if isinstance(data, dict) else None)
    return {
        "pjt_id": payload_get(payload, "pjt_id") or payload_get(payload, "meta_basic.pjt_id") or payload_get(payload, "meta_detail.pjt_id"),
        "pjt_no": payload_get(payload, "pjt_no") or payload_get(payload, "meta_basic.pjt_no") or payload_get(payload, "meta_detail.pjt_no"),
        "project_title": payload_get(payload, "meta_basic.kor_pjt_nm") or payload_get(payload, "title") or payload_get(payload, "title_text"),
    }


def _dedupe_reverse_trace_followup(origin_perf_points: List[Any], followup_perf_points: List[Any], *, payload_get_fn: Optional[Callable[..., Any]]) -> List[Any]:
    """Drop hop3 perf hits that describe the same seed perf evidence already returned by hop1."""
    origin_keys: set[tuple[str, str]] = set()
    for point in origin_perf_points:
        summary = _summarize_reverse_trace_perf(point, payload_get_fn=payload_get_fn)
        origin_keys.add((str(summary.get("doc_id") or "").strip().lower(), str(summary.get("perf_title") or "").strip().lower()))

    deduped: List[Any] = []
    seen_keys: set[tuple[str, str]] = set()
    for point in followup_perf_points:
        summary = _summarize_reverse_trace_perf(point, payload_get_fn=payload_get_fn)
        key = (str(summary.get("doc_id") or "").strip().lower(), str(summary.get("perf_title") or "").strip().lower())
        if key in origin_keys or key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(point)
    return deduped


def _build_reverse_trace_payload(*, origin_perf_points: List[Any], origin_project_points: List[Any], followup_perf_points: List[Any], followup_relation_hint: Optional[str], payload_get_fn: Optional[Callable[..., Any]]) -> Dict[str, Any]:
    """Assemble the perf -> project -> perf traversal payload exposed to renderers and debug views."""
    origin_perf = [_summarize_reverse_trace_perf(point, payload_get_fn=payload_get_fn) for point in origin_perf_points]
    origin_projects = [_summarize_reverse_trace_project(point, payload_get_fn=payload_get_fn) for point in origin_project_points]
    followup_perf = [_summarize_reverse_trace_perf(point, payload_get_fn=payload_get_fn) for point in followup_perf_points]
    status = "ok" if followup_perf else "partial"
    return {
        "status": status,
        "relation_chain": ["perf", "project", "perf"],
        "followup_relation_hint": followup_relation_hint,
        "origin_perf": origin_perf,
        "origin_projects": origin_projects,
        "followup_perf": followup_perf,
    }


def resolve_perf_followup_join_ids(*, request: PerfFollowupRequest, runtime: PerfFollowupRuntime) -> List[str]:
    """project 검색 결과에서 perf followup에 쓸 instance project id를 추출한다.

    strict JOIN seed가 없는 비JOIN 질의에서만 동작하며, hop1 결과에 key 이상 징후가 있으면 즉시 fail-close 한다.
    """
    if request.relation != ("project", "perf"):
        return []
    if isinstance(request.ids_map, dict) and any(v for v in request.ids_map.values() if v):
        return []
    if request.mode == "join":
        return []
    if COL_PROJECT not in request.target_cols or COL_PERF not in request.target_cols:
        runtime.log_kv("RAG.PERF.FOLLOWUP.SKIP", reason="target_collections", target_cols=request.target_cols, tier="debug")
        return []
    route = runtime.get_relation_route_fn(request.relation)
    hop1_tag_filters = route.hop1_tag_filters if route else None
    hop1_filter = runtime.build_tag_only_filter_fn(hop1_tag_filters) if hop1_tag_filters else None
    if request.year_range_filter:
        hop1_filter = runtime.and_filter_fn(hop1_filter, request.year_range_filter)
    hop1_keep = int(os.getenv("RAG_HOP1_KEEP", "5"))
    hop1_k_base = int(os.getenv("RAG_HOP1_TOPK_BASE", "250"))
    runtime.log_kv("RAG.PERF.FOLLOWUP.HOP1", hop1_col=COL_PROJECT, hop1_q=request.query_text, hop1_tag_filters=hop1_tag_filters, hop1_filter=str(hop1_filter) if hop1_filter is not None else None, hop1_k_base=hop1_k_base, hop1_keep=hop1_keep, tier="debug")
    hop1_reranked, local_timings = _run_ranked_hop(collection=COL_PROJECT, query_text=request.query_text, keywords=request.keywords, query_filter=hop1_filter, k_base=hop1_k_base, dense_topk=request.topk_dense, sparse_vector_name_eff=request.sparse_vector_name_eff, sparse_weight_eff=request.sparse_weight_eff, vector_names=request.vector_names, w_dense_map=request.w_dense_map, fallback_emb=request.fallback_emb, qdr=runtime.qdr, preset=runtime.preset, contract_scope="join_hop1_followup", rerank_mode="search", rerank_base_route="project", rerank_keep=int(os.getenv("RAG_HOP1_FINAL_KEEP", "40")), intent_item=request.intent_item, lex_w_eff=request.lex_w_eff, ctx_hard_limit=request.ctx_hard_limit, log_prefix="RAG.DENSE.THRESHOLD.FOLLOWUP", action=request.action, base_route=request.base_route, relation=request.relation, runtime=runtime)
    hop1_top = hop1_reranked[: max(1, hop1_keep)]
    if hop1_top:
        hydrate_keep = min(max(hop1_keep, int(os.getenv("RAG_HOP1_FINAL_KEEP", "40")), 20), request.ctx_hard_limit)
        runtime.hydrate_points_fn(hop1_reranked[:hydrate_keep])
        missing = runtime.count_missing_join_keys_fn(hop1_top, join_key_mode="instance")
        if (missing.get("missing_pjt_any") or missing.get("missing_tag") or missing.get("invalid_pjt_id") or missing.get("invalid_pjt_no") or missing.get("suspected_swap") or missing.get("same_id_no")):
            if str(os.getenv("RAG_HOP1_REHYDRATE_ON_MISSING_KEYS", "1")).strip().lower() in ("1", "true", "yes", "y"):
                runtime.hydrate_points_fn(hop1_top)
            raise_on_missing_join_keys(hop1_top, scope="join_hop1_followup", join_key_mode="instance", count_missing_join_keys=runtime.count_missing_join_keys_fn, log_kv=runtime.log_kv, debug_force_join_keys_enabled=runtime.debug_force_join_keys_enabled_fn, ensure_join_keys_in_payload_fn=lambda pts: ensure_join_keys_in_payload(pts, get_meta=runtime.get_meta_fn, pick_first=runtime.pick_first_fn))
    join_key_result = runtime.extract_join_keys_fn(hop1_top, mode="instance", max_ids=50)
    join_keys = list(dict.fromkeys([str(x).strip() for x in join_key_result.keys if str(x).strip()]))
    runtime.log_kv("RAG.PERF.FOLLOWUP.JOIN_IDS", join_ids_preview=join_keys[:10], join_ids_count=len(join_keys), timings={k: float(v) for k, v in (local_timings or {}).items()}, tier="debug")
    return join_keys


def execute_join_orchestration(*, request: JoinOrchestrationRequest, runtime: JoinOrchestrationRuntime) -> JoinOrchestrationOutcome:
    """planner가 확정한 relation과 join_key_mode에 따라 2-hop JOIN 검색을 실행한다.

    hop1 key 획득, hop2 filter 조립, 결과 assembly까지 한 함수에서 이어 주되, planner와 어긋나는 filter나 key 해석은 StrategyViolation으로 막는다.
    """
    t_hop0 = time.time()
    ids_map = getattr(request.intent_item, "ids_map", None) or getattr(request.intent_item, "ids", None) or {}
    join_key_source = "hop1"
    join_key_mode = request.resolved_join_key_mode
    pjt_ids = [str(x).strip() for x in list(ids_map.get("pjt_id") or []) if str(x).strip()]
    pjt_nos = [str(x).strip() for x in list(ids_map.get("pjt_no") or []) if str(x).strip()]
    seed_join_pjt_ids = list(dict.fromkeys(pjt_ids))
    seed_join_pjt_nos = list(dict.fromkeys(pjt_nos))
    seed_join_ids = seed_join_pjt_ids if join_key_mode == "instance" else seed_join_pjt_nos
    if seed_join_ids:
        join_key_source = "ids_map"

    hop1_strategy = str(request.join_execution_policy.get("hop1_strategy") or "search")
    runtime.log_kv("RAG.JOIN.POLICY", relation=request.relation, action=request.action, join_key_mode=join_key_mode, policy_source=request.join_execution_policy.get("policy_source") or "execution_policy", hop1_strategy=hop1_strategy, reason=request.join_execution_policy.get("reason"), execution_policy_reason=request.join_execution_policy.get("execution_policy_reason") or request.join_execution_policy.get("reason"), seed_key_source=request.join_execution_policy.get("seed_key_source"), seed_key_count=int(request.join_execution_policy.get("seed_key_count") or 0), group_resolve_project_ids=int(request.join_execution_policy.get("group_resolve_project_ids") or 0), group_resolve_max_ids=int(request.join_execution_policy.get("group_resolve_max_ids") or 0), group_resolve_topk=int(request.join_execution_policy.get("group_resolve_topk") or 0), tier="debug")

    route = runtime.get_relation_route_fn(request.relation)
    if route is None:
        raise StrategyViolation(error_code="PLANNER_JOIN_RELATION_UNRESOLVED", reason=f"JOIN relation unresolved (relation={request.relation})")
    hop1_col, hop2_col = route.hop1_col, route.hop2_col
    hop1_kind, hop2_kind = route.hop1_kind, route.hop2_kind
    hop1_tag_filters, hop2_tag_filters = route.hop1_tag_filters, route.hop2_tag_filters
    hop2_label = route.hop2_label

    hop1_keep_env = int(os.getenv("RAG_HOP1_KEEP", "5"))
    planner_limit = int(request.planner_limit or 0)
    hop1_keep = max(1, min(hop1_keep_env, planner_limit)) if planner_limit > 0 else hop1_keep_env
    hop2_keep = int(os.getenv("RAG_HOP2_KEEP", str(max(1, int(request.target_keep_hop2 or 10)))))
    hop1_k_base = int(os.getenv("RAG_HOP1_TOPK_BASE", "250"))
    hop2_k_base = int(os.getenv("RAG_HOP2_TOPK_BASE", "300"))
    if join_key_mode == "group" and int(request.join_execution_policy.get("group_resolve_project_ids") or 0):
        hop1_k_base = max(hop1_k_base, int(request.join_execution_policy.get("group_resolve_topk") or 1))

    join_pjt_ids: List[str] = []
    join_pjt_nos: List[str] = []
    hop1_top: List[Any] = []
    if hop1_strategy == "skip" and seed_join_ids:
        join_key_source = "ids_map"

    has_seed_join_keys = bool(seed_join_pjt_nos) if join_key_mode == "group" else bool(seed_join_pjt_ids)
    runtime.log_kv("RAG.PLAN.JOIN_EXECUTED", relation=request.relation, action=request.action, join_key_mode=join_key_mode, policy_source=request.join_execution_policy.get("policy_source") or "execution_policy", has_seed_join_keys=int(has_seed_join_keys), hop1_strategy=hop1_strategy, execution_policy_reason=request.join_execution_policy.get("execution_policy_reason") or request.join_execution_policy.get("reason"), seed_key_source=request.join_execution_policy.get("seed_key_source"), seed_key_count=int(request.join_execution_policy.get("seed_key_count") or 0), tier="debug")
    if join_key_mode == "group" and seed_join_pjt_nos:
        join_pjt_nos = seed_join_pjt_nos[:]
        join_key_source = "ids_map"
    elif join_key_mode == "instance" and seed_join_pjt_ids:
        join_pjt_ids = seed_join_pjt_ids[:]
        join_key_source = "ids_map"

    local_timings_h1: Dict[str, float] = {}
    allow_skip_min_lookup = str(os.getenv("RAG_JOIN_HOP1_SKIP_MIN_LOOKUP", "0")).strip().lower() in ("1", "true", "yes", "y")
    run_hop1 = hop1_strategy in ("lookup", "search") or (hop1_strategy == "skip" and join_key_mode == "instance" and bool(seed_join_pjt_ids) and allow_skip_min_lookup)
    hop1_filter = runtime.build_tag_only_filter_fn(hop1_tag_filters) if hop1_tag_filters else None
    if request.people_filter:
        hop1_filter = runtime.and_filter_fn(hop1_filter, request.people_filter)
    if request.org_filter:
        hop1_filter = runtime.and_filter_fn(hop1_filter, request.org_filter)
    hop1_filter = runtime.with_org_must_gate_fn(hop1_filter, col=hop1_col, mode_override="join")
    if request.join_hop1_lookup_filter_enabled and hop1_col in (COL_PROJECT, COL_PERF):
        join_people_filter = request.people_filter
        join_promote_one_must = bool(request.people_promote_one_must or (request.join_hop1_lookup_filter_enabled and request.lookup_filter_policy == "must_one_then_should" and not request.people_ids and len(request.people_terms) == 1))
        final_people_terms = list(getattr(request.context_state, "people_terms", None) or request.people_terms or [])
        if join_people_filter is None and final_people_terms:
            join_people_filter = runtime.build_people_filter_fn(runtime.people_filter_input_factory(people_terms=final_people_terms, person_ids=request.people_ids, gender_terms=request.gender_terms, org_terms=request.people_org_terms, min_should=request.people_min_should, promote_one_must=join_promote_one_must))
            if join_people_filter is None:
                runtime.log_kv("RAG.JOIN.HOP1.LOOKUP_FILTER.PEOPLE_MISSING", level="warning", reason="people_filter_unavailable", people_terms=final_people_terms[:4], people_ids=request.people_ids[:4], match_mode=request.people_match_mode, min_should=request.people_min_should, tier="debug")
        hop1_lookup_filter = None
        if hop1_col == COL_PROJECT:
            if join_people_filter or request.participant_org_filter or request.org_filter:
                hop1_lookup_filter = runtime.and_filter_fn(hop1_lookup_filter, runtime.build_tag_only_filter_fn([TAG_PJT_INFO]))
            if join_people_filter:
                hop1_lookup_filter = runtime.and_filter_fn(hop1_lookup_filter, join_people_filter)
            if request.participant_org_filter or request.org_filter:
                hop1_lookup_filter = runtime.and_filter_fn(hop1_lookup_filter, request.participant_org_filter or request.org_filter)
            if request.project_tag_filter:
                hop1_lookup_filter = runtime.and_filter_fn(hop1_lookup_filter, request.project_tag_filter)
        elif hop1_col == COL_PERF:
            if request.base_route == "perf" and join_people_filter:
                hop1_lookup_filter = runtime.and_filter_fn(hop1_lookup_filter, join_people_filter)
            if request.perf_tag_filter:
                hop1_lookup_filter = runtime.and_filter_fn(hop1_lookup_filter, request.perf_tag_filter)
        if hop1_lookup_filter is not None:
            hop1_filter = runtime.and_filter_fn(hop1_filter, hop1_lookup_filter)
    if hop1_col in (COL_PROJECT, COL_PERF) and request.year_range_filter:
        hop1_filter = runtime.and_filter_fn(hop1_filter, request.year_range_filter)
    if hop1_col == COL_PERF and request.perf_type_filter:
        hop1_filter = runtime.and_filter_fn(hop1_filter, request.perf_type_filter)

    hop1_filter, executed_hop1_filter_spec = build_join_hop1_filter(relation=request.relation, hop1_col=hop1_col, hop1_filter=hop1_filter, compiled_hop1_spec=request.compiled_strategy.hop1_spec, and_filter=runtime.and_filter_fn)
    planner_join_hop1_filter_spec = dict((request.planner_filter_spec or {}).get("join_hop1_filter") or {})
    join_hop1_filter_diff = runtime.diff_filter_spec_fn(planner_filter_spec=planner_join_hop1_filter_spec, executed_filter_spec=executed_hop1_filter_spec)
    join_hop1_filter_diff_changed = join_hop1_filter_diff.get("changed", {})
    runtime.log_kv("RAG.JOIN.HOP1.FILTER_SPEC.DIFF", level="error" if join_hop1_filter_diff_changed else "info", planner_filter_keys=join_hop1_filter_diff.get("planner_keys", []), changed=join_hop1_filter_diff_changed, changed_count=len(join_hop1_filter_diff_changed), planner_join_hop1_filter_spec=planner_join_hop1_filter_spec, executed_join_hop1_filter_spec=executed_hop1_filter_spec, tier="debug")
    if join_hop1_filter_diff_changed:
        raise StrategyViolation(error_code="STRATEGY_MISMATCH", reason=f"join Hop1 filter_spec mismatch between planner and executed (changed_keys={list(join_hop1_filter_diff_changed.keys())})")

    if hop1_strategy == "lookup" and join_key_mode == "group" and seed_join_pjt_nos:
        hop1_filter = runtime.and_filter_fn(hop1_filter, runtime.build_project_id_filter_fn([], seed_join_pjt_nos))
    if hop1_strategy == "skip" and join_key_mode == "instance" and seed_join_pjt_ids:
        hop1_filter = runtime.and_filter_fn(hop1_filter, runtime.build_project_id_filter_fn(seed_join_pjt_ids, []))
        hop1_k_base = max(10, min(hop1_k_base, 30))
        hop1_keep = max(1, min(hop1_keep, 1))

    runtime.log_kv("RAG.JOIN.HOP1", hop1_col=hop1_col, hop1_kind=hop1_kind, hop1_q=request.query_text, hop1_tag_filters=hop1_tag_filters, hop1_filter=str(hop1_filter) if hop1_filter is not None else None, hop1_k_base=hop1_k_base, hop1_keep=hop1_keep, hop1_execute=int(run_hop1), policy_source=request.join_execution_policy.get("policy_source") or "execution_policy", reason=request.join_execution_policy.get("reason"), execution_policy_reason=request.join_execution_policy.get("execution_policy_reason") or request.join_execution_policy.get("reason"), seed_key_source=request.join_execution_policy.get("seed_key_source"), seed_key_count=int(request.join_execution_policy.get("seed_key_count") or 0), tier="debug")

    if run_hop1:
        hop1_reranked, local_timings_h1 = _run_ranked_hop(collection=hop1_col, query_text=request.query_text, keywords=request.keywords, query_filter=hop1_filter, k_base=hop1_k_base, dense_topk=request.topk_dense, sparse_vector_name_eff=request.sparse_vector_name_eff, sparse_weight_eff=request.sparse_weight_eff, vector_names=request.vector_names, w_dense_map=request.w_dense_map, fallback_emb=request.fallback_emb, qdr=runtime.qdr, preset=runtime.preset, contract_scope="join_hop1", rerank_mode="search", rerank_base_route=("perf" if hop1_kind == "perf" else request.base_route), rerank_keep=int(os.getenv("RAG_HOP1_FINAL_KEEP", "40")), intent_item=request.intent_item, lex_w_eff=request.lex_w_eff, ctx_hard_limit=request.ctx_hard_limit, log_prefix="RAG.DENSE.THRESHOLD.HOP1", action=request.action, base_route=request.base_route, relation=request.relation, runtime=runtime)
        hop1_top = hop1_reranked[: max(1, hop1_keep)]
        if not hop1_top:
            runtime.log_kv("RAG.JOIN.HOP1.EMPTY", hop1_kind=hop1_kind, hop1_tag_filters=hop1_tag_filters, hop1_filter=str(hop1_filter) if hop1_filter is not None else None, tier="debug")
        else:
            hydrate_keep = min(max(hop1_keep, int(os.getenv("RAG_HOP1_FINAL_KEEP", "40")), 20), request.ctx_hard_limit)
            runtime.hydrate_points_fn(hop1_reranked[:hydrate_keep])
            missing = runtime.count_missing_join_keys_fn(hop1_top, join_key_mode=join_key_mode)
            if (missing.get("missing_pjt_any") or missing.get("missing_tag") or missing.get("invalid_pjt_id") or missing.get("invalid_pjt_no") or missing.get("suspected_swap") or missing.get("same_id_no")):
                if str(os.getenv("RAG_HOP1_REHYDRATE_ON_MISSING_KEYS", "1")).strip().lower() in ("1", "true", "yes", "y"):
                    runtime.hydrate_points_fn(hop1_top)
                raise_on_missing_join_keys(hop1_top, scope=f"join_hop1:{hop1_col}", join_key_mode=join_key_mode, count_missing_join_keys=runtime.count_missing_join_keys_fn, log_kv=runtime.log_kv, debug_force_join_keys_enabled=runtime.debug_force_join_keys_enabled_fn, ensure_join_keys_in_payload_fn=lambda pts: ensure_join_keys_in_payload(pts, get_meta=runtime.get_meta_fn, pick_first=runtime.pick_first_fn))
        join_resolution = resolve_join_keys_from_hop1(hop1_top=hop1_top, hop1_reranked=hop1_reranked, hop1_keep=hop1_keep, join_key_mode=join_key_mode, seed_join_pjt_nos=seed_join_pjt_nos, join_execution_policy=request.join_execution_policy, relation=request.relation, hop1_col=hop1_col, hop1_k_base=hop1_k_base, log_kv=runtime.log_kv, extract_join_keys=runtime.extract_join_keys_fn, resolve_group_pjt_ids=runtime.resolve_group_pjt_ids_fn)
        join_key_result = join_resolution.join_key_result
        join_pjt_ids = list(join_resolution.join_pjt_ids)
        join_pjt_nos = list(join_resolution.join_pjt_nos)
        if hop1_top:
            join_key_source = join_resolution.join_key_source or "hop1"
    else:
        join_key_result = JoinKeyExtractionResult(keys=[], invalid_values=[], suspected_swaps=[])
        runtime.log_kv("RAG.JOIN.HOP1.SKIPPED", relation=request.relation, hop1_col=hop1_col, hop1_strategy=hop1_strategy, policy_source=request.join_execution_policy.get("policy_source") or "execution_policy", reason=request.join_execution_policy.get("reason"), execution_policy_reason=request.join_execution_policy.get("execution_policy_reason") or request.join_execution_policy.get("reason"), seed_key_source=request.join_execution_policy.get("seed_key_source"), seed_key_count=int(request.join_execution_policy.get("seed_key_count") or 0), allow_skip_min_lookup=int(allow_skip_min_lookup), tier="debug")

    invalid_values = [str(x).strip() for x in (join_key_result.invalid_values or []) if str(x).strip()]
    suspected_swap_count = int(join_key_result.suspected_swap_count or 0)
    if invalid_values or suspected_swap_count > 0:
        invalid_samples = [runtime.point_summary_fn(p) for p in hop1_top[:3]] if hop1_top else []
        runtime.log_kv("RAG.JOIN_KEYS.INVALID", level="error", scope=f"join_hop1:{hop1_col}:extract", join_key_mode=join_key_mode, invalid_count=len(invalid_values), invalid_values=invalid_values[:10], suspected_swap_count=suspected_swap_count, suspected_swaps=join_key_result.to_log_dict().get("suspected_swaps", [])[:10], tier="debug")
        runtime.log_kv("RAG.ERROR.JOIN_KEYS", level="error", scope=f"join_hop1:{hop1_col}:extract", samples=invalid_samples, tier="normal")
        raise StrategyViolation(error_code="JOIN_KEYS_INVALID", reason=f"[join_hop1:{hop1_col}:extract] invalid join keys detected (join_key_mode={join_key_mode}, invalid_count={len(invalid_values)}, suspected_swap_count={suspected_swap_count})")

    runtime.log_top_points("RAG.JOIN.HOP1.TOP", hop1_top, topn=int(os.getenv("RAG_LOG_TOPN_HOP1", "6")), tier="debug")
    if join_key_mode == "group":
        runtime.log_section("RAG.JOIN.JOIN_PJT_NOS", join_pjt_nos[: min(len(join_pjt_nos), 30)], tier="debug")
    else:
        runtime.log_section("RAG.JOIN.JOIN_PJT_IDS", join_pjt_ids[: min(len(join_pjt_ids), 30)], tier="debug")
    hop1_payload = build_join_hop_timing_payload(merge_log_fields=runtime.merge_log_fields_fn, local_timings=local_timings_h1)
    runtime.log_kv("RAG.JOIN.HOP1.TIMINGS", tier="debug", **hop1_payload)

    has_join_keys = len(join_pjt_ids) > 0 or len(join_pjt_nos) > 0
    ensure_join_mode_has_keys(has_join_keys=has_join_keys, join_key_mode=join_key_mode, hop1_top=hop1_top, hop1_col=hop1_col, join_pjt_ids_count=len(join_pjt_ids), join_pjt_nos_count=len(join_pjt_nos), log_kv=runtime.log_kv, raise_on_missing_join_keys_fn=lambda points, **kwargs: raise_on_missing_join_keys(points, count_missing_join_keys=runtime.count_missing_join_keys_fn, log_kv=runtime.log_kv, debug_force_join_keys_enabled=runtime.debug_force_join_keys_enabled_fn, ensure_join_keys_in_payload_fn=lambda pts: ensure_join_keys_in_payload(pts, get_meta=runtime.get_meta_fn, pick_first=runtime.pick_first_fn), **kwargs))
    if join_key_mode == "group" and int(request.join_execution_policy.get("group_resolve_project_ids") or 0) and len(join_pjt_ids) == 0:
        raise StrategyViolation(error_code="JOIN_GROUP_KEYS_UNRESOLVED", reason=f"group JOIN Hop1 resolve failed: resolved_pjt_ids is empty (relation={request.relation}, hop1_col={hop1_col}, seed_pjt_nos={len(seed_join_pjt_nos)})")

    planner_join_key_mode = request.resolved_join_key_mode
    join_compile_selection = resolve_join_compile_selection(
        hop2_col=hop2_col,
        join_key_mode=planner_join_key_mode,
        join_pjt_ids=join_pjt_ids,
        join_pjt_nos=join_pjt_nos,
    )
    if join_compile_selection == "group_join_keys_missing":
        raise StrategyViolation(error_code="JOIN_GROUP_KEYS_UNRESOLVED", reason=f"group JOIN Hop2(perf) compile failed: neither pjt_no nor fallback pjt_id is available (relation={request.relation}, hop2_col={hop2_col}, join_key_mode={planner_join_key_mode})")
    runtime.log_kv("RAG.JOIN.KEY_MODE.CHECK", resolved_join_key_mode=request.resolved_join_key_mode, planner_raw_join_key_mode=request.planner_raw_join_key_mode, planner_join_key_mode=planner_join_key_mode, join_compile_selection=join_compile_selection, join_pjt_ids_count=len(join_pjt_ids), resolved_pjt_ids_count=len(join_pjt_ids), join_pjt_nos_count=len(join_pjt_nos), opposite_key_count=(len(join_pjt_ids) if join_key_mode == "group" else len(join_pjt_nos)), relation=request.relation, hop2_col=hop2_col, tier="debug")
    try:
        runtime.validate_resolved_join_keys_fn(mode=planner_join_key_mode, pjt_ids=join_pjt_ids, pjt_nos=join_pjt_nos)
    except ValueError as exc:
        msg = str(exc)
        error_code, _, reason = msg.partition(": ")
        raise StrategyViolation(error_code=error_code if error_code.startswith("EXECUTOR_") else "EXECUTOR_JOIN_KEYS_INVALID", reason=reason or msg) from exc

    hop2_join_key_mode = planner_join_key_mode
    hop2_filter, executed_join_filter_spec = build_join_hop2_filter(relation=request.relation, hop2_col=hop2_col, join_key_mode=hop2_join_key_mode, join_pjt_ids=join_pjt_ids, join_pjt_nos=join_pjt_nos, join_ids=(join_pjt_ids if hop2_join_key_mode == "instance" else []), q=request.query_text, hop2_tag_filters=hop2_tag_filters, people_terms=request.people_terms, org_terms=request.org_terms, planner_filter_spec=request.planner_filter_spec, compiled_hop2_spec=request.compiled_strategy.hop2_spec, build_collection_join_filter=runtime.build_collection_join_filter_fn, join_filter_input_factory=runtime.join_filter_input_factory, and_filter=runtime.and_filter_fn, serialize_filter_for_log=runtime.serialize_filter_for_log_fn, log_kv=runtime.log_kv)
    if request.relation not in (("project", "perf"), ("people", "perf"), ("org", "perf")) and hop2_kind in ("project", "org") and request.org_filter:
        hop2_filter = runtime.and_filter_fn(hop2_filter, request.org_filter)
    planner_join_filter_spec = dict((request.planner_filter_spec or {}).get("join_filter") or {})
    join_filter_diff = runtime.diff_filter_spec_fn(planner_filter_spec=planner_join_filter_spec, executed_filter_spec=executed_join_filter_spec, list_match_mode="subset")
    join_filter_diff_changed = join_filter_diff.get("changed", {})
    runtime.log_kv("RAG.JOIN.HOP2.FILTER_SPEC.DIFF", level="error" if join_filter_diff_changed else "info", planner_filter_keys=join_filter_diff.get("planner_keys", []), changed=join_filter_diff_changed, changed_count=len(join_filter_diff_changed), planner_join_filter_spec=planner_join_filter_spec, executed_join_filter_spec=executed_join_filter_spec, tier="debug")
    if join_filter_diff_changed:
        raise StrategyViolation(error_code="STRATEGY_MISMATCH", reason=f"join Hop2 filter_spec mismatch between planner and executed (changed_keys={list(join_filter_diff_changed.keys())})")

    if hop2_col == COL_PERF:
        runtime.log_kv("RAG.JOIN.HOP2.ORG_GATE.SKIP", hop2_col=hop2_col, relation=request.relation, join_key_mode=hop2_join_key_mode, reason="hop2_perf_join_key_only", tier="debug")
    else:
        hop2_filter = runtime.with_org_must_gate_fn(hop2_filter, col=hop2_col, mode_override="join_hop2")
    if hop2_col in (COL_PROJECT, COL_PERF) and request.year_range_filter:
        hop2_filter = runtime.and_filter_fn(hop2_filter, request.year_range_filter)
    if hop2_col == COL_PERF and request.perf_type_filter:
        hop2_filter = runtime.and_filter_fn(hop2_filter, request.perf_type_filter)

    executed_join_meta = dict((executed_join_filter_spec or {}).get("_meta") or {})
    effective_join_mode = str(hop2_join_key_mode or "")
    join_compile_selection = str(executed_join_meta.get("join_compile_selection") or join_compile_selection)
    hop2_key_strategy = str(executed_join_meta.get("hop2_key_strategy") or ("pjt_no" if effective_join_mode == "group" else "pjt_id_in"))
    resolved_runtime_key_kind = str(executed_join_meta.get("resolved_runtime_key_kind") or ("pjt_no" if hop2_key_strategy == "pjt_no" else "pjt_id"))
    join_keys_used_count = int(
        executed_join_meta.get("join_keys_used_count")
        if executed_join_meta.get("join_keys_used_count") is not None
        else (len(join_pjt_nos) if hop2_key_strategy == "pjt_no" else len(join_pjt_ids))
    )
    runtime.log_kv("RAG.JOIN.HOP2", hop2_col=hop2_col, hop2_kind=hop2_kind, hop2_q=request.query_text, hop2_tag_filters=hop2_tag_filters, hop2_filter=str(hop2_filter) if hop2_filter is not None else None, hop2_k_base=hop2_k_base, hop2_keep=hop2_keep, join_pjt_ids_preview=join_pjt_ids[:10], join_pjt_nos_preview=join_pjt_nos[:10], tier="debug")

    hop2_reranked, local_timings_h2 = _run_ranked_hop(collection=hop2_col, query_text=request.query_text, keywords=request.keywords, query_filter=hop2_filter, k_base=hop2_k_base, dense_topk=request.topk_dense, sparse_vector_name_eff=request.sparse_vector_name_eff, sparse_weight_eff=request.sparse_weight_eff, vector_names=request.vector_names, w_dense_map=request.w_dense_map, fallback_emb=request.fallback_emb, qdr=runtime.qdr, preset=runtime.preset, contract_scope="join_hop2", rerank_mode="join", rerank_base_route=("perf" if hop2_kind == "perf" else request.base_route), rerank_keep=int(os.getenv("RAG_HOP2_FINAL_KEEP", "80")), intent_item=request.intent_item, lex_w_eff=request.lex_w_eff, ctx_hard_limit=request.ctx_hard_limit, log_prefix="RAG.DENSE.THRESHOLD.HOP2", action=request.action, base_route=request.base_route, relation=request.relation, runtime=runtime)
    hop2_top = hop2_reranked[: max(1, hop2_keep)]
    runtime.log_top_points("RAG.JOIN.HOP2.TOP", hop2_top, topn=int(os.getenv("RAG_LOG_TOPN_HOP2", "8")), tier="debug")
    hop2_payload = build_join_hop_timing_payload(merge_log_fields=runtime.merge_log_fields_fn, local_timings=local_timings_h2)
    runtime.log_kv("RAG.JOIN.HOP2.TIMINGS", tier="debug", **hop2_payload)

    t0 = time.time()
    hydrate_limit = min(max(1, hop2_keep), request.ctx_hard_limit)
    runtime.hydrate_points_fn(hop2_reranked[:hydrate_limit], chunk_size=int(os.getenv("RAG_HYDRATE_FULL_CHUNK", "64")))
    reverse_trace = None
    followup_perf_top: List[Any] = []
    if request.reverse_trace_followup and request.relation == ("perf", "project") and join_pjt_ids:
        followup_filter = runtime.build_collection_join_filter_fn(
            hop2_col=COL_PERF,
            join_key_mode="instance",
            join_ids=join_pjt_ids,
            pjt_nos=[],
            query=request.query_text,
            apply_query_tag_inference=False,
        )
        if request.perf_tag_filter:
            followup_filter = runtime.and_filter_fn(followup_filter, request.perf_tag_filter)
        if request.perf_type_filter:
            followup_filter = runtime.and_filter_fn(followup_filter, request.perf_type_filter)
        if request.year_range_filter:
            followup_filter = runtime.and_filter_fn(followup_filter, request.year_range_filter)
        followup_keep = int(os.getenv("RAG_REVERSE_TRACE_KEEP", str(max(1, hop2_keep))))
        followup_reranked, local_timings_h3 = _run_ranked_hop(
            collection=COL_PERF,
            query_text=request.query_text,
            keywords=request.keywords,
            query_filter=followup_filter,
            k_base=max(hop2_k_base, int(os.getenv("RAG_REVERSE_TRACE_TOPK_BASE", str(hop2_k_base)))),
            dense_topk=request.topk_dense,
            sparse_vector_name_eff=request.sparse_vector_name_eff,
            sparse_weight_eff=request.sparse_weight_eff,
            vector_names=request.vector_names,
            w_dense_map=request.w_dense_map,
            fallback_emb=request.fallback_emb,
            qdr=runtime.qdr,
            preset=runtime.preset,
            contract_scope="reverse_trace_hop3",
            rerank_mode="join",
            rerank_base_route="perf",
            rerank_keep=followup_keep,
            intent_item=request.intent_item,
            lex_w_eff=request.lex_w_eff,
            ctx_hard_limit=request.ctx_hard_limit,
            log_prefix="RAG.DENSE.THRESHOLD.HOP3",
            action=request.action,
            base_route=request.base_route,
            relation=("project", "perf"),
            runtime=runtime,
        )
        followup_perf_top = _dedupe_reverse_trace_followup(list(hop1_top or []), list(followup_reranked[: max(1, followup_keep)]), payload_get_fn=runtime.payload_get_fn)
        if followup_perf_top:
            runtime.hydrate_points_fn(followup_perf_top[: min(len(followup_perf_top), request.ctx_hard_limit)], chunk_size=int(os.getenv("RAG_HYDRATE_FULL_CHUNK", "64")))
        reverse_trace = _build_reverse_trace_payload(
            origin_perf_points=list(hop1_top or []),
            origin_project_points=list(hop2_top or []),
            followup_perf_points=followup_perf_top,
            followup_relation_hint=request.followup_relation_hint,
            payload_get_fn=runtime.payload_get_fn,
        )
        runtime.log_top_points("RAG.REVERSE_TRACE.HOP3.TOP", followup_perf_top, topn=int(os.getenv("RAG_LOG_TOPN_HOP2", "8")), tier="debug")
        runtime.log_kv(
            "RAG.REVERSE_TRACE.HOP3.TIMINGS",
            tier="debug",
            **build_join_hop_timing_payload(merge_log_fields=runtime.merge_log_fields_fn, local_timings=local_timings_h3),
        )
        runtime.timing_put_fn(request.timings, "info.origin_project_count", len(hop2_top or []))
        runtime.timing_put_fn(request.timings, "info.followup_perf_count", len(followup_perf_top or []))
        runtime.timing_put_fn(request.timings, "info.reverse_trace_hop_count", 3)
        if not followup_perf_top:
            runtime.timing_put_fn(request.timings, "info.failed_step", "reverse_trace_partial")
    runtime.timing_put_fn(request.timings, "phase.hydrate_full_payload", time.time() - t0)
    runtime.timing_put_fn(request.timings, "phase.hop_total", time.time() - t_hop0)
    hits = (hop1_top or []) + (hop2_top or []) + (followup_perf_top or [])
    debug_meta = {"join": {"join_key_mode": join_key_mode, "join_key_source": join_key_source, "hop1_mode": hop1_strategy, "policy_source": request.join_execution_policy.get("policy_source") or "execution_policy", "execution_policy_reason": request.join_execution_policy.get("execution_policy_reason") or request.join_execution_policy.get("reason"), "hop2_key_strategy": hop2_key_strategy, "resolved_runtime_key_kind": resolved_runtime_key_kind, "join_compile_selection": join_compile_selection, "join_keys_used_count": join_keys_used_count, "seed_key_source": request.join_execution_policy.get("seed_key_source"), "seed_key_count": int(request.join_execution_policy.get("seed_key_count") or 0)}, "reverse_trace": {"enabled": bool(request.reverse_trace_followup and request.relation == ("perf", "project")), "hop_count": (3 if request.reverse_trace_followup and request.relation == ("perf", "project") else 2), "origin_project_count": len(hop2_top or []), "followup_perf_count": len(followup_perf_top or []), "followup_relation_hint": request.followup_relation_hint}}
    series = None
    if callable(runtime.series_builder_fn):
        series = runtime.series_builder_fn(
            reranked=hits,
            intent=request.intent_item,
            hinted_limit=hop2_keep,
            policy_limit=request.ctx_hard_limit,
            payload_get_fn=runtime.payload_get_fn,
        )
    result = assemble_join_rag_result(context_builder=runtime.context_builder, hop1_points=hop1_top, hop1_kind=hop1_kind, hop1_query_text=request.query_text, hop1_max_items=hop1_keep, hop2_reranked=hop2_top, hop2_label=hop2_label, effective_join_mode=effective_join_mode, join_pjt_ids=join_pjt_ids, join_pjt_nos=join_pjt_nos, preset_max_ctx_items=hop2_keep, ctx_hard_limit=request.ctx_hard_limit, action=request.action, hop2_kind=hop2_kind, output_type=request.output_type, mode=request.mode, query_text=request.query_text, people_terms=request.people_terms, person_ids=request.people_ids, org_terms=request.org_terms, org_role=request.org_role, timings=request.timings, t_all0=request.t_all0, stack=request.stack, keywords=request.keywords, hits=hits, series=series, reverse_trace=reverse_trace, debug_meta=debug_meta, timing_put=lambda key, value: runtime.timing_put_fn(request.timings, key, value), log_kv=runtime.log_kv)
    return JoinOrchestrationOutcome(
        result=result,
        join_key_source=join_key_source,
        hop1_mode=hop1_strategy,
        join_compile_selection=join_compile_selection,
        hop2_key_strategy=hop2_key_strategy,
        resolved_runtime_key_kind=resolved_runtime_key_kind,
        join_keys_used_count=join_keys_used_count,
    )
