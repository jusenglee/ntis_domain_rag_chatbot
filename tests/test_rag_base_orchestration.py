from __future__ import annotations

from types import SimpleNamespace

from apps.core.rag_base_orchestration import (
    BaseFilterPolicyInputs,
    BaseOrchestrationRequest,
    BaseOrchestrationRuntime,
    BaseSearchPolicyInputs,
    execute_base_orchestration,
)
from apps.core.rag_types import RagResult


def test_execute_base_orchestration_calls_finalize_and_reports_followup_usage():
    seen = {}
    log_events = []

    def fake_retrieve_collections(**kwargs):
        seen["server_filter"] = kwargs["server_filter_for_col_fn"]("ntis_perf_v1")
        return (
            {
                "ntis_perf_v1": {
                    "hybrid": [],
                    "dense": {"e5": [SimpleNamespace(payload={"doc_id": "d1"})]},
                    "lexical": [],
                }
            },
            [{"collection": "ntis_perf_v1", "count": 1}],
        )

    def fake_finalize(**kwargs):
        seen["finalize"] = kwargs
        return RagResult(
            stack="M",
            keywords=list(kwargs["keywords"]),
            hits=[],
            reranked_hits=[],
            context="ctx",
            refs=[],
            timings=kwargs["timings"],
        )

    result = execute_base_orchestration(
        request=BaseOrchestrationRequest(
            mode="lookup",
            plan_mode="lookup",
            base_route="perf",
            relation=None,
            action="detail",
            output_type="detail",
            query_text="perf detail",
            keywords=["perf"],
            query_intent=SimpleNamespace(ids_map={}),
            target_cols=["ntis_perf_v1"],
            topk_dense=5,
            topk_lex_cand=5,
            topk_lex=5,
            sparse_vector_name_eff=None,
            sparse_topk_eff=5,
            sparse_weight_eff=1.0,
            vector_names=["e5"],
            pre_vecs={"e5": [0.1]},
            fallback_emb={"e5": [0.1]},
            w_dense_map={"e5": 1.0},
            lex_w_eff=1.0,
            hinted_limit=0,
            ctx_hard_limit=5,
            timings={},
            t_all0=0.0,
            stack="M",
            rerank_spec={"final_keep": 5},
            preset=SimpleNamespace(),
            title_match_mode="contains",
            title_terms=[],
            lookup_title_filter_policy="soft",
            people_terms=None,
            people_ids=None,
            org_terms=None,
            org_role=None,
            intent_payload=None,
            perf_followup_join_ids_count=2,
        ),
        runtime=BaseOrchestrationRuntime(
            qdr=object(),
            logger=SimpleNamespace(),
            retrieve_collections_fn=fake_retrieve_collections,
            get_relation_route_fn=lambda relation: None,
            build_tag_only_filter_fn=lambda tags: ("tag", tuple(tags)),
            and_filter_fn=lambda a, b: (a, b),
            build_project_id_filter_fn=lambda pjt_ids, pjt_nos: ("project_id", pjt_ids, pjt_nos),
            with_org_must_gate_fn=lambda base_filter, **kwargs: ("org_gate", base_filter),
            named_vectors_in_collection_fn=lambda qdr, col: {"e5"},
            call_dense_retrieve_hybrid_multi_fn=lambda **kwargs: {},
            validate_lookup_join_hybrid_metrics_fn=lambda **kwargs: None,
            apply_dense_threshold_fn=lambda *args, **kwargs: None,
            ensure_collection_mark_fn=lambda *args, **kwargs: None,
            log_kv_fn=lambda event, **kwargs: log_events.append((event, kwargs)),
            log_section_fn=lambda *args, **kwargs: None,
            log_top_points_fn=lambda *args, **kwargs: None,
            serialize_filter_for_log_fn=lambda filter_obj: filter_obj,
            resolve_sparse_hits_metric_fn=lambda *args, **kwargs: 0,
            record_col_timings_fn=lambda *args, **kwargs: None,
            rank_source_factory=lambda **kwargs: SimpleNamespace(**kwargs),
            use_dense_score_weight_fn=lambda: False,
            dense_score_weight_fn=lambda points: 1.0,
            rrf_merge_fn=lambda sources, **kwargs: [SimpleNamespace(payload={"doc_id": "merged-1"})],
            dedup_by_doc_id_fn=lambda hits: list(hits),
            timing_put_fn=lambda timings, key, value: timings.__setitem__(key, value),
            finalize_rag_result_fn=fake_finalize,
            resolve_env_topn_fn=lambda *args, **kwargs: 3,
            prepare_title_post_rerank_fn=lambda *args, **kwargs: {},
            final_rerank_fn=lambda *args, **kwargs: [],
            resolve_effective_min_reranked_fn=lambda **kwargs: (0, "none"),
            has_explicit_identifiers_fn=lambda intent: False,
            enforce_reranked_contract_fn=lambda **kwargs: None,
            hydrate_reranked_payloads_fn=lambda **kwargs: None,
            coerce_int_fn=lambda value, default: default,
            get_attr_fn=lambda obj, name, default=None: getattr(obj, name, default),
            hydrate_points_payload_fn=lambda *args, **kwargs: None,
            soft_title_contains_fn=lambda *args, **kwargs: False,
            aggregation_builder_fn=lambda **kwargs: None,
            payload_get_fn=lambda payload, key: payload.get(key),
            hit_key_fn=lambda hit: ("k", "v"),
            title_match_mode_contains="contains",
        ),
        filter_inputs=BaseFilterPolicyInputs(
            relation_lookup_enforce=False,
            lookup_filter_enabled=True,
            title_filter_server_applied=False,
            planner_org_filter_present=False,
            org_role=None,
            org_terms=[],
            people_terms=[],
            people_ids=[],
            has_perf_ids=False,
            pjt_ids=[],
            pjt_nos=[],
            people_filter=None,
            participant_org_filter=None,
            org_filter=None,
            title_filter=None,
            project_tag_filter=None,
            perf_tag_filter="perf_tag",
            perf_type_filter=None,
            year_range_filter=None,
            perf_followup_filter="perf_followup",
            col_project="ntis_project_v1",
            col_perf="ntis_perf_v1",
        ),
        search_policy=BaseSearchPolicyInputs(
            search_filter_enabled=True,
            search_filter_signal="explicit",
            search_filter_conf_ok=True,
            title_filter_server_applied=False,
        ),
    )

    assert result.followup_used is True
    assert result.followup_join_ids_count == 2
    assert seen["finalize"]["query_intent"].ids_map == {}
    assert seen["server_filter"] is not None
    retrieve_events = [payload for event, payload in log_events if event == "RAG.RETRIEVE"]
    assert retrieve_events
    assert retrieve_events[0]["execution_mode"] == "lookup"
    assert retrieve_events[0]["execution_base_route"] == "perf"
    assert retrieve_events[0]["strategy_source"] == "execution_request"
