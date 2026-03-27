from apps.core.filters import PeopleFilterInput, build_people_filter
from apps.core.rag_filter_policy import CollectionFilterPolicyContext, resolve_collection_server_filter


COL_PROJECT = "ntis_project_v1"
COL_PERF = "ntis_perf_v1"


def _and_filter(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return ("AND", left, right)


def _with_org_gate(value):
    return value


def _flatten_terms(value):
    if value is None:
        return []
    if isinstance(value, tuple):
        out = []
        for item in value:
            out.extend(_flatten_terms(item))
        return out
    return [value]


def test_people_lookup_project_filter_keeps_people_axis_without_ids():
    context = CollectionFilterPolicyContext(
        mode="lookup",
        base_route="people",
        relation=None,
        relation_lookup_enforce=False,
        lookup_filter_enabled=True,
        title_filter_server_applied=False,
        planner_org_filter_present=False,
        org_role=None,
        org_terms=[],
        people_terms=["신동구"],
        people_ids=[],
        has_perf_ids=False,
        pjt_ids=[],
        pjt_nos=[],
        people_filter=("PEOPLE", "신동구"),
        participant_org_filter=None,
        org_filter=None,
        title_filter=None,
        project_tag_filter=None,
        perf_tag_filter=None,
        perf_type_filter=None,
        year_range_filter=None,
        perf_followup_filter=None,
        col_project=COL_PROJECT,
        col_perf=COL_PERF,
    )

    result = resolve_collection_server_filter(
        col=COL_PROJECT,
        context=context,
        get_relation_route_fn=lambda relation: None,
        build_tag_only_filter_fn=lambda tags: ("TAG", tuple(tags)),
        and_filter_fn=_and_filter,
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: None,
        with_org_must_gate_fn=_with_org_gate,
        log_lookup_ids_empty_fn=lambda **kwargs: None,
        log_project_key_filter_fn=lambda **kwargs: None,
    )

    flattened = _flatten_terms(result)
    assert "PEOPLE" in flattened
    assert "신동구" in flattened
    assert "IRD_NAI_PJT_INFO" in flattened


def test_people_lookup_perf_filter_keeps_people_and_org_axis_without_ids():
    context = CollectionFilterPolicyContext(
        mode="lookup",
        base_route="people",
        relation=None,
        relation_lookup_enforce=False,
        lookup_filter_enabled=True,
        title_filter_server_applied=False,
        planner_org_filter_present=False,
        org_role="affiliation_org",
        org_terms=["한국과학기술정보연구원"],
        people_terms=["신동구"],
        people_ids=[],
        has_perf_ids=False,
        pjt_ids=[],
        pjt_nos=[],
        people_filter=("PEOPLE", "신동구"),
        participant_org_filter=("PARTICIPANT_ORG", "한국과학기술정보연구원"),
        org_filter=None,
        title_filter=None,
        project_tag_filter=None,
        perf_tag_filter=("PERF_TAG",),
        perf_type_filter=None,
        year_range_filter=None,
        perf_followup_filter=None,
        col_project=COL_PROJECT,
        col_perf=COL_PERF,
    )

    result = resolve_collection_server_filter(
        col=COL_PERF,
        context=context,
        get_relation_route_fn=lambda relation: None,
        build_tag_only_filter_fn=lambda tags: ("TAG", tuple(tags)),
        and_filter_fn=_and_filter,
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: None,
        with_org_must_gate_fn=_with_org_gate,
        log_lookup_ids_empty_fn=lambda **kwargs: None,
        log_project_key_filter_fn=lambda **kwargs: None,
    )

    flattened = _flatten_terms(result)
    assert "PEOPLE" in flattened
    assert "신동구" in flattened
    assert "PARTICIPANT_ORG" in flattened
    assert "한국과학기술정보연구원" in flattened
    assert "PERF_TAG" in flattened


def test_build_people_filter_uses_same_nested_object_conjunctive_semantics_for_name_and_affiliation():
    result = build_people_filter(
        PeopleFilterInput(
            people_terms=["신동구"],
            org_terms=["한국과학기술정보연구원"],
        )
    )

    nested = result.must[0].nested.filter
    assert nested.should is None
    assert len(nested.must) == 2
    assert getattr(nested.must[0], "key", None) == "hm_nm"

    org_gate = nested.must[1]
    assert org_gate.must is not None
    assert len(org_gate.must) == 1
    assert org_gate.must[0].should is not None
    assert all(getattr(cond, "key", None) == "blng_org_nm" for cond in org_gate.must[0].should)
