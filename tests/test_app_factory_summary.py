from __future__ import annotations

from types import SimpleNamespace

from apps.api.app_factory import _state_log_summary_fields


def test_state_log_summary_fields_prefers_execution_strategy():
    state = SimpleNamespace(
        request_id="rid",
        conversation_id="cid",
        question_analysis=SimpleNamespace(
            strategy_version="v3",
            mode="lookup",
            relation=("project", "perf"),
            target_cols=["ntis_project_v1"],
        ),
        strategy=SimpleNamespace(
            mode="join",
            relation=("project", "perf"),
            target_collections=("ntis_project_v1", "ntis_perf_v1"),
            join_key_mode="group",
            join_key_source="hop1",
            join_compile_selection="group_perf_pjt_id_fallback",
            hop2_key_strategy="pjt_id_in",
            resolved_runtime_key_kind="pjt_id",
            join_keys_used_count=1,
            query_graph_kind="project_to_perf",
            anchor_summary={"researcher_count": 1, "project_instance_seed_count": 0},
            anchor_resolution_status="partial",
            ambiguity_codes=("researcher_org_pair_unresolved",),
            resolved_researcher_count=1,
            resolved_org_count=1,
            aggregation_kind="comparison",
            series_kind="pjt_no",
            pattern_kind="perf_mix_gap",
            bundle_kind="project_outputs",
            guidance_required=True,
        ),
        intent_payload=SimpleNamespace(intent_payload_version="v3", strategy_meta={"candidate_project_key_count": 1, "candidate_perf_key_count": 0}),
        context=[{"doc_id": "d1"}],
        merge_debug={"selected_model": "GEMMA"},
        timings={"info.contract_fail_reason": "no_reranked", "info.empty_result_policy": "normal_no_result", "info.reranked_count": 0, "info.aggregation_metric": "paper_count", "info.aggregation_group_by": "project", "info.aggregation_threshold": 2, "info.aggregation_result_count": 1, "info.series_result_count": 2, "info.series_bucket_count": 1, "info.pattern_kind": "perf_mix_gap", "info.pattern_result_count": 1, "info.pattern_subject_count": 1, "info.pattern_support_doc_count": 3, "info.bundle_kind": "project_outputs", "info.bundle_target_count": 2, "info.bundle_project_count": 1, "info.bundle_item_count": 3, "info.guidance_required": 1, "info.failed_step": "aggregation_empty_result", "info.project_key_policy": "ambiguous_or", "info.join_resolution_policy": "auto_resolve", "info.resolved_join_key_mode": "instance", "info.dual_branch_used": 0, "info.candidate_project_key_count": 1},
        rendered_context_used=True,
        degraded=False,
    )

    payload = _state_log_summary_fields(state, total_ms=12)

    assert payload["mode"] == "join"
    assert payload["relation"] == ("project", "perf")
    assert payload["target_cols"] == ["ntis_project_v1", "ntis_perf_v1"]
    assert payload["strategy_source"] == "execution_strategy"
    assert payload["join_compile_selection"] == "group_perf_pjt_id_fallback"
    assert payload["resolved_runtime_key_kind"] == "pjt_id"
    assert payload["planner_mode"] == "lookup"
    assert payload["question_analysis_mode"] == "lookup"
    assert payload["execution_mode"] == "join"
    assert payload["query_graph_kind"] == "project_to_perf"
    assert payload["anchor_summary"]["researcher_count"] == 1
    assert payload["anchor_resolution_status"] == "partial"
    assert payload["ambiguity_codes"] == ["researcher_org_pair_unresolved"]
    assert payload["resolved_researcher_count"] == 1
    assert payload["resolved_org_count"] == 1
    assert payload["aggregation_kind"] == "comparison"
    assert payload["aggregation_metric"] == "paper_count"
    assert payload["aggregation_group_by"] == "project"
    assert payload["aggregation_threshold"] == 2
    assert payload["aggregation_result_count"] == 1
    assert payload["failed_step"] == "aggregation_empty_result"
    assert payload["series_kind"] == "pjt_no"
    assert payload["series_result_count"] == 2
    assert payload["series_bucket_count"] == 1
    assert payload["pattern_kind"] == "perf_mix_gap"
    assert payload["bundle_kind"] == "project_outputs"
    assert payload["bundle_target_count"] == 2
    assert payload["bundle_project_count"] == 1
    assert payload["bundle_item_count"] == 3
    assert payload["guidance_required"] is True
    assert payload["pattern_result_count"] == 1
    assert payload["pattern_subject_count"] == 1
    assert payload["pattern_support_doc_count"] == 3
    assert payload["planner_target_cols"] == ["ntis_project_v1"]
    assert payload["project_key_policy"] == "ambiguous_or"
    assert payload["join_resolution_policy"] == "auto_resolve"
    assert payload["resolved_join_key_mode"] == "instance"
    assert payload["dual_branch_used"] == 0
    assert payload["intent_payload_version"] == "v3"
    assert payload["strategy_version"] == "v3"
    assert payload["candidate_project_key_count"] == 1
    assert payload["candidate_perf_key_count"] == 0
    assert payload["contract_fail_reason"] == "no_reranked"
    assert payload["empty_result_policy"] == "normal_no_result"
    assert payload["reranked_count"] == 0


def test_state_log_summary_fields_falls_back_to_question_analysis_without_strategy():
    state = SimpleNamespace(
        request_id="rid",
        conversation_id="cid",
        question_analysis=SimpleNamespace(
            strategy_version="v3",
            mode="lookup",
            relation=("perf", "project"),
            target_cols=["ntis_perf_v1"],
        ),
        intent_payload=SimpleNamespace(intent_payload_version="v3", strategy_meta={}),
        strategy=None,
        context=[],
        merge_debug={},
        timings={},
        rendered_context_used=False,
        degraded=True,
    )

    payload = _state_log_summary_fields(state, total_ms=5)

    assert payload["mode"] == "lookup"
    assert payload["relation"] == ("perf", "project")
    assert payload["target_cols"] == ["ntis_perf_v1"]
    assert payload["strategy_source"] == "assembled_question_analysis_only"
    assert payload["join_compile_selection"] is None
    assert payload["planner_mode"] == "lookup"
    assert payload["question_analysis_mode"] == "lookup"
    assert payload["execution_mode"] is None
    assert payload["intent_payload_version"] == "v3"
    assert payload["strategy_version"] == "v3"
    assert payload["contract_fail_reason"] is None
    assert payload["empty_result_policy"] is None
