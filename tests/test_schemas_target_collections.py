from types import SimpleNamespace

from apps.core.schemas import build_query_plan, default_target_collections_for_route


def _intent(**overrides):
    base = dict(
        action="list",
        base_route="people",
        relation=None,
        output_type="list",
        join_key_mode=None,
        stats_metric=None,
        window_years=None,
        candidate_n=None,
        top_k=None,
        tie_break=None,
        is_id_query=False,
        ids_map={},
        people_terms=["kim"],
        lead_org_terms=[],
        participant_org_terms=[],
        people_affiliation_org_terms=[],
        org_terms=[],
        org_role=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_default_target_collections_for_people_include_project_and_perf():
    assert default_target_collections_for_route("people") == ["ntis_project_v1", "ntis_perf_v1"]


def test_default_target_collections_for_org_include_project_and_perf():
    assert default_target_collections_for_route("org") == ["ntis_project_v1", "ntis_perf_v1"]


def test_build_query_plan_keeps_people_route_targeting_project_and_perf():
    plan, reason = build_query_plan(_intent(base_route="people"))
    assert plan.target_collections == ("ntis_project_v1", "ntis_perf_v1")
    assert reason == "people_org_name_lookup"


def test_build_query_plan_keeps_org_route_targeting_project_and_perf():
    plan, reason = build_query_plan(_intent(base_route="org", org_terms=["etri"], people_terms=[]))
    assert plan.target_collections == ("ntis_project_v1", "ntis_perf_v1")
    assert reason == "people_org_name_lookup"
