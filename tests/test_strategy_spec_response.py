from __future__ import annotations

from types import SimpleNamespace

from apps.core.schemas import strategy_spec_to_response, to_strategy_spec


def test_to_strategy_spec_normalizes_join_runtime_meta_fields():
    spec = to_strategy_spec(
        {
            "mode": "join",
            "action": "list",
            "relation": ("project", "perf"),
            "join_key_mode": "group",
            "join_key_source": "hop1",
            "hop1_mode": "lookup",
            "join_compile_selection": "GROUP_PERF_PJT_ID_FALLBACK",
            "hop2_key_strategy": "PJT_ID_IN",
            "resolved_runtime_key_kind": "PJT_ID",
            "join_keys_used_count": 3,
        }
    )

    assert spec.join_compile_selection == "group_perf_pjt_id_fallback"
    assert spec.hop2_key_strategy == "pjt_id_in"
    assert spec.resolved_runtime_key_kind == "pjt_id"
    assert spec.join_keys_used_count == 3


def test_strategy_spec_to_response_includes_join_runtime_meta_fields():
    raw = SimpleNamespace(
        mode="join",
        action="list",
        relation=("project", "perf"),
        join_key_mode="group",
        join_key_source="hop1",
        hop1_mode="lookup",
        join_compile_selection="group_perf_pjt_id_fallback",
        hop2_key_strategy="pjt_id_in",
        resolved_runtime_key_kind="pjt_id",
        join_keys_used_count=2,
        people_terms=(),
        target_collections=("ntis_project_v1", "ntis_perf_v1"),
        search_filter_enabled=False,
        lookup_filter_enabled=True,
        relation_lookup_enforce=False,
        lookup_filter_policy="soft",
        lookup_filter_min_should=None,
        lookup_filter_gate=None,
        lookup_filter_promote_one_must=False,
        lookup_title_filter_policy=None,
        title_match_mode=None,
        search_filter_server_policy=None,
    )

    response = strategy_spec_to_response(raw)

    assert response["join_compile_selection"] == "group_perf_pjt_id_fallback"
    assert response["hop2_key_strategy"] == "pjt_id_in"
    assert response["resolved_runtime_key_kind"] == "pjt_id"
    assert response["join_keys_used_count"] == 2
