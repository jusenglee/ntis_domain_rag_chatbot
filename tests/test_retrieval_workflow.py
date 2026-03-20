from __future__ import annotations

import asyncio
from types import SimpleNamespace

from apps.api.services.retrieval_workflow import node_rag_search


class _Tool:
    def __init__(self, *, name, description, func):
        self.name = name
        self.description = description
        self.func = func


def test_node_rag_search_prefers_normalized_intent_planner_limit():
    seen = {}

    class _Retriever:
        def __init__(self, *, top_k, model_name, intent_payload):
            seen["top_k"] = top_k
            seen["model_name"] = model_name
            seen["intent_payload"] = intent_payload

        def retrieve(self, query):
            seen["query"] = query
            return {
                "documents": [{"doc_id": "d1"}],
                "canonical_evidence": [{"identity": "1711015550"}],
                "render_profile": {"name": "detail", "context_kind": "project"},
            }

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="ks-query", confidence=0.9),
        question_analysis=SimpleNamespace(limit=99, retrieval_query="qa-query", confidence=0.1),
        intent_payload=SimpleNamespace(normalized_intent=SimpleNamespace(planner_limit=7)),
        request_id="rid",
        conversation_id="cid",
    )

    result = asyncio.run(
        node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: ("raw", "hint", "search-query", 0.9),
            custom_rag_retriever_cls=_Retriever,
            tool_cls=_Tool,
            max_top_k_size=20,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
        )
    )

    assert seen["top_k"] == 7
    assert seen["query"] == "search-query"
    assert result["render_profile"]["name"] == "detail"
