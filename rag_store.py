# rag_store.py
# -*- coding: utf-8 -*-

from typing import Tuple, Optional, Any
from qdrant_client import QdrantClient
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from settings import (
    QDRANT_HOST,
    QDRANT_PORT,
    EMBED_MODEL,      # e5-large-instruct
    EMBED_MODEL_B,    # e5-large
    logger,
)
from rag_parts.constants import (
    COL_PERF,
    COL_PROJECT,
    COL_SUPPORT,
    KEY_ORG_NORM,
)
from retrieval import ensure_keyword_index, ensure_text_index
from triton_client import get_triton_client

_qdr: Optional[QdrantClient] = None
_emb_e5i: Optional[HuggingFaceEmbedding] = None
_emb_e5: Optional[HuggingFaceEmbedding] = None


def _ensure_payload_indexes(client: QdrantClient) -> None:
    collections = [COL_PROJECT, COL_PERF, COL_SUPPORT]
    text_fields = ["title", "answer_public", "meta_flat"]
    keyword_fields = ["tag", KEY_ORG_NORM, "doc_id", "PJT_ID", "pjt_id", "meta.PJT_ID", "meta.pjt_id"]

    for collection in collections:
        for field in text_fields:
            ensure_text_index(client, collection, field)
        for field in keyword_fields:
            ensure_keyword_index(client, collection, field)

def build_rag_objects_dual() -> Tuple[
    QdrantClient, HuggingFaceEmbedding, Any,
    QdrantClient, HuggingFaceEmbedding, Any,
]:
    """
    single collection + multi-vector
      - e5i_qa: multilingual-e5-large-instruct
      - e5_qa : multilingual-e5-large
    반환 형태는 기존 main.py 호환을 위해 (qdr, embA, None, qdr, embB, None)
    """
    global _qdr, _emb_e5i, _emb_e5

    if _qdr and _emb_e5i and _emb_e5:
        return _qdr, _emb_e5i, None, _qdr, _emb_e5, None


    _qdr = QdrantClient(
        host=QDRANT_HOST,
        grpc_port=QDRANT_PORT,
        prefer_grpc=True,
    )
    _ensure_payload_indexes(_qdr)

    # e5-large-instruct (e5i_qa)
    _emb_e5i = HuggingFaceEmbedding(
        model_name=EMBED_MODEL,
        device="cuda",
        embed_batch_size=32,
        trust_remote_code=True,
        query_instruction=(
            "Instruct: Given a user query about the NTIS domain, "
            "retrieve the most relevant documents from the knowledge base. "
            "The query may involve research projects, project IDs, "
            "research outcomes (papers, patents, software, reports), "
            "researchers, institutions, statistics, or system procedures. "
            "Focus on factual relevance and entity matching.\n"
            "Query: "
        ),
        text_instruction=None,
    )

    # e5-large (e5_qa)
    _emb_e5 = HuggingFaceEmbedding(
        model_name=EMBED_MODEL_B,
        device="cuda",              # 필요 시 조정
        embed_batch_size=32,
        trust_remote_code=True,
        query_instruction="query: ntis ",
        text_instruction="passage: ",
    )

    # Triton warm-up
    get_triton_client()

    return _qdr, _emb_e5i, None, _qdr, _emb_e5, None
