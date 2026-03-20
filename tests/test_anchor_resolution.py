from __future__ import annotations

from types import SimpleNamespace

from apps.core.anchor_resolution import build_anchor_execution_inputs
from apps.core.pipeline_steps import normalize_intent


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


def test_anchor_execution_inputs_keep_generic_org_ambiguity_visible():
    inputs = build_anchor_execution_inputs(
        _intent(people_terms=["신동구"], org_terms=["KISTI"], org_role=None)
    )

    assert inputs.anchor_set.resolution_status == "ambiguous"
    assert inputs.people_terms == ["신동구"]
    assert inputs.org_terms == ["KISTI"]
    assert inputs.lead_org_terms == []
    assert inputs.participant_org_terms == []
    assert inputs.people_affiliation_org_terms == []
    assert inputs.planner_org_filter_present is True


def test_anchor_execution_inputs_route_generic_org_to_requested_role():
    inputs = build_anchor_execution_inputs(
        _intent(people_terms=["신동구"], org_terms=["KISTI"], org_role="participant")
    )

    assert inputs.org_terms == []
    assert inputs.participant_org_terms == ["KISTI"]
    assert inputs.org_role == "participant"
    assert "researcher_org_pair_unresolved" not in inputs.anchor_set.ambiguities

