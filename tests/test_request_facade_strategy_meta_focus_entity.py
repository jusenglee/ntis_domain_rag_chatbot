from types import SimpleNamespace

from apps.conversation.request_facade import _build_strategy_meta


def test_build_strategy_meta_persists_focus_entity():
    normalized_intent = {
        "ids_map": {},
        "candidate_keys": {},
        "project_key_policy": None,
        "join_resolution_policy": None,
        "join_key_mode": None,
        "is_exact_key_query": False,
    }
    qa = SimpleNamespace(strategy_version="v3")
    followup_resolution = {
        "followup_resolution_status": "resolved",
        "focus_entity": {"kind": "project", "source": "display_snapshot", "pjt_id": "2340006682"},
    }

    meta = _build_strategy_meta(normalized_intent, qa, followup_resolution=followup_resolution)

    assert meta["focus_entity"]["pjt_id"] == "2340006682"


def test_build_strategy_meta_marks_anchor_lock_fields():
    normalized_intent = {
        "ids_map": {"pjt_id": ["2340006682"]},
        "candidate_keys": {},
        "project_key_policy": "anchor_locked_pjt_id",
        "join_resolution_policy": None,
        "join_key_mode": None,
        "is_exact_key_query": True,
    }
    qa = SimpleNamespace(strategy_version="v3")

    meta = _build_strategy_meta(normalized_intent, qa, followup_resolution={})

    assert meta["anchor_locked"] is True
    assert meta["anchor_locked_key_kind"] == "pjt_id"
