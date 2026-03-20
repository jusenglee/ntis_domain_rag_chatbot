from types import SimpleNamespace

from apps.api.services.context_build_policy import resolve_output_fieldset, should_use_list_context
from apps.api.services.render_profile import resolve_render_profile
from apps.core.pipeline_steps import normalize_intent
from apps.core.schemas import build_query_plan, derive_resolved_anchor_set, summarize_anchor_set, strategy_spec_to_response, to_strategy_spec


def _intent(**overrides):
    data = dict(
        action="list",
        base_route="project",
        relation=None,
        is_id_query=False,
        output_type="summary",
        ids_map={},
        years=[],
        year_from=None,
        year_to=None,
        keywords=[],
        people_terms=[],
        org_terms=[],
        perf_types=[],
        title=[],
        categories=[],
        planner_limit=None,
        retrieval_query=None,
        planner_confidence=None,
        gender_terms=[],
        org_role=None,
        lead_org_terms=[],
        participant_org_terms=[],
        people_affiliation_org_terms=[],
        perf_tag_filters=[],
        project_tag_filters=[],
        tag_filters=[],
        ids_flat=[],
        remove_terms_for_head=[],
        wants_rank=False,
        wants_count=False,
        wants_list=False,
        wants_detail=False,
        join_key_mode=None,
        target_cols=[],
        lookup_filter_policy=None,
        people_terms_match_mode=None,
        people_terms_min_should=None,
        stats_metric=None,
        window_years=None,
        candidate_n=None,
        top_k=None,
        tie_break=None,
        mode=None,
    )
    data.update(overrides)
    return normalize_intent(SimpleNamespace(**data), query="q", keywords=[])


def test_build_query_plan_adds_project_to_perf_graph_metadata():
    plan, _ = build_query_plan(
        _intent(
            relation=("project", "perf"),
            output_type="relation",
            action="list",
            base_route="project",
        )
    )

    assert plan.query_graph is not None
    assert plan.query_graph.kind == "project_to_perf"
    assert [step.kind for step in plan.query_graph.steps] == ["lookup_projects", "join_project_to_perf"]
    assert plan.aggregation_plan is None
    assert plan.project_series_plan is None


def test_build_query_plan_adds_aggregation_and_temporal_metadata_for_comparison():
    plan, _ = build_query_plan(
        _intent(
            action="stats",
            output_type="comparison",
            year_from="2021",
            year_to="2024",
            people_terms=["shin"],
            top_k=3,
        )
    )

    assert plan.aggregation_plan is not None
    assert plan.aggregation_plan.comparison_mode == "comparison"
    assert plan.aggregation_plan.group_by == "project"
    assert plan.temporal_constraint is not None
    assert plan.temporal_constraint.year_from == "2021"
    assert plan.temporal_constraint.year_to == "2024"
    assert plan.query_graph is not None
    assert plan.query_graph.kind == "aggregate_comparison"


def test_build_query_plan_adds_series_metadata_for_group_seed():
    plan, _ = build_query_plan(
        _intent(
            action="list",
            output_type="series",
            ids_map={"pjt_no": ["PJT-2020-1234-5678"]},
        )
    )

    assert plan.project_series_plan is not None
    assert plan.project_series_plan.series_key_kind == "pjt_no"
    assert plan.query_graph is not None
    assert plan.query_graph.kind == "project_series"


def test_strategy_spec_round_trips_query_graph_metadata():
    spec = to_strategy_spec(
        {
            "mode": "lookup",
            "action": "list",
            "relation": ("project", "perf"),
            "target_collections": ("ntis_project_v1", "ntis_perf_v1"),
            "query_graph_kind": "project_to_perf",
            "anchor_summary": {"researcher_count": 1},
            "anchor_resolution_status": "resolved",
            "ambiguity_codes": ["org_role_unspecified"],
            "resolved_researcher_count": 1,
            "resolved_org_count": 1,
            "aggregation_kind": "comparison",
            "series_kind": "pjt_no",
        }
    )
    payload = strategy_spec_to_response(spec)

    assert payload["query_graph_kind"] == "project_to_perf"
    assert payload["anchor_summary"]["researcher_count"] == 1
    assert payload["anchor_resolution_status"] == "resolved"
    assert payload["ambiguity_codes"] == ["org_role_unspecified"]
    assert payload["resolved_researcher_count"] == 1
    assert payload["resolved_org_count"] == 1
    assert payload["aggregation_kind"] == "comparison"
    assert payload["series_kind"] == "pjt_no"


def test_comparison_and_series_output_types_have_fieldsets_and_profiles():
    assert resolve_output_fieldset("comparison") == ("title_text", "aggregation_keys", "meta_basic", "summary")
    assert resolve_output_fieldset("series") == ("title_text", "year", "relation", "meta_basic", "summary")
    assert should_use_list_context(action="stats", base_route="project", output_type="comparison") is True
    assert should_use_list_context(action="list", base_route="project", output_type="series") is True

    comparison_profile = resolve_render_profile(
        output_type="comparison",
        action="stats",
        base_route="project",
        mode="lookup",
        fieldset=resolve_output_fieldset("comparison"),
        people_terms_present=True,
        person_ids_present=False,
        org_terms_present=False,
        org_role_present=False,
    )
    series_profile = resolve_render_profile(
        output_type="series",
        action="list",
        base_route="perf",
        mode="join",
        fieldset=resolve_output_fieldset("series"),
        people_terms_present=False,
        person_ids_present=False,
        org_terms_present=False,
        org_role_present=False,
    )

    assert comparison_profile.name == "comparison"
    assert comparison_profile.context_kind == "project"
    assert series_profile.name == "series"
    assert series_profile.context_kind == "project"


def test_anchor_summary_tracks_generic_org_and_ambiguities():
    intent = _intent(
        action="list",
        base_route="project",
        people_terms=["shin"],
        org_terms=["KISTI"],
        org_role=None,
    )
    anchor_set = derive_resolved_anchor_set(intent)
    summary = summarize_anchor_set(anchor_set)
    plan, _ = build_query_plan(intent)
    spec = to_strategy_spec(
        {
            "mode": plan.mode,
            "action": plan.action,
            "relation": plan.relation,
            "target_collections": plan.target_collections,
            "query_graph_kind": getattr(plan.query_graph, "kind", None),
            "anchor_summary": summary,
        }
    )
    payload = strategy_spec_to_response(spec)

    assert payload["anchor_summary"]["generic_org_count"] == 1
    assert payload["anchor_summary"]["ambiguity_count"] == 2
    assert "org_role_unspecified" in payload["anchor_summary"]["ambiguities"]

def test_build_query_plan_adds_reverse_trace_graph_metadata():
    plan, _ = build_query_plan(
        _intent(
            relation=("perf", "project"),
            output_type="relation",
            action="list",
            base_route="perf",
            reverse_trace_followup=True,
            followup_relation_hint="origin_project_other_perf",
        )
    )

    assert plan.query_graph is not None
    assert plan.query_graph.kind == "perf_to_project_to_perf"
    assert [step.kind for step in plan.query_graph.steps] == [
        "lookup_perf",
        "join_perf_to_project",
        "followup_project_to_perf",
    ]
    assert plan.reverse_trace_followup is True
    assert plan.followup_relation_hint == "origin_project_other_perf"


def test_anchor_summary_exposes_resolution_status_and_counts():
    intent = _intent(
        action="list",
        base_route="project",
        people_terms=["shin"],
        org_terms=["KISTI"],
    )
    anchor_set = derive_resolved_anchor_set(intent)
    summary = summarize_anchor_set(anchor_set)

    assert anchor_set.resolution_status == "ambiguous"
    assert summary["anchor_resolution_status"] == "ambiguous"
    assert summary["resolved_researcher_count"] == 1
    assert summary["resolved_org_count"] == 1


def test_build_query_plan_adds_pattern_analysis_metadata():
    plan, _ = build_query_plan(
        _intent(
            action="stats",
            base_route="project",
            relation=("project", "perf"),
            pattern_kind="perf_mix_gap",
        )
    )

    assert plan.pattern_analysis_plan is not None
    assert plan.pattern_analysis_plan.kind == "perf_mix_gap"
    assert plan.query_graph is not None
    assert plan.query_graph.kind == "pattern_analysis"


def test_strategy_spec_round_trips_pattern_kind():
    spec = to_strategy_spec({"mode": "lookup", "action": "stats", "pattern_kind": "perf_mix_gap"})
    payload = strategy_spec_to_response(spec)
    assert payload["pattern_kind"] == "perf_mix_gap"


def test_build_query_plan_adds_multi_hop_bundle_metadata():
    plan, _ = build_query_plan(
        _intent(
            action="list",
            base_route="project",
            relation=("project", "perf"),
            output_type="relation",
            bundle_kind="project_outputs",
            bundle_targets=["paper", "patent"],
            guidance_required=True,
        )
    )

    assert plan.multi_hop_bundle_plan is not None
    assert plan.multi_hop_bundle_plan.kind == "project_outputs"
    assert plan.multi_hop_bundle_plan.targets == ("paper", "patent")
    assert plan.query_graph is not None
    assert plan.query_graph.kind == "multi_hop_bundle"


def test_strategy_spec_round_trips_bundle_metadata():
    spec = to_strategy_spec({
        "mode": "lookup",
        "action": "list",
        "bundle_kind": "project_outputs",
        "bundle_targets": ["paper", "patent"],
        "guidance_required": True,
    })
    payload = strategy_spec_to_response(spec)

    assert payload["bundle_kind"] == "project_outputs"
    assert payload["bundle_targets"] == ["paper", "patent"]
    assert payload["guidance_required"] is True
