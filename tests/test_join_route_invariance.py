import sys
import types
import unittest


# rag_pipeline import 의존성 최소 스텁
if "qdrant_client" not in sys.modules:
    qdrant_mod = types.ModuleType("qdrant_client")
    qdrant_mod.QdrantClient = object
    sys.modules["qdrant_client"] = qdrant_mod
if "qdrant_client.http" not in sys.modules:
    qdrant_http_mod = types.ModuleType("qdrant_client.http")
    qdrant_http_mod.models = types.SimpleNamespace()
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


class JoinRouteInvarianceTests(unittest.TestCase):
    def test_relation_join_without_ids_requires_hop1_hop2(self):
        policy = rag_pipeline._resolve_join_execution_policy(
            relation=("project", "perf"),
            mode="join",
            action="relation",
            has_relation_join_ids=False,
        )

        self.assertTrue(policy["required"])
        self.assertEqual(policy["reason"], "hop1_key_extraction_required")

    def test_relation_join_with_ids_still_required(self):
        policy = rag_pipeline._resolve_join_execution_policy(
            relation=("project", "perf"),
            mode="join",
            action="relation",
            has_relation_join_ids=True,
        )

        self.assertTrue(policy["required"])
        self.assertEqual(policy["reason"], "seed_ids_present")

    def test_topic_action_with_forced_join_mode_is_required(self):
        policy = rag_pipeline._resolve_join_execution_policy(
            relation=("project", "perf"),
            mode="join",
            action="topic",
            has_relation_join_ids=False,
        )

        self.assertTrue(policy["required"])
        self.assertEqual(policy["action"], "topic")
        self.assertEqual(policy["reason"], "hop1_key_extraction_required")


if __name__ == "__main__":
    unittest.main()
