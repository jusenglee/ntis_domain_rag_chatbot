from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from qdrant_client import QdrantClient

from apps.platform.settings import EMBED_MODEL, EMBED_MODEL_B, QDRANT_HOST, QDRANT_PORT


@dataclass(frozen=True)
class RagResources:
    """Shared Qdrant and embedding resources for the retrieval runtime."""

    qdrant_client: QdrantClient
    embed_e5i: Any
    embed_e5: Any


_resources: Optional[RagResources] = None


def build_rag_objects() -> RagResources:
    """Initialize and cache the dual embedding resources the retrieval stack needs."""

    global _resources

    if _resources is not None:
        return _resources

    try:
        from llama_index.embeddings.huggingface import HuggingFaceEmbedding
    except ModuleNotFoundError as exc:
        raise RuntimeError("llama_index is required to build NTIS RAG embedding resources.") from exc

    from apps.platform.triton_client import get_triton_client

    qdr = QdrantClient(
        host=QDRANT_HOST,
        port=QDRANT_PORT,
        #prefer_grpc=True,
        timeout=6000,
    )

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

    emb_e5 = HuggingFaceEmbedding(
        model_name=EMBED_MODEL_B,
        device="cuda",
        embed_batch_size=32,
        trust_remote_code=True,
        query_instruction="query: ntis ",
        text_instruction="passage: ",
    )

    # Keep the previous warm-up side effect, but only when the runtime actually builds RAG objects.
    get_triton_client()

    _resources = RagResources(
        qdrant_client=qdr,
        embed_e5i=emb_e5i,
        embed_e5=emb_e5,
    )
    return _resources


def build_rag_objects_dual() -> Tuple[QdrantClient, Any, Any, QdrantClient, Any, Any]:
    """Expose the cached resources in the legacy dual tuple shape."""

    resources = build_rag_objects()
    return (
        resources.qdrant_client,
        resources.embed_e5i,
        None,
        resources.qdrant_client,
        resources.embed_e5,
        None,
    )
