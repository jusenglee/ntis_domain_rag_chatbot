from types import SimpleNamespace

from apps.api.services.request_facade import _apply_anchor_lock, _apply_followup_context_lock, _merge_seed_into_ids_map


def test_merge_seed_prefers_pjt_id_over_pjt_no():
    ids_map = {}
    seed_map = {"pjt_id": ["2340006682"], "pjt_no": ["2024S1A5A8021180"]}

    merged = _merge_seed_into_ids_map(ids_map, seed_map)

    assert merged["pjt_id"] == ["2340006682"]
    assert "pjt_no" not in merged


def test_apply_anchor_lock_sets_anchor_locked_pjt_id_policy():
    intent = {"ids_map": {}, "project_key_policy": None}

    locked = _apply_anchor_lock(intent, {"pjt_id": ["2340006682"]})

    assert locked["ids_map"] == {"pjt_id": ["2340006682"]}
    assert locked["project_key_policy"] == "anchor_locked_pjt_id"


def test_apply_anchor_lock_sets_anchor_locked_pjt_no_policy():
    intent = SimpleNamespace(ids_map={}, project_key_policy=None)

    locked = _apply_anchor_lock(intent, {"pjt_no": ["2024S1A5A8021180"]})

    assert locked.ids_map == {"pjt_no": ["2024S1A5A8021180"]}
    assert locked.project_key_policy == "anchor_locked_pjt_no"


def test_resolved_perf_followup_restores_perf_route_after_planner_merge():
    followup_resolution = {
        "followup_resolution_status": "resolved",
        "selected_prev_context_kind": "perf",
        "selected_prev_item": {
            "context_kind": "perf",
            "rst_id": "REP-2014-0118236988",
            "pjt_id": "1415128833",
        },
        "seed_map": {
            "rst_id": ["REP-2014-0118236988"],
            "pjt_id": ["1415128833"],
        },
    }

    planner_output = {
        "base_route": "project",
        "target_cols": ["ntis_project_v1"],
        "action": "detail",
        "ids_map": {"rst_id": ["REP-2014-0118236988"], "pjt_id": ["1415128833"]},
    }

    patched = _apply_followup_context_lock(planner_output, followup_resolution)

    assert patched["base_route"] == "project"
    assert patched["target_cols"] == ["ntis_project_v1"]
    assert patched["context_owner_lock"] == "perf"
    assert patched["context_owner_lock_reason"] == "followup_context_perf"
    assert patched["ids_map"]["rst_id"] == ["REP-2014-0118236988"]

