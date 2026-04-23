from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from loguru import logger

TimingPut = Callable[[str, Any], None]
PayloadGet = Callable[[Dict[str, Any], str], Any]
CoerceInt = Callable[[Any, int], int]
GetAttr = Callable[[Any, str, Any], Any]
HydratePayloads = Callable[[Sequence[Any]], None]
SoftTitleContains = Callable[[Dict[str, Any], List[str]], bool]


_HYDRATE_TOPN_BY_MODE_OUTPUT_ROUTE: dict[tuple[str, str, str], int] = {
    ("lookup", "detail", "project"): 6,
    ("lookup", "detail", "perf"): 5,
    ("lookup", "detail", "people"): 5,
    ("lookup", "detail", "org"): 5,
    ("join", "relation", "project"): 6,
    ("join", "series", "project"): 6,
    ("search", "stats", "project"): 8,
}

_HYDRATE_TOPN_BY_MODE_OUTPUT: dict[tuple[str, str], int] = {
    ("lookup", "detail"): 5,
    ("lookup", "summary"): 4,
    ("lookup", "list"): 5,
    ("join", "relation"): 6,
    ("join", "series"): 6,
    ("join", "summary"): 5,
    ("search", "detail"): 5,
    ("search", "summary"): 6,
    ("search", "list"): 8,
    ("search", "stats"): 8,
}


def prepare_title_post_rerank(
    merged_rrf: Sequence[Any],
    *,
    plan_mode: str,
    title_match_mode: str,
    title_match_mode_contains: str,
    title_terms: List[str],
    lookup_title_filter_policy: str,
    soft_title_contains: SoftTitleContains,
    log_kv: Callable[..., None],
    timing_put: TimingPut,
) -> Dict[str, Any]:
    """lookup title contains 질의에 대해 post-rerank용 soft title boost 설정을 계산한다.

    서버 필터를 강하게 걸지 않는 대신, 상위 문서에서 제목 일치 히트 수를 세고 rerank에만 약한 가중치를 전달한다.
    """
    title_post_filter_applied = False
    title_post_filter_hits = 0
    title_soft_boost = 0.0
    title_soft_terms_for_rerank: List[str] = []

    if plan_mode == "lookup" and title_match_mode == title_match_mode_contains and bool(title_terms):
        title_filter_topn = max(1, int(os.getenv("RAG_TITLE_POST_FILTER_TOPN", "80")))
        post_filter_pool = list(merged_rrf[:title_filter_topn])
        title_post_filter_hits = sum(
            1 for point in post_filter_pool if soft_title_contains(getattr(point, "payload", None) or {}, title_terms)
        )
        title_soft_terms_for_rerank = list(title_terms)
        title_soft_boost = float(os.getenv("RAG_TITLE_SOFT_BOOST", "8.0"))
        log_kv(
            "RAG.TITLE_POST_FILTER",
            applied=int(title_post_filter_applied),
            policy=lookup_title_filter_policy,
            title_match_mode=title_match_mode,
            topn=title_filter_topn,
            input_count=len(post_filter_pool),
            hits=title_post_filter_hits,
            title_terms=title_terms[:6],
            tier="debug",
        )

    timing_put("info.title_post_filter_applied", int(title_post_filter_applied))
    timing_put("info.title_post_filter_hits", int(title_post_filter_hits))
    return {
        "title_soft_terms_for_rerank": title_soft_terms_for_rerank,
        "title_soft_boost": title_soft_boost,
        "title_post_filter_applied": title_post_filter_applied,
        "title_post_filter_hits": title_post_filter_hits,
    }


def hydrate_reranked_payloads(
    *,
    reranked: Sequence[Any],
    qdr: Any,
    ctx_hard_limit: int,
    min_ctx_items: int,
    preset_max_ctx_items: int,
    intent_payload: Any,
    hinted_limit: int,
    coerce_int: CoerceInt,
    get_attr: GetAttr,
    hydrate_points_payload: HydratePayloads,
    timing_put: TimingPut,
    mode: str = "",
    output_type: Optional[str] = None,
    base_route: str = "",
) -> None:
    """최종 rerank 상위 문서의 full payload를 수화한다.

    실제 context로 쓸 수 있는 범위까지만 payload를 채워 비용을 통제하고, 상위 문서에 제목 정보가 비어 있는지 함께 점검한다.
    """
    if not reranked:
        return

    mode_name = str(mode or "").strip().lower()
    output_name = str(output_type or "").strip().lower() or "summary"
    base_route_name = str(base_route or "").strip().lower() or "project"
    max_items = min(ctx_hard_limit, max(min_ctx_items, int(preset_max_ctx_items)))
    requested_limit = max(
        0,
        coerce_int(get_attr(intent_payload, "limit", 0), 0),
        coerce_int(hinted_limit, 0),
    )

    strategy_meta = get_attr(intent_payload, "strategy_meta", {}) or {}
    normalized_intent = get_attr(intent_payload, "normalized_intent", None)
    ids_map = get_attr(normalized_intent, "ids_map", None)
    if ids_map is None and isinstance(normalized_intent, dict):
        ids_map = normalized_intent.get("ids_map")
    has_anchor_seed = bool(
        isinstance(ids_map, dict)
        and any(list(values or []) for values in ids_map.values())
    ) or bool(str((strategy_meta or {}).get("anchor_source") or "").strip())

    hydrate_topn = _HYDRATE_TOPN_BY_MODE_OUTPUT_ROUTE.get(
        (mode_name, output_name, base_route_name),
        _HYDRATE_TOPN_BY_MODE_OUTPUT.get((mode_name, output_name), max_items),
    )
    hydrate_topn = min(ctx_hard_limit, max(1, int(hydrate_topn), int(max_items)))
    exact_lookup_topn = 0
    if mode_name == "lookup":
        exact_lookup_topn = min(ctx_hard_limit, max(1, min(3, requested_limit or 1)))

    selected_points: List[Any] = []
    seen_keys: set[str] = set()

    def _append(points: Sequence[Any]) -> None:
        for point in list(points or []):
            point_id = getattr(point, "id", None)
            key = str(point_id) if point_id is not None else str(id(point))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            selected_points.append(point)

    _append(reranked[:hydrate_topn])
    if exact_lookup_topn:
        _append(reranked[:exact_lookup_topn])
    if has_anchor_seed:
        _append(reranked[: min(len(reranked), max(2, exact_lookup_topn or 0, 1))])

    reranked_for_hydrate = list(selected_points)

    t0 = time.time()
    hydrate_points_payload(reranked_for_hydrate)
    timing_put("phase.hydrate_full_payload", time.time() - t0)
    timing_put("info.hydrate_candidate_count", len(reranked_for_hydrate))
    timing_put("info.hydrate_initial_topn", int(hydrate_topn))
    timing_put("info.hydrate_exact_lookup_topn", int(exact_lookup_topn))
    timing_put("info.hydrate_anchor_seed_present", int(has_anchor_seed))

    check_top_k = min(len(reranked), max(1, requested_limit))
    missing_kor = []
    for rank, point in enumerate(reranked[:check_top_k], start=1):
        payload = getattr(point, "payload", {}) or {}
        meta_basic = payload.get("meta_basic") or {}
        if not meta_basic.get("kor_pjt_nm"):
            missing_kor.append(rank)
    logger.info(
        "hydrated={} top_k={} missing_meta_basic_kor_pjt_nm={}",
        len(reranked_for_hydrate),
        check_top_k,
        missing_kor or "none",
    )
