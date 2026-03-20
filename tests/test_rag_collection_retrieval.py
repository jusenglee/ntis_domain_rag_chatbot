from __future__ import annotations

from types import SimpleNamespace

from apps.core.rag_collection_retrieval import build_emb_map_for_collection, expand_vector_names, retrieve_collections


class Point:
    def __init__(self, score):
        self.score = score
        self.payload = {}


def test_expand_vector_names_adds_base_variant_once():
    assert expand_vector_names(["e5i_qa", "e5i_qa", "e5_qa"]) == ["e5i_qa", "e5i", "e5_qa", "e5"]


def test_build_emb_map_for_collection_filters_unavailable_vectors():
    logger = SimpleNamespace(warning=lambda *args, **kwargs: None)
    emb_map = build_emb_map_for_collection(
        qdr=object(),
        col="ntis_project_v1",
        vector_names=["e5i_qa", "e5_qa"],
        pre_vecs={"e5i_qa": "precomputed-e5i"},
        fallback_emb={"e5_qa": "fallback-e5"},
        named_vectors_in_collection_fn=lambda qdr, col: {"e5i_qa", "e5_qa"},
        logger=logger,
    )

    assert emb_map == {"e5i_qa": "precomputed-e5i", "e5_qa": "fallback-e5"}


def test_retrieve_collections_returns_per_collection_results():
    timings = {}
    log_events = []
    sr_by_col, per_col_stats = retrieve_collections(
        target_cols=["ntis_project_v1"],
        qdr=object(),
        vector_names=["e5i_qa"],
        pre_vecs={"e5i_qa": "vec"},
        fallback_emb={},
        named_vectors_in_collection_fn=lambda qdr, col: {"e5i_qa"},
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
        server_filter_for_col_fn=lambda col: {"kind": "filter", "col": col},
        mode="lookup",
        plan_mode="lookup",
        q="AI related project",
        kws=["AI"],
        preset=SimpleNamespace(lexical_fields=["title"]),
        sparse_vector_name_eff="bm25",
        sparse_topk_eff=20,
        sparse_weight_eff=1.0,
        topk_dense=10,
        topk_lex_cand=20,
        topk_lex=10,
        call_dense_retrieve_hybrid_multi_fn=lambda **kwargs: {"dense": {"e5i_qa": [Point(0.9)]}, "lexical": [Point(0.7)], "hybrid": []},
        validate_lookup_join_hybrid_metrics_fn=lambda **kwargs: None,
        apply_dense_threshold_fn=lambda *args, **kwargs: None,
        ensure_collection_mark_fn=lambda points, col: None,
        log_kv_fn=lambda event, **kwargs: log_events.append((event, kwargs)),
        log_section_fn=lambda *args, **kwargs: None,
        log_top_points_fn=lambda *args, **kwargs: None,
        serialize_filter_for_log_fn=lambda value: value,
        resolve_sparse_hits_metric_fn=lambda local_timings: 0.0,
        record_col_timings_fn=lambda timings, col, stats, local_timings: None,
        action="detail",
        base_route="project",
        relation=None,
        search_filter_enabled=False,
        search_filter_signal=False,
        search_filter_conf_ok=False,
        title_filter_server_applied=False,
        lex_w_eff={"title": 1.0},
        col_project="ntis_project_v1",
        col_perf="ntis_perf_v1",
        timings=timings,
    )

    assert "ntis_project_v1" in sr_by_col
    assert per_col_stats["ntis_project_v1"]["dense_hits"] == 1.0
    assert per_col_stats["ntis_project_v1"]["lex_hits"] == 1.0
    retrieve_events = [payload for event, payload in log_events if event == "RAG.COL.RETRIEVE"]
    assert retrieve_events
    assert retrieve_events[0]["execution_mode"] == "lookup"
    assert retrieve_events[0]["execution_base_route"] == "project"
    assert retrieve_events[0]["planner_mode_hint"] == "lookup"
    assert retrieve_events[0]["strategy_source"] == "execution_request"


def test_retrieve_collections_delegates_dense_threshold_policy_to_runtime_wrapper():
    seen = {}

    def apply_dense_threshold_wrapper(sr, **kwargs):
        seen["kwargs"] = dict(kwargs)

    retrieve_collections(
        target_cols=["ntis_project_v1"],
        qdr=object(),
        vector_names=["e5i_qa"],
        pre_vecs={"e5i_qa": "vec"},
        fallback_emb={},
        named_vectors_in_collection_fn=lambda qdr, col: {"e5i_qa"},
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
        server_filter_for_col_fn=lambda col: None,
        mode="lookup",
        plan_mode="lookup",
        q="dense threshold ownership",
        kws=["dense"],
        preset=SimpleNamespace(lexical_fields=["title"]),
        sparse_vector_name_eff="bm25",
        sparse_topk_eff=20,
        sparse_weight_eff=1.0,
        topk_dense=10,
        topk_lex_cand=20,
        topk_lex=10,
        call_dense_retrieve_hybrid_multi_fn=lambda **kwargs: {"dense": {"e5i_qa": [Point(0.9)]}, "lexical": [], "hybrid": []},
        validate_lookup_join_hybrid_metrics_fn=lambda **kwargs: None,
        apply_dense_threshold_fn=apply_dense_threshold_wrapper,
        ensure_collection_mark_fn=lambda points, col: None,
        log_kv_fn=lambda *args, **kwargs: None,
        log_section_fn=lambda *args, **kwargs: None,
        log_top_points_fn=lambda *args, **kwargs: None,
        serialize_filter_for_log_fn=lambda value: value,
        resolve_sparse_hits_metric_fn=lambda local_timings: 0.0,
        record_col_timings_fn=lambda timings, col, stats, local_timings: None,
        action="detail",
        base_route="project",
        relation=None,
        search_filter_enabled=False,
        search_filter_signal=False,
        search_filter_conf_ok=False,
        title_filter_server_applied=False,
        lex_w_eff={"title": 1.0},
        col_project="ntis_project_v1",
        col_perf="ntis_perf_v1",
        timings={},
    )

    assert seen["kwargs"]["log_prefix"] == "RAG.DENSE.THRESHOLD.COL"
    assert seen["kwargs"]["col"] == "ntis_project_v1"
    assert "use_dense_threshold" not in seen["kwargs"]
    assert "min_dense_score" not in seen["kwargs"]
