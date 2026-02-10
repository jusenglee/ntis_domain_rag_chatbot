import ast
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional


class Server3RetrieverSourceTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path("server3.py").read_text(encoding="utf-8")
        module_ast = ast.parse(source)

        targets = {
            "CustomRAGRetriever",
            "_is_hit_source",
            "_filter_hit_documents",
        }

        snippets = []
        for node in module_ast.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in targets:
                snippets.append(textwrap.dedent(ast.get_source_segment(source, node)))

        cls.namespace: Dict[str, Any] = {
            "BaseModel": object,
            "Optional": Optional,
            "Dict": Dict,
            "Any": Any,
            "List": List,
            "QuestionAnalysisV2": object,
            "run_rag_ab_compare": None,
            "_resolve_title_from_payload": lambda payload: payload.get("title", ""),
        }
        exec("\n\n".join(snippets), cls.namespace)

    def test_retrieve_with_no_hits_returns_empty_documents_and_fallback_context(self):
        retriever_cls = self.namespace["CustomRAGRetriever"]

        def fake_compare(**kwargs):
            return {"M": SimpleNamespace(reranked_hits=[], context="요약 문맥")}

        self.namespace["run_rag_ab_compare"] = fake_compare
        retriever = retriever_cls()
        retriever.model_name = "gemma_vllm_0"
        retriever.top_k = 3
        retriever.intent_payload = None

        result = retriever.retrieve("질문")

        self.assertEqual(result["documents"], [])
        self.assertEqual(result["fallback_context"], "요약 문맥")

    def test_retrieve_hits_are_marked_with_hit_source_type(self):
        retriever_cls = self.namespace["CustomRAGRetriever"]

        def fake_compare(**kwargs):
            return {
                "M": SimpleNamespace(
                    reranked_hits=[
                        {
                            "tag": "IRD_NAI_PJT_INFO",
                            "meta_basic": {"kor_pjt_nm": "과제명"},
                            "meta_detail": {},
                            "prtcp_mp": [],
                        }
                    ],
                    context="",
                )
            }

        self.namespace["run_rag_ab_compare"] = fake_compare
        retriever = retriever_cls()
        retriever.model_name = "gemma_vllm_0"
        retriever.top_k = 3
        retriever.intent_payload = None

        result = retriever.retrieve("질문")

        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(result["documents"][0]["source_type"], "hit")

    def test_retrieve_passes_intent_payload_v2_normalized_only(self):
        retriever_cls = self.namespace["CustomRAGRetriever"]
        captured = {}

        def fake_compare(**kwargs):
            captured["intent_payload"] = kwargs.get("intent_payload")
            return {"M": SimpleNamespace(reranked_hits=[], context="")}

        self.namespace["run_rag_ab_compare"] = fake_compare
        retriever = retriever_cls()
        retriever.intent_payload = {
            "normalized_intent": {"action": "topic"},
            "query_intent": {"action": "legacy"},
            "planner_failed": 1,
        }

        retriever.retrieve("질문")

        self.assertEqual(captured["intent_payload"], {"normalized_intent": {"action": "topic"}})

    def test_filter_hit_documents_excludes_synthetic_docs(self):
        filter_fn = self.namespace["_filter_hit_documents"]

        docs = [
            {"source_type": "hit", "tag": "A"},
            {"source_type": "synthetic", "title": "fallback"},
            {"tag": "B"},
        ]
        filtered = filter_fn(docs)

        self.assertEqual(len(filtered), 2)
        self.assertTrue(all(d.get("source_type", "hit") == "hit" for d in filtered))


if __name__ == "__main__":
    unittest.main()
