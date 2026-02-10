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
from rag_parts.planner_contract import StrategyViolation


class JoinModeContractTests(unittest.TestCase):
    def test_join_without_join_keys_fails_explicitly_instead_of_hop1_only(self):
        with self.assertRaises(StrategyViolation) as cm:
            rag_pipeline._ensure_join_mode_has_keys(
                has_join_keys=False,
                join_key_mode="instance",
                hop1_top=[],
                hop1_col="ntis_project",
            )

        self.assertEqual(cm.exception.error_code, "JOIN_KEYS_MISSING")
        self.assertIn("requires Hop2 execution", cm.exception.reason)


if __name__ == "__main__":
    unittest.main()
