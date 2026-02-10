import sys
import types
import unittest


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


class LookupJoinHybridMetricValidationTests(unittest.TestCase):
    def test_lookup_hybrid_once_only_passes_without_dense_sparse_breakdown(self):
        rag_pipeline._validate_lookup_join_hybrid_metrics(
            mode="lookup",
            contract_scope="lookup:col_project",
            timings={"dense_queries": 0.0, "lexical_scored": 0.0, "hybrid_once_hits": 3.0},
        )

    def test_join_hybrid_once_only_passes_without_dense_sparse_breakdown(self):
        rag_pipeline._validate_lookup_join_hybrid_metrics(
            mode="join",
            contract_scope="join:hop2:col_performance",
            timings={"dense_queries": 0.0, "lexical_scored": 0.0, "hybrid_once_hits": 1.0},
        )

    def test_fails_when_dense_queries_zero_and_no_hybrid_once_hits(self):
        with self.assertRaisesRegex(rag_pipeline.StrategyViolation, "dense_queries == 0"):
            rag_pipeline._validate_lookup_join_hybrid_metrics(
                mode="lookup",
                contract_scope="lookup:col_project",
                timings={"dense_queries": 0.0, "lexical_scored": 4.0, "hybrid_once_hits": 0.0},
            )

    def test_fails_when_sparse_hits_zero_and_no_hybrid_once_hits(self):
        with self.assertRaisesRegex(rag_pipeline.StrategyViolation, "sparse_hits == 0"):
            rag_pipeline._validate_lookup_join_hybrid_metrics(
                mode="join",
                contract_scope="join:hop2:col_performance",
                timings={"dense_queries": 2.0, "lexical_scored": 0.0, "hybrid_once_hits": 0.0},
            )


if __name__ == "__main__":
    unittest.main()
