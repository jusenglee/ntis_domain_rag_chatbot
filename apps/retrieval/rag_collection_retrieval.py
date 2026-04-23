from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Sequence, Tuple

from loguru import logger


def expand_vector_names(names: Sequence[str]) -> List[str]:
    """요청한 vector 이름 목록에 대응하는 base 이름까지 확장한다.

    *_qa 벡터를 쓰는 경우 같은 base 벡터도 함께 시도해 컬렉션별 가용 벡터 차이를 흡수한다.
    """
    expanded: List[str] = []
    for name in names:
        if name not in expanded:
            expanded.append(name)
        if name.endswith("_qa"):
            base = name[:-3]
            if base and base not in expanded:
                expanded.append(base)
    return expanded


def build_emb_map_for_collection(
    *,
    qdr: Any,
    col: str,
    vector_names: Sequence[str],
    pre_vecs: Dict[str, Any],
    fallback_emb: Dict[str, Any],
    named_vectors_in_collection: Callable[[Any, str], Any],
) -> Dict[str, Any]:
    """특정 컬렉션에서 실제로 쓸 수 있는 embedding만 골라 dense 검색 입력 맵을 만든다."""
    vec_avail = named_vectors_in_collection(qdr, col)
    expanded_names = expand_vector_names(list(vector_names))
    use_vecs = [v for v in expanded_names if (not isinstance(vec_avail, set) or v in vec_avail)]
    if isinstance(vec_avail, set) and not use_vecs and vector_names:
        logger.warning(
            "no matching vectors for col={} available={} requested={} expanded={}",
            col,
            sorted(vec_avail),
            list(vector_names),
            expanded_names,
        )
    out: Dict[str, Any] = {}
    for vname in use_vecs:
        pe = pre_vecs.get(vname)
        out[vname] = pe if pe is not None else fallback_emb.get(vname)
    return {k: v for k, v in out.items() if v is not None}


def retrieve_collections(
    *,
    target_cols: Sequence[str],
    qdr: Any,
    vector_names: Sequence[str],
    pre_vecs: Dict[str, Any],
    fallback_emb: Dict[str, Any],
    named_vectors_in_collection: Callable[[Any, str], Any],
    server_filter_for_col: Callable[[str], Any],
    mode: str,
    plan_mode: str,
    q: str,
    kws: List[str],
    preset: Any,
    sparse_vector_name_eff: str,
    sparse_topk_eff: int,
    sparse_weight_eff: float,
    topk_dense: int,
    topk_lex_cand: int,
    topk_lex: int,
    call_dense_retrieve_hybrid_multi: Callable[..., Dict[str, Any]],
    validate_lookup_join_hybrid_metrics: Callable[..., None],
    apply_dense_threshold: Callable[..., None],
    ensure_collection_mark: Callable[[Any, str], None],
    log_kv: Callable[..., None],
    log_section: Callable[..., None],
    log_top_points: Callable[..., None],
    serialize_filter_for_log: Callable[[Any], Any],
    resolve_sparse_hits_metric: Callable[[Dict[str, float]], float],
    record_col_timings: Callable[..., None],
    action: str,
    base_route: str,
    relation: Any,
    search_filter_enabled: bool,
    search_filter_signal: bool,
    search_filter_conf_ok: bool,
    title_filter_server_applied: bool,
    lex_w_eff: Dict[str, float],
    col_project: str,
    col_perf: str,
    timings: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, float]]]:
    """target 컬렉션들을 순회하며 hybrid retrieval을 실행하고 컬렉션별 결과와 통계를 모은다.

    각 컬렉션에 다른 서버 필터를 적용하고, hybrid contract 검증과 dense threshold 적용까지 한곳에서 처리한다.
    """
    sr_by_col: Dict[str, Dict[str, Any]] = {}
    per_col_stats: Dict[str, Dict[str, float]] = {}

    for col in target_cols:
        emb_map_col = build_emb_map_for_collection(
            qdr=qdr,
            col=col,
            vector_names=vector_names,
            pre_vecs=pre_vecs,
            fallback_emb=fallback_emb,
            named_vectors_in_collection=named_vectors_in_collection,
        )
        use_dense_k = topk_dense if emb_map_col else 0

        qfilter = server_filter_for_col(col)
        if mode == "search":
            qfilter = None

        log_kv(
            "RAG.COL.RETRIEVE",
            col=col,
            execution_mode=mode,
            execution_base_route=base_route,
            execution_action=action,
            execution_relation=relation,
            planner_mode_hint=plan_mode,
            strategy_source="execution_request",
            search_filter_enabled=search_filter_enabled,
            search_filter_signal=search_filter_signal,
            search_filter_conf_ok=search_filter_conf_ok,
            title_filter_server_applied=int(title_filter_server_applied),
            use_dense_k=use_dense_k,
            topk_lex_cand=topk_lex_cand,
            topk_lex=topk_lex,
            sparse_vector_name=sparse_vector_name_eff,
            sparse_topk=int(sparse_topk_eff),
            sparse_weight=float(sparse_weight_eff),
            qfilter=str(qfilter) if qfilter is not None else None,
            executed_filter_spec_json={
                "qfilter": serialize_filter_for_log(qfilter),
                "title_filter_server_applied": bool(title_filter_server_applied and col in (col_project, col_perf)),
            },
            lex_w_preview={k: float(lex_w_eff.get(k)) for k in list(lex_w_eff.keys())[:8]},
            dense_vecs=list(emb_map_col.keys()),
            tier="debug",
        )

        local_timings: Dict[str, float] = {}
        sr = call_dense_retrieve_hybrid_multi(
            qdr=qdr,
            emb_map=emb_map_col,
            qtext=q,
            kws=kws,
            collection=col,
            lexical_fields=preset.lexical_fields,
            sparse_vector_name=sparse_vector_name_eff,
            sparse_topk=sparse_topk_eff,
            top_k_dense=topk_dense if plan_mode == "lookup" else use_dense_k,
            top_k_lex_cand=topk_lex_cand,
            top_k_lex=topk_lex,
            query_filter=qfilter,
            timings_out=local_timings,
            require_hybrid_both_sides=(plan_mode in ("lookup", "join")),
            contract_scope=f"{plan_mode}:{col}",
            violation_on_contract=(plan_mode in ("lookup", "join")),
        )
        if plan_mode in ("lookup", "join"):
            validate_lookup_join_hybrid_metrics(
                mode=plan_mode,
                contract_scope=f"{plan_mode}:{col}",
                timings=local_timings,
                strict=False,
            )

        apply_dense_threshold(
            sr,
            log_prefix="RAG.DENSE.THRESHOLD.COL",
            col=col,
            action=action,
            base_route=base_route,
            relation=relation,
        )

        hybrid_points = sr.get("hybrid") or []
        if hybrid_points:
            ensure_collection_mark(hybrid_points, col)
        else:
            ensure_collection_mark((sr.get("lexical") or []), col)
            for _, lst in (sr.get("dense") or {}).items():
                ensure_collection_mark(lst or [], col)

        sr_by_col[col] = sr

        dense_vec_stats: Dict[str, Dict[str, float]] = {}
        if not hybrid_points:
            for vname, lst in (sr.get("dense") or {}).items():
                if not lst:
                    continue
                top_score = None
                try:
                    top_score = float(getattr(lst[0], "score", 0.0))
                except Exception:
                    top_score = None
                dense_vec_stats[str(vname)] = {
                    "hits": float(len(lst)),
                    "top_score": float(top_score) if top_score is not None else -1.0,
                }

            lex_top_score = None
            if sr.get("lexical"):
                try:
                    lex_top_score = float(getattr(sr["lexical"][0], "score", 0.0))
                except Exception:
                    lex_top_score = None

            log_section(
                "RAG.COL.RESULTS",
                {
                    "col": col,
                    "dense": dense_vec_stats,
                    "lexical": {
                        "hits": float(len(sr.get("lexical") or [])),
                        "top_score": float(lex_top_score) if lex_top_score is not None else -1.0,
                    },
                },
                tier="debug",
            )
        else:
            top_score = None
            try:
                top_score = float(getattr(hybrid_points[0], "score", 0.0))
            except Exception:
                top_score = None
            log_section(
                "RAG.COL.RESULTS",
                {
                    "col": col,
                    "hybrid": {
                        "hits": float(len(hybrid_points)),
                        "top_score": float(top_score) if top_score is not None else -1.0,
                    },
                },
                tier="debug",
            )

        dense_topn = int(os.getenv("RAG_LOG_TOPN_COL_DENSE", "4"))
        if not hybrid_points:
            for vname, lst in (sr.get("dense") or {}).items():
                log_top_points(f"RAG.COL.DENSE.TOP.{col}.{vname}", lst or [], topn=dense_topn)

        lex_topn = int(os.getenv("RAG_LOG_TOPN_COL_LEX", "4"))
        if not hybrid_points:
            log_top_points(f"RAG.COL.LEX.TOP.{col}", sr.get("lexical") or [], topn=lex_topn)
        else:
            log_top_points(f"RAG.COL.HYBRID.TOP.{col}", hybrid_points, topn=lex_topn)

        if not hybrid_points:
            d_hit = sum(len(lst or []) for lst in (sr.get("dense") or {}).values())
            l_hit = len(sr.get("lexical") or [])
            best_dense = None
            for lst in (sr.get("dense") or {}).values():
                if lst:
                    s0 = getattr(lst[0], "score", None)
                    try:
                        best_dense = max(best_dense or -1.0, float(s0) if s0 is not None else -1.0)
                    except Exception:
                        pass

            per_col_stats[col] = {
                "dense_hits": float(d_hit),
                "lex_hits": float(l_hit),
                "dense_queries": float(local_timings.get("dense_queries", 0.0)),
                "sparse_hits": resolve_sparse_hits_metric(local_timings),
                "hybrid_once_hits": float(local_timings.get("hybrid_once_hits", 0.0)),
                "hybrid_mode_used": bool(float(local_timings.get("hybrid_once_hits", 0.0)) > 0.0),
                "best_dense": float(best_dense) if best_dense is not None else -1.0,
                "total": float(local_timings.get("total", 0.0)),
            }

            log_kv(
                "RAG.COL.STATS",
                tier="debug",
                col=col,
                execution_mode=mode,
                execution_base_route=base_route,
                strategy_source="execution_request",
                dense_hits=int(d_hit),
                lex_hits=int(l_hit),
                best_dense=float(best_dense) if best_dense is not None else -1.0,
                timings=local_timings,
            )
            record_col_timings(
                timings,
                col,
                stats=per_col_stats[col],
                local_timings=local_timings,
            )
        else:
            per_col_stats[col] = {
                "hybrid_hits": float(len(hybrid_points)),
                "dense_queries": float(local_timings.get("dense_queries", 0.0)),
                "sparse_hits": resolve_sparse_hits_metric(local_timings),
                "hybrid_once_hits": float(local_timings.get("hybrid_once_hits", 0.0)),
                "hybrid_mode_used": bool(float(local_timings.get("hybrid_once_hits", 0.0)) > 0.0),
                "total": float(local_timings.get("total", 0.0)),
            }
            log_kv(
                "RAG.COL.STATS",
                tier="debug",
                col=col,
                execution_mode=mode,
                execution_base_route=base_route,
                strategy_source="execution_request",
                hybrid_hits=int(len(hybrid_points)),
                timings=local_timings,
            )
            record_col_timings(
                timings,
                col,
                stats=per_col_stats[col],
                local_timings=local_timings,
            )

    return sr_by_col, per_col_stats
