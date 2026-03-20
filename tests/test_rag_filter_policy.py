from __future__ import annotations

from apps.core.rag_filter_policy import CollectionFilterPolicyContext, resolve_collection_server_filter


def _ctx(**overrides):
    base = dict(
        mode="lookup",
        base_route="project",
        relation=None,
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
        people_filter="people",
        participant_org_filter="participant_org",
        org_filter="org",
        title_filter="title",
        project_tag_filter="project_tag",
        perf_tag_filter="perf_tag",
        perf_type_filter="perf_type",
        year_range_filter="year_range",
        perf_followup_filter="perf_followup",
        col_project="ntis_project_v1",
        col_perf="ntis_perf_v1",
    )
    base.update(overrides)
    return CollectionFilterPolicyContext(**base)


def _resolve(col, ctx):
    return resolve_collection_server_filter(
        col=col,
        context=ctx,
        get_relation_route_fn=lambda relation: None,
        build_tag_only_filter_fn=lambda tags: ("tag", tuple(tags)),
        and_filter_fn=lambda a, b: (a, b),
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: ("project_id", tuple(pjt_ids), tuple(pjt_nos)) if (pjt_ids or pjt_nos) else None,
        with_org_must_gate_fn=lambda base_filter: ("org_gate", base_filter),
        log_lookup_ids_empty_fn=lambda **kwargs: None,
        log_project_key_filter_fn=lambda **kwargs: None,
    )


def _repr(result):
    return repr(result)


def test_search_returns_no_server_filter():
    result = _resolve("ntis_project_v1", _ctx(mode="search"))
    assert result is None


def test_lookup_project_id_filter_takes_precedence():
    result = _resolve("ntis_project_v1", _ctx(pjt_ids=["1711015550"]))
    result_repr = _repr(result)
    assert "org_gate" in result_repr
    assert "project_id" in result_repr
    assert "1711015550" in result_repr
    assert "project_tag" in result_repr
    assert "year_range" in result_repr


def test_lookup_perf_route_applies_perf_filters():
    result = _resolve("ntis_perf_v1", _ctx(base_route="perf"))
    result_repr = _repr(result)
    assert "org_gate" in result_repr
    assert "perf_tag" in result_repr
    assert "perf_type" in result_repr
    assert "perf_followup" in result_repr
    assert "year_range" in result_repr


def test_relation_lookup_filter_applies_only_when_route_matches():
    ctx = _ctx(relation=("project", "perf"), relation_lookup_enforce=True)
    project_result = resolve_collection_server_filter(
        col="ntis_project_v1",
        context=ctx,
        get_relation_route_fn=lambda relation: type("Route", (), {"hop1_col": "ntis_project_v1", "hop2_col": "ntis_perf_v1", "hop1_tag_filters": ["hop1"], "hop2_tag_filters": ["hop2"], "relation": relation})(),
        build_tag_only_filter_fn=lambda tags: ("tag", tuple(tags)),
        and_filter_fn=lambda a, b: (a, b),
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: None,
        with_org_must_gate_fn=lambda base_filter: base_filter,
        log_lookup_ids_empty_fn=lambda **kwargs: None,
        log_project_key_filter_fn=lambda **kwargs: None,
    )
    perf_result = resolve_collection_server_filter(
        col="ntis_perf_v1",
        context=ctx,
        get_relation_route_fn=lambda relation: type("Route", (), {"hop1_col": "ntis_project_v1", "hop2_col": "ntis_perf_v1", "hop1_tag_filters": ["hop1"], "hop2_tag_filters": ["hop2"], "relation": relation})(),
        build_tag_only_filter_fn=lambda tags: ("tag", tuple(tags)),
        and_filter_fn=lambda a, b: (a, b),
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: None,
        with_org_must_gate_fn=lambda base_filter: base_filter,
        log_lookup_ids_empty_fn=lambda **kwargs: None,
        log_project_key_filter_fn=lambda **kwargs: None,
    )
    assert "hop1" in _repr(project_result)
    assert "hop2" not in _repr(project_result)
    assert "hop2" in _repr(perf_result)
    assert "hop1" not in _repr(perf_result)


def test_org_gate_still_wraps_soft_lookup_filter_when_lookup_filters_disabled():
    result = _resolve("ntis_project_v1", _ctx(lookup_filter_enabled=False, planner_org_filter_present=True, org_terms=["ETRI"]))
    result_repr = _repr(result)
    assert "org_gate" in result_repr
    assert "IRD_NAI_PJT_INFO" in result_repr
    assert "year_range" in result_repr
