from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from apps.core.rag_dispatch_runtime import build_dispatch_runtime_support


class Embedder:
    def __init__(self, vector):
        self.vector = vector
        self.calls = 0

    def get_query_embedding(self, text):
        self.calls += 1
        return list(self.vector)


class Point:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else {}


def _support(hydrate_sink):
    resources = SimpleNamespace(embed_e5i=Embedder([0.1, 0.2]), embed_e5=Embedder([0.3, 0.4]))
    support = build_dispatch_runtime_support(
        qdr='qdr',
        resources=resources,
        vector_names=['e5i_qa', 'e5_qa'],
        timings={},
        fallback_emb={'e5i_qa': object(), 'e5_qa': object()},
        use_dense_threshold_policy=False,
        min_dense_score_policy=0.0,
        col_project='ntis_project_v1',
        col_perf='ntis_perf_v1',
        col_support='ntis_support_v1',
        tag_pjt_info='PJT_INFO',
        tag_pjt_mp='PJT_MP',
        tag_pjt_org='PJT_ORG',
        rag_collection_allowlist=['ntis_project_v1', 'ntis_perf_v1'],
        project_tags_norm={'PJT_INFO'},
        perf_tags_norm={'PERF'},
        normalize_tag_value_fn=lambda value: value,
        named_vectors_in_collection_fn=lambda qdr, col: {'e5i_qa', 'e5_qa'},
        get_relation_route_fn=lambda relation: None,
        build_tag_only_filter_fn=lambda tags: ('tag', tuple(tags)),
        and_filter_fn=lambda a, b: (a, b),
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: ('project_id', pjt_ids, pjt_nos),
        build_people_filter_fn=lambda *args, **kwargs: None,
        build_collection_join_filter_fn=lambda *args, **kwargs: None,
        with_org_must_gate_fn=lambda base_filter, **kwargs: base_filter,
        validate_resolved_join_keys_fn=lambda **kwargs: None,
        retrieve_collections_fn=lambda **kwargs: ({}, {}),
        call_dense_retrieve_hybrid_multi_fn=lambda **kwargs: {},
        validate_lookup_join_hybrid_metrics_fn=lambda **kwargs: None,
        apply_dense_threshold_fn=lambda *args, **kwargs: None,
        ensure_collection_mark_fn=lambda points, col: None,
        resolve_sparse_hits_metric_fn=lambda *args, **kwargs: 0.0,
        record_col_timings_fn=lambda *args, **kwargs: None,
        rank_source_factory=lambda **kwargs: SimpleNamespace(**kwargs),
        use_dense_score_weight_fn=lambda: False,
        dense_score_weight_fn=lambda points: 1.0,
        rrf_merge_fn=lambda sources, **kwargs: [],
        dedup_by_doc_id_fn=lambda hits: list(hits),
        timing_put_fn=lambda timings, key, value: timings.__setitem__(key, value),
        finalize_rag_result_fn=lambda **kwargs: kwargs,
        resolve_env_topn_fn=lambda *args, **kwargs: 3,
        prepare_title_post_rerank_fn=lambda *args, **kwargs: {},
        final_rerank_fn=lambda *args, **kwargs: [],
        resolve_effective_min_reranked_fn=lambda **kwargs: (0, 'none'),
        has_explicit_identifiers_fn=lambda intent: False,
        enforce_reranked_contract_fn=lambda **kwargs: None,
        hydrate_reranked_payloads_fn=lambda **kwargs: None,
        soft_title_contains_fn=lambda *args, **kwargs: False,
        aggregation_builder_fn=lambda **kwargs: None,
        payload_get_fn=lambda payload, key: payload.get(key),
        hit_key_fn=lambda hit: ('k', 'v'),
        title_match_mode_contains='contains',
        serialize_filter_for_log_fn=lambda value: value,
        diff_filter_spec_fn=lambda **kwargs: {},
        merge_log_fields_fn=lambda a, b: {**a, **b},
        extract_join_keys_fn=lambda *args, **kwargs: None,
        resolve_group_pjt_ids_fn=lambda *args, **kwargs: [],
        count_missing_join_keys_fn=lambda *args, **kwargs: {},
        debug_force_join_keys_enabled_fn=lambda: False,
        get_meta_fn=lambda payload: dict(payload.get('meta_basic') or {}),
        pick_first_fn=lambda *values: next((str(v).strip() for v in values if str(v or '').strip()), ''),
        people_filter_input_factory=SimpleNamespace,
        join_filter_input_factory=SimpleNamespace,
        context_builder_fn=lambda *args, **kwargs: ('ctx', [], None),
        logger_obj=SimpleNamespace(warning=lambda *args, **kwargs: None),
        log_kv_fn=lambda *args, **kwargs: None,
        log_section_fn=lambda *args, **kwargs: None,
        log_top_points_fn=lambda *args, **kwargs: None,
        point_summary_get_meta_fn=lambda payload: dict(payload.get('meta_basic') or {}),
        point_summary_resolve_collection_fn=lambda point, payload: payload.get('_collection', ''),
        precomputed_embedding_cls=lambda vec: SimpleNamespace(get_query_embedding=lambda _text: vec, get_text_embedding=lambda _text: vec, _vec=vec),
    )

    import apps.core.rag_hydration_runtime as hydration_module
    hydration_module.hydrate_points_payload = lambda qdr, points, **kwargs: hydrate_sink.append((qdr, points, kwargs))
    return support, resources



def test_get_pre_vecs_caches_per_query():
    hydrate_sink = []
    support, resources = _support(hydrate_sink)
    first = support.get_pre_vecs_fn('query')
    second = support.get_pre_vecs_fn('query')
    assert first is second
    assert resources.embed_e5i.calls == 1
    assert resources.embed_e5.calls == 1



def test_hydrate_points_passes_expected_runtime_fields():
    hydrate_sink = []
    support, _ = _support(hydrate_sink)
    support.hydrate_points_fn([Point(payload={'doc_id': 'd1'})], chunk_size=64)
    qdr, points, kwargs = hydrate_sink[0]
    assert qdr == 'qdr'
    assert len(points) == 1
    assert kwargs['col_project'] == 'ntis_project_v1'
    assert kwargs['chunk_size'] == 64



def test_normalize_target_collections_maps_known_aliases_once():
    hydrate_sink = []
    support, _ = _support(hydrate_sink)
    assert support.normalize_target_collections_fn(['project', 'ntis_perf_v1', 'project']) == ['ntis_project_v1', 'ntis_perf_v1']



def test_build_base_runtime_uses_dispatch_helpers():
    hydrate_sink = []
    support, _ = _support(hydrate_sink)
    runtime = support.build_base_runtime_fn()
    assert runtime.coerce_int_fn('7', 1) == 7
    assert runtime.get_attr_fn({'a': 1}, 'a') == 1
    assert runtime.hydrate_points_payload_fn is support.hydrate_points_fn



def test_rag_pipeline_uses_dispatch_runtime_module():
    source = Path('apps/core/rag_pipeline.py').read_text(encoding='utf-8')
    assert 'build_dispatch_runtime_support(' in source
    assert 'def _hydrate_points(' not in source
    assert 'def _get_pre_vecs(' not in source
    assert 'def _normalize_target_collections(' not in source
    assert 'JoinOrchestrationRuntime(' not in source
    assert 'BaseOrchestrationRuntime(' not in source
