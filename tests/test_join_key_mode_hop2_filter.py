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


class JoinHop2FilterModeTests(unittest.TestCase):
    @patch("rag_pipeline.build_perf_filter_by_pjt_id")
    @patch("rag_pipeline.build_perf_filter_by_pjt_no")
    def test_group_mode_uses_pjt_no_filter_for_perf_relation(self, mock_by_pjt_no, mock_by_pjt_id):
        mock_by_pjt_no.return_value = {"filter": "by_pjt_no"}

        result = rag_pipeline._build_join_hop2_filter(
            relation=("project", "perf"),
            join_key_mode="group",
            join_pjt_ids=["PJT-001"],
            join_pjt_nos=["NO-001"],
            join_ids=["PJT-001"],
            q="성과 보여줘",
            hop2_tag_filters=["TAG_A"],
            people_terms=[],
            org_terms=[],
            planner_filter_spec={},
        )

        self.assertEqual(result, {"filter": "by_pjt_no"})
        mock_by_pjt_no.assert_called_once_with(["NO-001"], "성과 보여줘")
        mock_by_pjt_id.assert_not_called()

    @patch("rag_pipeline.build_perf_filter_by_pjt_id")
    @patch("rag_pipeline.build_perf_filter_by_pjt_no")
    def test_instance_mode_uses_pjt_id_filter_for_perf_relation(self, mock_by_pjt_no, mock_by_pjt_id):
        mock_by_pjt_id.return_value = {"filter": "by_pjt_id"}

        result = rag_pipeline._build_join_hop2_filter(
            relation=("people", "perf"),
            join_key_mode="instance",
            join_pjt_ids=["PJT-999"],
            join_pjt_nos=[],
            join_ids=["PJT-ALT"],
            q="성과",
            hop2_tag_filters=None,
            people_terms=["홍길동"],
            org_terms=[],
            planner_filter_spec={},
        )

        self.assertEqual(result, {"filter": "by_pjt_id"})
        mock_by_pjt_id.assert_called_once_with(["PJT-999"], "성과")
        mock_by_pjt_no.assert_not_called()


if __name__ == "__main__":
    unittest.main()
