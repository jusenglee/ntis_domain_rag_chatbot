from types import SimpleNamespace

from apps.retrieval.rag_retriever import has_active_anchor_seed


def test_detail_cache_gate_requires_active_anchor_seed():
    class DummyState:
        pass

    state = DummyState()
    state.view_state = type(
        "ViewState",
        (),
        {
            "active_scope": type(
                "ActiveScope",
                (),
                {
                    "focus": type(
                        "Focus",
                        (),
                        {"pjt_id": "2340006682", "pjt_no": "2024S1A5A8021180"},
                    )(),
                    "child_anchor": None,
                },
            )()
        },
    )()
    state.intent_payload = type(
        "Payload",
        (),
        {
            "normalized_intent": type(
                "Intent",
                (),
                {"ids_map": {}, "action": "detail", "output_type": "detail"},
            )(),
            "strategy_meta": {
                "followup_resolution_status": "none",
                "explicit_followup": False,
                "anchor_source": None,
            },
        },
    )()

    assert has_active_anchor_seed(state) is False
