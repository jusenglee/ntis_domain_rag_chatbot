import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


if "qdrant_client" not in sys.modules:
    qdrant_mod = types.ModuleType("qdrant_client")
    qdrant_mod.QdrantClient = object
    sys.modules["qdrant_client"] = qdrant_mod

if "qdrant_client.http" not in sys.modules:
    qdrant_http_mod = types.ModuleType("qdrant_client.http")

    class MatchAny:
        def __init__(self, any=None, any_values=None):
            self.any = any if any is not None else any_values

    class MatchValue:
        def __init__(self, value):
            self.value = value

    class FieldCondition:
        def __init__(self, key, match=None, range=None):
            self.key = key
            self.match = match
            self.range = range

    class Filter:
        def __init__(self, must=None, should=None, must_not=None, min_should=None):
            self.must = must
            self.should = should
            self.must_not = must_not
            self.min_should = min_should

    class Nested:
        def __init__(self, key, filter):
            self.key = key
            self.filter = filter

    class NestedCondition:
        def __init__(self, nested):
            self.nested = nested

    qdrant_http_mod.models = types.SimpleNamespace(
        MatchAny=MatchAny,
        MatchValue=MatchValue,
        FieldCondition=FieldCondition,
        Filter=Filter,
        Nested=Nested,
        NestedCondition=NestedCondition,
    )
    sys.modules["qdrant_client.http"] = qdrant_http_mod

if "triton_client" not in sys.modules:
    triton_mod = types.ModuleType("triton_client")
    triton_mod.get_triton_client = lambda: None
    sys.modules["triton_client"] = triton_mod

if "llama_index.embeddings.huggingface" not in sys.modules:
    li_mod = types.ModuleType("llama_index")
    li_embed_mod = types.ModuleType("llama_index.embeddings")
    li_hf_mod = types.ModuleType("llama_index.embeddings.huggingface")
    li_hf_mod.HuggingFaceEmbedding = object
    sys.modules["llama_index"] = li_mod
    sys.modules["llama_index.embeddings"] = li_embed_mod
    sys.modules["llama_index.embeddings.huggingface"] = li_hf_mod

import rag_pipeline


def _collect_filter_keys(filter_obj):
    keys = set()

    def walk(node):
        if node is None:
            return
        key = getattr(node, "key", None)
        if isinstance(key, str):
            keys.add(key)
        nested = getattr(node, "nested", None)
        if nested is not None:
            walk(getattr(nested, "filter", None))
        for attr in ("must", "should", "must_not"):
            for child in list(getattr(node, attr, None) or []):
                walk(child)

    walk(filter_obj)
    return keys


class OrgFilterQueryFilterGateTests(unittest.TestCase):
    def _run_and_capture_filters(self, *, hint):
        captured_filters = []

        def _fake_retrieve(**kwargs):
            captured_filters.append(kwargs.get("query_filter"))
            return {"dense": {}, "lexical": [], "hybrid": []}

        with patch.object(rag_pipeline, "build_rag_objects", return_value=SimpleNamespace(qdrant_client=object(), embed_e5i=None, embed_e5=None)), \
             patch.object(rag_pipeline, "_named_vectors_in_collection", return_value=set()), \
             patch.object(rag_pipeline, "_call_dense_retrieve_hybrid_multi", side_effect=_fake_retrieve), \
             patch.object(rag_pipeline, "_validate_lookup_join_hybrid_metrics", return_value=None), \
             patch.object(rag_pipeline, "_enforce_reranked_contract", return_value=None):
            rag_pipeline._run_rag_with_vectors(
                query="테스트 질의",
                model_name="gpt-4o-mini",
                hint=hint,
                intent_payload=None,
                stack="test",
                vector_names=[],
                w_dense_map={},
            )

        return captured_filters

    def test_lookup_with_org_filter_without_ids_always_sets_org_condition(self):
        filters = self._run_and_capture_filters(
            hint={
                "mode": "LOOKUP",
                "head": "project",
                "action": "list",
                "filters": {"lead_org_name": ["한국전자통신연구원"]},
                "confidence": 0.99,
            }
        )
        self.assertTrue(filters)
        self.assertTrue(all(f is not None for f in filters))
        self.assertTrue(any("org_nm" in _collect_filter_keys(f) for f in filters))

    def test_relation_lookup_and_join_always_include_org_condition(self):
        lookup_filters = self._run_and_capture_filters(
            hint={
                "mode": "LOOKUP",
                "head": "project",
                "action": "relation",
                "relation": "people_project",
                "filters": {"participant_org_name": ["카이스트"]},
                "confidence": 0.99,
            }
        )
        join_filters = self._run_and_capture_filters(
            hint={
                "mode": "JOIN",
                "head": "project",
                "action": "relation",
                "relation": "project_perf",
                "join_key_mode": "instance",
                "ids_map": {"pjt_id": ["12345678"]},
                "filters": {"participant_org_name": ["카이스트"]},
                "confidence": 0.99,
            }
        )

        self.assertTrue(lookup_filters)
        self.assertTrue(join_filters)
        self.assertTrue(any("org_nm" in _collect_filter_keys(f) or "blng_org_nm" in _collect_filter_keys(f) for f in lookup_filters if f is not None))
        self.assertTrue(any("org_nm" in _collect_filter_keys(f) or "blng_org_nm" in _collect_filter_keys(f) for f in join_filters if f is not None))


    def test_lookup_mixed_project_keys_fail_fast_without_correction(self):
        hint = {
            "mode": "LOOKUP",
            "head": "project",
            "action": "list",
            "ids_map": {"pjt_id": ["12345678"], "pjt_no": ["PJT-2024-0001"]},
            "confidence": 0.99,
        }

        with patch.object(rag_pipeline, "build_rag_objects", return_value=SimpleNamespace(qdrant_client=object(), embed_e5i=None, embed_e5=None)):
            with self.assertRaises(rag_pipeline.StrategyViolation) as cm:
                rag_pipeline._run_rag_with_vectors(
                    query="테스트 질의",
                    model_name="gpt-4o-mini",
                    hint=hint,
                    intent_payload=None,
                    stack="test",
                    vector_names=[],
                    w_dense_map={},
                )

        self.assertEqual(cm.exception.error_code, "PLANNER_MIXED_PROJECT_KEYS")

    def test_join_mixed_project_keys_fail_fast_without_correction(self):
        hint = {
            "mode": "JOIN",
            "head": "project",
            "action": "join",
            "relation": "project_perf",
            "join_key_mode": "instance",
            "ids_map": {"pjt_id": ["12345678"], "pjt_no": ["PJT-2024-0001"]},
            "confidence": 0.99,
        }

        with patch.object(rag_pipeline, "build_rag_objects", return_value=SimpleNamespace(qdrant_client=object(), embed_e5i=None, embed_e5=None)):
            with self.assertRaises(rag_pipeline.StrategyViolation) as cm:
                rag_pipeline._run_rag_with_vectors(
                    query="테스트 질의",
                    model_name="gpt-4o-mini",
                    hint=hint,
                    intent_payload=None,
                    stack="test",
                    vector_names=[],
                    w_dense_map={},
                )

        self.assertEqual(cm.exception.error_code, "PLANNER_JOIN_MIXED_PROJECT_KEYS")


if __name__ == "__main__":
    unittest.main()
