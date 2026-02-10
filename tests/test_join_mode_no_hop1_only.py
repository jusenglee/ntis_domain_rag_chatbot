import sys
import types
import unittest
from unittest.mock import patch


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


    def test_missing_hop1_keys_never_calls_hop2_and_uses_standard_error_code(self):
        with patch("rag_pipeline._build_join_hop2_filter") as mock_hop2:
            with self.assertRaises(StrategyViolation) as cm:
                # 회귀 가드: Hop1 hit가 있어도 JOIN key(pjt_id/pjt_no)가 없으면 Hop2 진입 금지
                rag_pipeline._ensure_join_mode_has_keys(
                    has_join_keys=False,
                    join_key_mode="instance",
                    hop1_top=[types.SimpleNamespace(payload={"title": "hop1-hit-without-join-key"})],
                    hop1_col="ntis_project",
                    join_pjt_ids_count=0,
                    join_pjt_nos_count=0,
                )

                # 아래 라인은 실행되면 안 된다(가드가 실패를 던져 Hop2를 차단해야 함)
                rag_pipeline._build_join_hop2_filter(
                    relation=("project", "perf"),
                    join_key_mode="instance",
                    join_pjt_ids=[],
                    join_pjt_nos=[],
                    join_ids=[],
                    q="성과",
                    hop2_tag_filters=None,
                    people_terms=[],
                    org_terms=[],
                    planner_filter_spec=None,
                )

        self.assertEqual(cm.exception.error_code, "JOIN_KEYS_MISSING")
        mock_hop2.assert_not_called()


if __name__ == "__main__":
    unittest.main()
