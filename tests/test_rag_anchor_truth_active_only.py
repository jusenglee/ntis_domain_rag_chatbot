from types import SimpleNamespace

from apps.api.services.rag_retriever import get_followup_anchor_context, has_active_anchor_seed


def test_active_only_ignores_stale_latest_focus_without_followup_signal():
    state = SimpleNamespace(
        view_state=SimpleNamespace(
            latest_focus_entity=SimpleNamespace(
                kind="project",
                source="detail_lookup",
                pjt_id="2340006682",
                pjt_no="2024S1A5A8021180",
                title_text="old focus",
            )
        ),
        intent_payload=SimpleNamespace(
            normalized_intent=SimpleNamespace(ids_map={}, action="detail", output_type="detail"),
            strategy_meta={
                "followup_resolution_status": "none",
                "explicit_followup": False,
                "anchor_source": None,
                "focus_entity": {},
                "selected_prev_item": {},
            },
        ),
    )

    assert has_active_anchor_seed(state) is False
    ctx = get_followup_anchor_context(state, active_only=True)
    assert ctx["present"] is False


def test_active_only_uses_ids_map_seed_even_without_latest_focus():
    state = SimpleNamespace(
        view_state=SimpleNamespace(latest_focus_entity=None),
        intent_payload=SimpleNamespace(
            normalized_intent=SimpleNamespace(
                ids_map={"pjt_id": ["2340006682"]},
                action="detail",
                output_type="detail",
            ),
            strategy_meta={
                "followup_resolution_status": "none",
                "explicit_followup": False,
                "anchor_source": None,
                "focus_entity": {},
                "selected_prev_item": {},
            },
        ),
    )

    assert has_active_anchor_seed(state) is True
    ctx = get_followup_anchor_context(state, active_only=True)
    assert ctx["present"] is True
    assert ctx["pjt_id"] == "2340006682"


def test_followup_anchor_context_keeps_reference_kind_for_source_reference_seed():
    state = SimpleNamespace(
        view_state=SimpleNamespace(latest_focus_entity=None),
        intent_payload=SimpleNamespace(
            normalized_intent=SimpleNamespace(
                ids_map={"pjt_id": ["PJT-222"]},
                action="detail",
                output_type="detail",
            ),
            strategy_meta={
                "followup_resolution_status": "resolved",
                "explicit_followup": True,
                "anchor_source": None,
                "seed_source": "reference_context_source_reference",
                "anchor_reference_kind": "source_reference",
                "focus_entity": {},
                "selected_prev_item": {},
            },
        ),
    )

    ctx = get_followup_anchor_context(state, active_only=True)

    assert ctx["present"] is True
    assert ctx["anchor_source"] == "reference_context_source_reference"
    assert ctx["anchor_reference_kind"] == "source_reference"
