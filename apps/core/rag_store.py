# rag_store.py
# -*- coding: utf-8 -*-

from dataclasses import dataclass
from typing import Tuple, Optional, Any
from qdrant_client import QdrantClient
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from apps.core.settings import (
    QDRANT_HOST,
    QDRANT_PORT,
    EMBED_MODEL,      # e5-large-instruct
    EMBED_MODEL_B,    # e5-large
)
from apps.core.triton_client import get_triton_client

@dataclass(frozen=True)
class RagResources:
    """Qdrant와 임베딩 모델처럼 RAG 실행에 공통으로 쓰는 리소스 묶음이다."""
    qdrant_client: QdrantClient
    embed_e5i: HuggingFaceEmbedding
    embed_e5: HuggingFaceEmbedding


_resources: Optional[RagResources] = None


def build_rag_objects() -> RagResources:
    """Qdrant 클라이언트와 두 임베딩 모델을 한 번만 초기화해 캐시한다.

    질의용 e5-large-instruct와 범용 e5-large를 함께 준비해 이후 retrieval 경로가 같은 리소스를 재사용하게 만든다.
    """
    global _resources

    if _resources is not None:
        return _resources

    qdr = QdrantClient(
        host=QDRANT_HOST,
        port=QDRANT_PORT,
        prefer_grpc=True,
        timeout=6000
    )

    # e5-large-instruct (e5i_qa)
    emb_e5i = HuggingFaceEmbedding(
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
    emb_e5 = HuggingFaceEmbedding(
        model_name=EMBED_MODEL_B,
        device="cuda",              # 필요 시 조정
        embed_batch_size=32,
        trust_remote_code=True,
        query_instruction="query: ntis ",
        text_instruction="passage: ",
    )

    # Triton warm-up
    triton_client = get_triton_client()

    _resources = RagResources(
        qdrant_client=qdr,
        embed_e5i=emb_e5i,
        embed_e5=emb_e5,
    )
    return _resources

def build_rag_objects_dual() -> Tuple[
    QdrantClient, HuggingFaceEmbedding, Any,
    QdrantClient, HuggingFaceEmbedding, Any,
]:
    """기존 dual-resource 인터페이스를 유지하기 위해 cached RagResources를 튜플로 풀어 반환한다."""
    resources = build_rag_objects()
    return (
        resources.qdrant_client, resources.embed_e5i, None,
        resources.qdrant_client, resources.embed_e5, None,
    )
