from __future__ import annotations

import asyncio
from types import SimpleNamespace

from apps.api.services.retrieval_workflow import node_knowledge_sufficiency


class FakeParser:
    def __init__(self, pydantic_object):
        self.pydantic_object = pydantic_object

    def get_format_instructions(self):
        return "json"


class FakePrompt:
    captured = None

    @classmethod
    def from_messages(cls, messages):
        return cls()

    def __or__(self, other):
        return FakeChain()


class FakeChain:
    def __or__(self, other):
        return self

    async def ainvoke(self, payload):
        FakePrompt.captured = payload
        return SimpleNamespace(
            requires_new_knowledge="low",
            search_intent="prev context enough",
            retrieval_query="cached",
            confidence=0.9,
        )


class KS:
    def __init__(self, requires_new_knowledge, search_intent, retrieval_query, confidence):
        self.requires_new_knowledge = requires_new_knowledge
        self.search_intent = search_intent
        self.retrieval_query = retrieval_query
        self.confidence = confidence


def test_knowledge_sufficiency_prefers_canonical_prev_context():
    state = SimpleNamespace(
        chat_history=[],
        question_analysis=SimpleNamespace(
            retrieval_query="cached",
            mode="LOOKUP",
            action="summary",
            filters={},
            ids_map={},
            head="project",
            output_type="summary",
        ),
        messages=[SimpleNamespace(content="cached context question")],
        prev_context=[
            {
                "pjt_id": "1711015550",
                "pjt_no": "PJT-2020-1234-5678",
                "org_nm": "ETRI",
                "title_text": "AI related project",
                "summary": "project summary",
            }
        ],
        intent_payload=SimpleNamespace(normalized_intent=SimpleNamespace(action="summary")),
        request_id="rid",
        conversation_id="cid",
    )

    result = asyncio.run(
        node_knowledge_sufficiency(
            state,
            build_llm_fn=lambda model_name: object(),
            pydantic_output_parser_cls=FakeParser,
            chat_prompt_template_cls=FakePrompt,
            knowledge_sufficiency_cls=KS,
            sanitize_llm_json_fn=object(),
            refine_documents_rule_based_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy prev-context renderer must not run")),
            priority_context_fields=(),
            max_field_sentences=3,
            max_field_tokens=120,
            default_max_doc_sentences=3,
            default_max_doc_tokens=120,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
        )
    )

    assert result["knowledge_sufficiency"].requires_new_knowledge == "low"
    assert "PJT_ID=1711015550" in FakePrompt.captured["prev_context"]
    assert "LEGACY_PREV_CONTEXT" not in FakePrompt.captured["prev_context"]


def test_knowledge_sufficiency_prefers_render_profile_and_normalized_intent_over_question_analysis():
    state = SimpleNamespace(
        chat_history=[],
        question_analysis=SimpleNamespace(
            retrieval_query="qa-query",
            mode="LOOKUP",
            action="summary",
            filters={},
            ids_map={},
            head="project",
            output_type="summary",
        ),
        strategy=SimpleNamespace(mode="join"),
        render_profile={"name": "relation", "context_kind": "perf"},
        messages=[SimpleNamespace(content="relation followup")],
        prev_context=[
            {
                "pjt_id": "1711015550",
                "pjt_no": "PJT-2020-1234-5678",
                "org_nm": "ETRI",
                "title_text": "AI related project",
                "summary": "project summary",
            }
        ],
        intent_payload=SimpleNamespace(
            normalized_intent=SimpleNamespace(
                action="summary",
                retrieval_query="intent-query",
                base_route="perf",
                output_type="relation",
            )
        ),
        request_id="rid",
        conversation_id="cid",
    )

    result = asyncio.run(
        node_knowledge_sufficiency(
            state,
            build_llm_fn=lambda model_name: object(),
            pydantic_output_parser_cls=FakeParser,
            chat_prompt_template_cls=FakePrompt,
            knowledge_sufficiency_cls=KS,
            sanitize_llm_json_fn=object(),
            refine_documents_rule_based_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy prev-context renderer must not run")),
            priority_context_fields=(),
            max_field_sentences=3,
            max_field_tokens=120,
            default_max_doc_sentences=3,
            default_max_doc_tokens=120,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
        )
    )

    assert result["knowledge_sufficiency"].retrieval_query == "cached"
    assert "[RenderProfile] name=relation kind=perf" in FakePrompt.captured["prev_context"]
