from types import SimpleNamespace

from apps.api.services.rag_retriever import _resolve_followup_anchor_context
from apps.api.services.view_state import ConversationViewState, FocusEntity
from apps.core.canonical_evidence import build_canonical_evidence
from apps.core.pipeline_steps import NormalizedIntent


class Payload:
    def __init__(self, normalized_intent, strategy_meta=None):
        self.normalized_intent = normalized_intent
        self.strategy_meta = strategy_meta or {}


def test_anchor_context_prefers_ids_map_without_resolved_status():
    state = SimpleNamespace(
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={"pjt_id": ["2340006682"]},
            ),
            strategy_meta={"followup_resolution_status": "none"},
        ),
        view_state=ConversationViewState(),
    )

    context = _resolve_followup_anchor_context(state)

    assert context["present"] is True
    assert context["pjt_id"] == "2340006682"
    assert context["entity_key"] == "2340006682"


def test_anchor_context_falls_back_to_latest_focus_entity_before_strategy_meta():
    state = SimpleNamespace(
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={},
            ),
            strategy_meta={
                "focus_entity": {"kind": "project", "source": "display_snapshot", "pjt_id": "OLD-ID"},
                "selected_prev_item": {"pjt_id": "OLDER-ID", "title": "Old Project"},
            },
        ),
        view_state=ConversationViewState(
            latest_focus_entity=FocusEntity(kind="project", source="detail_lookup", pjt_id="NEW-ID", title_text="New Project")
        ),
    )

    context = _resolve_followup_anchor_context(state)

    assert context["present"] is True
    assert context["pjt_id"] == "NEW-ID"
    assert context["title_text"] == "New Project"



def test_build_canonical_evidence_collects_followup_seed_ids():
    item = build_canonical_evidence(
        {
            "title_text": "sample",
            "prtcp_mp": [{"person_no": "PERSON-1", "blng_org_nm": "Org Alpha"}],
            "prtcp_org": [{"org_id": "ORG-1", "org_code": "ORG-CODE", "biz_no": "123-45-67890"}],
            "doi": "10.1234/example",
            "issn": "1225-0000",
        },
        rank=1,
        base_route="project",
        output_type="detail",
    ).to_dict()

    assert item["ids"]["person_no"] == "PERSON-1"
    assert item["ids"]["org_id"] == "ORG-1"
    assert item["ids"]["org_code"] == "ORG-CODE"
    assert item["ids"]["biz_no"] == "123-45-67890"
    assert item["ids"]["doi"] == "10.1234/example"
    assert item["ids"]["issn"] == "1225-0000"
