#!/usr/bin/env python3
"""
retrieval.py (refactored)

NTIS/일반 문서형 RAG 검색 모듈.

핵심 목표
1) 멀티-벡터 dense 검색 + spase 키워드 벡터(vec name="bm25") 후보를 결합해 RRF로 재정렬
2) 토큰/컨텍스트 폭발을 줄이기 위해 *컨텍스트 빌더*를 "예산 기반"으로 구성

설계 포인트
- rag_pipeline.py가 S3 query_filter(qdrant Filter)를 전달할 수 있게 파라미터를 유지합니다.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, get_origin

from qdrant_client import QdrantClient
from qdrant_client.http import models


_ARRAY_PART_RE = re.compile(r"^(?P<k>.+)\[\]$")
# ---------------------------------------------------------------------
# Sparse (BM25) query support
# - If Qdrant has a named sparse vector (e.g., "bm25"), we can query it directly.
# - Query sparse vector is generated via fastembed (if available).
# - If fastembed is unavailable, we return empty sparse results (no legacy lexical fallback).
# ---------------------------------------------------------------------

# ---- globals ----
_SPARSE_ENCODERS: dict[str, Any] = {}
_SPARSE_LOCK = threading.Lock()


# logger는 기존 그대로 쓴다고 가정
# logger = logging.getLogger("RAG_Retrieval")


def _filter_brief(f: Any) -> str:
    if f is None:
        return "None"
    return (
        f"Filter(must={len(getattr(f, 'must', None) or [])}, "
        f"should={len(getattr(f, 'should', None) or [])}, "
        f"must_not={len(getattr(f, 'must_not', None) or [])})"
    )


def _peek(points: Any, n: int = 3) -> List[Any]:
    out: List[Any] = []
    for p in (points or [])[:n]:
        pl = getattr(p, "payload", {}) or {}
        out.append(
            (
                str(getattr(p, "id", "")),
                pl.get("doc_id"),
                (pl.get("title_text") or "")[:40],
                float(getattr(p, "score", 0.0) or 0.0),
            )
        )
    return out


def _get_sparse_encoder(model_name: str):
    """Per-model cached SparseTextEmbedding encoder (thread-safe)."""
    # fastembed import는 여기서 한 번만 시도
    from fastembed import SparseTextEmbedding  # type: ignore

    with _SPARSE_LOCK:
        enc = _SPARSE_ENCODERS.get(model_name)
        if enc is None:
            enc = SparseTextEmbedding(model_name=model_name)
            _SPARSE_ENCODERS[model_name] = enc
        return enc


def _encode_sparse_query(text: str, *, model_name: str) -> Optional[models.SparseVector]:
    """
    Encode query text into Qdrant SparseVector using fastembed.

    Contract:
    - returns models.SparseVector or None only
    - NEVER returns QueryRequest / dict / tuple etc.
    """
    if not text:
        return None

    try:
        enc = _get_sparse_encoder(model_name)
        # fastembed expects list[str] and yields SparseEmbedding (indices/values)
        emb = next(enc.embed([text]))
        idx = emb.indices.tolist() if hasattr(emb.indices, "tolist") else list(emb.indices)
        val = emb.values.tolist() if hasattr(emb.values, "tolist") else list(emb.values)

        if not idx or not val:
            return None

        # indices/values 길이 안 맞는 케이스 방어
        if len(idx) != len(val):
            m = min(len(idx), len(val))
            idx, val = idx[:m], val[:m]
            if not idx:
                return None

        return models.SparseVector(indices=idx, values=val)

    except Exception as e:  # pragma: no cover
        logger.warning(f"[retrieval] SparseText encode failed: {e}")
        return None


def _qdrant_query_points_sparse(
        client: Any,
        *,
        collection_name: str,
        sparse_vector: models.SparseVector,
        sparse_vector_name: str,
        limit: int,
        query_filter: Any = None,
        with_payload: Any = True,
        timeout: int = 3000,
) -> List[Any]:
    """
    Qdrant sparse search compat layer.

    Tries:
    1) client.query_points(query=SparseVector, using="bm25")
    2) fallback: client.search(query_vector=(name, SparseVector))
    """
    # 디버깅에 필요한 정보만 남김 (nnz=non-zero terms)
    nnz = len(getattr(sparse_vector, "indices", []) or [])
    logger.debug(
        "[retrieval] sparse_query: col=%s using=%s limit=%d nnz=%d filter=%s payload=%s",
        collection_name,
        sparse_vector_name,
        int(limit),
        int(nnz),
        type(query_filter).__name__,
        type(with_payload).__name__,
    )

    # 1) new-style
    try:
        res = client.query_points(
            collection_name=collection_name,
            query=sparse_vector,              # ✅ 반드시 SparseVector만
            using=str(sparse_vector_name),    # ✅ named sparse vector (e.g., "bm25")
            limit=int(limit),
            with_payload=with_payload,
            with_vectors=False,
            query_filter=query_filter,        # query_points 쪽 파라미터명
            timeout=int(timeout),
        )
        logger.warning(f"[retrieval] sparse client.search res: {res}")
        return list(getattr(res, "points", []) or [])
    except Exception as e:  # pragma: no cover
        logger.warning(f"[retrieval] sparse client.search failed: {e}")
        return []


def _qdrant_sparse_search(
        client: Any,
        *,
        collection_name: str,
        query_text: str,
        sparse_vector_name: str,
        limit: int,
        query_filter: Any = None,
        with_payload: Any = True,
) -> List[Any]:
    """Public sparse search entry (bm25)."""
    if not sparse_vector_name:
        return []

    model_name = (os.getenv("RAG_SPARSE_EMBED_MODEL", "Qdrant/bm25") or "").strip() or "Qdrant/bm25"
    sv = _encode_sparse_query(query_text, model_name=model_name)
    if sv is None:
        logger.warning(
            "[retrieval] sparse_query skipped: encoder unavailable or empty query col=%s using=%s",
            collection_name,
            sparse_vector_name,
        )
        return []
    logger.info(
        "[SPARSE.ENC] model=%s len=%d nnz=%d head=%r",
        model_name,
        len(query_text or ""),
        len(getattr(sv, "indices", []) or []),
        (query_text or "")[:80],
    )

    return _qdrant_query_points_sparse(
        client,
        collection_name=collection_name,
        sparse_vector=sv,
        sparse_vector_name=str(sparse_vector_name),
        limit=int(limit),
        query_filter=query_filter,
        with_payload=with_payload,
        timeout=_DEFAULT_QDRANT_TIMEOUT,
    )


def _prefetch_supports_using() -> bool:
    prefetch_cls = getattr(models, "Prefetch", None)
    if prefetch_cls is None:
        return False
    for attr in ("model_fields", "__fields__"):
        fields = getattr(prefetch_cls, attr, None)
        if isinstance(fields, dict) and "using" in fields:
            return True
    try:
        signature = inspect.signature(prefetch_cls)
    except (TypeError, ValueError):
        return False
    return "using" in signature.parameters


def _supports_qdrant_query_model() -> bool:
    query_model = getattr(models, "Query", None)
    if query_model is not None:
        if inspect.isclass(query_model):
            return True
        if get_origin(query_model) is not None:
            return True
        return True
    query_request_cls = getattr(models, "QueryRequest", None)
    return inspect.isclass(query_request_cls)


def _supports_qdrant_hybrid_query() -> bool:
    required = ("Prefetch", "Fusion")
    if not all(hasattr(models, name) for name in required):
        return False
    if not _supports_qdrant_query_model():
        return False
    if _prefetch_supports_using():
        return True
    return all(hasattr(models, name) for name in ("NamedVector", "NamedSparseVector"))


def _build_hybrid_query_model(
    *,
    prefetch: Sequence[Any],
    fusion: Any,
) -> Any:
    query_model = getattr(models, "Query", None)
    if inspect.isclass(query_model):
        return query_model(prefetch=prefetch, query=fusion)
    query_request_cls = getattr(models, "QueryRequest", None)
    if inspect.isclass(query_request_cls):
        return query_request_cls(prefetch=prefetch, query=fusion)
    return {
        "prefetch": prefetch,
        "query": fusion,
    }


def _get_fusion_rrf() -> Any:
    fusion_enum = getattr(models, "Fusion", None)
    fusion_query_cls = getattr(models, "FusionQuery", None)
    if fusion_enum is None or fusion_query_cls is None:
        return None

    # Fusion.RRF / Fusion.rrf 둘 다 방어
    if hasattr(fusion_enum, "RRF"):
        return fusion_query_cls(fusion=fusion_enum.RRF)
    if hasattr(fusion_enum, "rrf"):
        return fusion_query_cls(fusion=getattr(fusion_enum, "rrf"))
    return None


def _qdrant_hybrid_query_once(
    client: Any,
    *,
    collection_name: str,
    query_text: str,
    emb_map: Dict[str, Any],
    sparse_vector_name: str,
    top_k_dense: int,
    top_k_lexical_candidates: int,
    top_k_lexical: int,
    lexical_fields: Optional[List[str]],
    query_filter: Any = None,
) -> Optional[List[models.ScoredPoint]]:
    if not _supports_qdrant_hybrid_query():
        return None
    fusion = _get_fusion_rrf()
    if fusion is None:
        return None
    logger.warning(f"[retrieval] hybrid query_points query_text: {query_text}")
    supports_using = _prefetch_supports_using()
    supports_named_vector = hasattr(models, "NamedVector")
    supports_named_sparse = hasattr(models, "NamedSparseVector")
    logger.debug(
        "[retrieval] hybrid prefetch support using=%s named_vector=%s named_sparse=%s",
        supports_using,
        supports_named_vector,
        supports_named_sparse,
    )

    prefetch = []
    for vec_name, emb in (emb_map or {}).items():
        v = embed_query(emb, query_text)
        if not v:
            continue
        if supports_using:
            logger.debug(
                "[retrieval] hybrid prefetch dense using path vec_name=%s",
                vec_name,
            )
            prefetch.append(
                models.Prefetch(
                    query=v,
                    using=str(vec_name),
                    limit=int(top_k_dense),
                )
            )
        elif supports_named_vector:
            logger.debug(
                "[retrieval] hybrid prefetch dense named_vector path vec_name=%s",
                vec_name,
            )
            prefetch.append(
                models.Prefetch(
                    query=models.NamedVector(name=str(vec_name), vector=v),
                    limit=int(top_k_dense),
                )
            )
        else:
            logger.debug(
                "[retrieval] hybrid prefetch dense unsupported vec_name=%s",
                vec_name,
            )
            return None

    model_name = str(os.getenv("RAG_SPARSE_EMBED_MODEL", "Qdrant/bm25")).strip() or "Qdrant/bm25"
    sv = _encode_sparse_query(query_text, model_name=model_name)
    if sv is None:
        return None
    if supports_using:
        logger.debug(
            "[retrieval] hybrid prefetch sparse using path vec_name=%s",
            sparse_vector_name,
        )
        prefetch.append(
            models.Prefetch(
                query=sv,
                using=str(sparse_vector_name),
                limit=int(top_k_lexical_candidates),
            )
        )
    elif supports_named_sparse:
        logger.debug(
            "[retrieval] hybrid prefetch sparse named_sparse path vec_name=%s",
            sparse_vector_name,
        )
        prefetch.append(
            models.Prefetch(
                query=models.NamedSparseVector(name=str(sparse_vector_name), vector=sv),
                limit=int(top_k_lexical_candidates),
            )
        )
    else:
        logger.debug(
            "[retrieval] hybrid prefetch sparse unsupported vec_name=%s",
            sparse_vector_name,
        )
        return None

    if not prefetch:
        return None

    lexical_fields_eff = list(
        lexical_fields
        or [
            "title_text",
            "content_text",
            "keyword_text",
            "flat_text",
        ]
    )
    with_payload = _with_payload_selector(
        _PAYLOAD_MODE_LEX,
        _PAYLOAD_MIN_FIELDS,
        lexical_fields_eff,
    )

    fusion_query = _get_fusion_rrf()
    if fusion_query is None:
        return None
    query_model = _build_hybrid_query_model(prefetch=prefetch, fusion=fusion)
    try:
        res = client.query_points(
            collection_name=collection_name,
            prefetch=prefetch,  # ✅ 여기!
            query=fusion_query, # ✅ 여기!
            limit=int(max(int(top_k_dense), int(top_k_lexical))),
            with_payload=with_payload,
            with_vectors=False,
            query_filter=query_filter,
            timeout=int(_DEFAULT_QDRANT_TIMEOUT),
        )
        logger.warning(f"[retrieval] hybrid query_points res: {res}")
    except Exception as e:  # pragma: no cover
        logger.warning(f"[retrieval] hybrid query_points failed: {e}")
        return None

    return list(getattr(res, "points", []) or [])

def fetch_full_payloads(client: QdrantClient, collection_name: str, points):
    ids = [p.id for p in points if getattr(p, "id", None) is not None]
    if not ids:
        return points

    got = client.retrieve(
        collection_name=collection_name,
        ids=ids,
        with_payload=True,
        with_vectors=False,
    )
    # id -> payload 맵
    mp = {str(x.id): x.payload for x in (got or [])}
    for p in points:
        pid = str(p.id)
        if pid in mp:
            p.payload = mp[pid]
    return points

logger = logging.getLogger("RAG_Retrieval")


# =========================
# Env / Defaults
# =========================

_DEFAULT_TOPK_DENSE = int(os.getenv("RAG_TOPK_DENSE", "25"))
_DEFAULT_TOPK_LEX_CAND = int(os.getenv("RAG_TOPK_LEX_CAND", "250"))
_DEFAULT_TOPK_LEX = int(os.getenv("RAG_TOPK_LEX", "50"))

_DEFAULT_RRF_K = int(os.getenv("RAG_RRF_K", "60"))
_DEFAULT_RERANK_K = int(os.getenv("RAG_RERANK_K", "60"))

_HYBRID_QUERY_ONCE = os.getenv("RAG_HYBRID_QUERY_ONCE", "1") == "1"

_SNIP_MAX_CHARS = int(os.getenv("SNIPPET_MAX_CHARS", "8192"))
_CTX_TOKEN_BUDGET = int(os.getenv("CTX_TOKEN_BUDGET", "8192"))
_CTX_MAX_ITEMS = int(os.getenv("RAG_MAX_CONTEXT_ITEMS", "10"))

_CTX_PER_DOC_CHARS = int(os.getenv("RAG_CTX_PER_DOC_MAX_CHARS", "1200"))
_CTX_META_MAX_FIELDS = int(os.getenv("RAG_CTX_META_MAX_FIELDS", "12"))

# 0이면 meta_basic/meta_detail의 긴 텍스트(요약류)를 기본적으로 제외
_CTX_INCLUDE_META_LONG = os.getenv("RAG_CTX_INCLUDE_META_LONG", "0") == "1"

# Qdrant call timeout (seconds)
_DEFAULT_QDRANT_TIMEOUT = int(os.getenv("RAG_QDRANT_TIMEOUT", "300"))


# =========================
# Payload selector (min/full)
# =========================
_PAYLOAD_MODE_DENSE = os.getenv("RAG_PAYLOAD_MODE_DENSE", "min").strip().lower()  # min|full|true
_PAYLOAD_MODE_LEX   = os.getenv("RAG_PAYLOAD_MODE_LEX", "min").strip().lower()    # min|full|true

# 후보 단계에 필요한 최소 키들(메타/본문 제외)
_PAYLOAD_MIN_FIELDS = [s.strip() for s in os.getenv(
    "RAG_PAYLOAD_MIN_FIELDS",
    "id,"
    "doc_id,"
    "tag,"
    "title_text,"
    "content_text,"
    "keyword_text,"
    "flat_text,"
    "category,"
    "prtcp_mp[].hm_nm,"
    "prtcp_mp[].blng_org_nm,"
    "prtcp_org[].org_nm,"
    "org_nm,"
    "pjt_id,"
    "pjt_no,"
    "meta_basic.pjt_no"
).split(",") if s.strip()]

def _with_payload_selector(
    mode: str,
    fields: List[str],
    extra_fields: Optional[List[str]] = None,
):
    """
    Qdrant with_payload:
      - True/False 가능
      - 또는 PayloadSelectorInclude 가능
    """
    m = (mode or "min").lower().strip()
    merged_fields = list(dict.fromkeys(list(fields or []) + list(extra_fields or [])))
    if m in ("true", "1", "yes", "y"):
        return True
    if m in ("full",):
        try:
            return models.PayloadSelectorInclude(include=merged_fields)
        except Exception as e:  # pragma: no cover
            logger.warning(f"[retrieval] _with_payload_selector failed: {e}")
            return None
    # default: min
    try:
        return models.PayloadSelectorInclude(include=merged_fields)
    except Exception as e:  # pragma: no cover
        logger.warning(f"[retrieval] _with_payload_selector failed: {e}")
        return None



def _safe_str(x: Any, *, max_chars: Optional[int] = None) -> str:
    if x is None:
        return ""
    s = str(x)
    if max_chars is not None and len(s) > max_chars:
        return s[: max_chars - 1] + "…"
    return s


def _clamp_top_k(value: Any, *, minimum: int = 0, fallback: int = 0) -> int:
    try:
        val = int(value)
    except (TypeError, ValueError):
        return fallback
    if val < minimum:
        return minimum
    return val


def _payload_get(pl: Dict[str, Any], key: str, default: Any = "") -> Any:
    """
    Supports:
      - a.b.c
      - a[].b
      - a[].b.c
    For [] paths, returns space-joined string of leaf values by default.
    """
    if not isinstance(pl, dict) or not key:
        return default

    parts = key.split(".")
    curs: List[Any] = [pl]

    for part in parts:
        m = _ARRAY_PART_RE.match(part)
        next_curs: List[Any] = []

        if m:
            arr_key = m.group("k")
            for cur in curs:
                if isinstance(cur, dict):
                    arr = cur.get(arr_key)
                    if isinstance(arr, list):
                        next_curs.extend(arr)
            curs = next_curs
            continue

        for cur in curs:
            if isinstance(cur, dict) and part in cur:
                next_curs.append(cur[part])
        curs = next_curs

        if not curs:
            return default

    # leaf flatten -> text join
    leaves: List[str] = []
    for c in curs:
        if c is None:
            continue
        if isinstance(c, (dict, list)):
            # dict/list는 문자열화하지 않고 무시(원하면 json dump로 바꿔도 됨)
            continue
        s = str(c).strip()
        if s:
            leaves.append(s)

    if not leaves:
        return default
    return " ".join(leaves)


# =========================
# Query normalization
# =========================

_SPACE_RE = re.compile(r"\s+")


def normalize_query(q: str) -> str:
    """Normalize user query: strip + collapse whitespaces."""
    if q is None:
        return ""
    q = str(q).strip()
    q = _SPACE_RE.sub(" ", q)
    return q


def _approx_token_len(text: str) -> int:
    """토크나이저 없이 예산 기반 컷오프용 근사치 (추측입니다)."""
    if not text:
        return 0
    words = len(text.split())
    return max(words, int(len(text) / 4))


def embed_query(emb: Any, text: str) -> Optional[List[float]]:
    """Get query embedding vector from embedder (supports multiple APIs)."""
    if emb is None:
        return None
    try:
        if hasattr(emb, "get_query_embedding"):
            v = emb.get_query_embedding(text)
        elif hasattr(emb, "get_text_embedding"):
            v = emb.get_text_embedding(text)
        elif hasattr(emb, "encode"):
            v = emb.encode([text])[0]
        else:
            return None
        if hasattr(v, "tolist"):
            v = v.tolist()
        if isinstance(v, (list, tuple)):
            return list(v)
        return None
    except Exception as e:  # pragma: no cover
        logger.warning(f"[retrieval] embed_query failed: {e}")
        return None


# =========================
# Index helpers
# =========================

def ensure_text_index(
        client: QdrantClient,
        collection_name: str,
        field_name: str,
        *,
        wait: bool = True,
) -> None:
    """Ensure a text index exists for a payload field."""
    try:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=models.PayloadSchemaType.TEXT,
            wait=wait,
        )
    except Exception as e:  # pragma: no cover
        logger.debug(f"[retrieval] ensure_text_index skip: {collection_name}.{field_name}: {e}")


def ensure_keyword_index(
        client: QdrantClient,
        collection_name: str,
        field_name: str,
        *,
        wait: bool = True,
) -> None:
    """Ensure a keyword index exists for a payload field (fast filtering)."""
    try:
        client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=wait,
        )
    except Exception as e:  # pragma: no cover
        logger.debug(f"[retrieval] ensure_keyword_index skip: {collection_name}.{field_name}: {e}")


# =========================
# Payload helpers
# =========================

def _attach_collection(point: Any, collection: str) -> Any:
    if point is None or not collection:
        return point
    if isinstance(point, dict):
        payload = point.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("_collection", collection)
        point["payload"] = payload
        point.setdefault("_collection", collection)
        return point
    setattr(point, "_collection", collection)
    payload = getattr(point, "payload", None)
    if not isinstance(payload, dict):
        payload = {}
        setattr(point, "payload", payload)
    payload.setdefault("_collection", collection)
    return point


def _ensure_collection_mark(points: List[Any], collection: str) -> None:
    for point in points or []:
        _attach_collection(point, collection)


def _set_payload_hint(point: Any, collection: str, vec_name: str = "") -> None:
    # """Annotate payload with collection/vector hints for debug/dedup."""
    # try:
    #     if not isinstance(point.payload, dict):
    #         return
    #     point.payload.setdefault("_collection", collection)
    #     if vec_name:
    #         point.payload.setdefault("_vec", vec_name)
    # except Exception as e:  # pragma: no cover
    #     logger.warning(f"[retrieval] _set_payload_hint failed: {e}")
        #return None
    return

# =========================
# Search result model
# =========================

def _make_scored_point_from_payload(
        *,
        pid: Any,
        payload: Dict[str, Any],
        score: float,
        version: int = 0,
) -> models.ScoredPoint:
    return models.ScoredPoint(
        id=pid,
        version=int(version or 0),
        score=float(score),
        payload=payload,
        vector=None,
    )


def _combine_filters(base: Optional[models.Filter], extra: Optional[models.Filter]) -> Optional[models.Filter]:
    """Combine two Qdrant filters conservatively."""
    if base is None and extra is None:
        return None
    if base is None:
        return extra
    if extra is None:
        return base

    def _get_list(f: models.Filter, name: str) -> List[Any]:
        v = getattr(f, name, None)
        return list(v) if v else []

    return models.Filter(
        must=_get_list(base, "must") + _get_list(extra, "must"),
        should=_get_list(base, "should") + _get_list(extra, "should"),
        must_not=_get_list(base, "must_not") + _get_list(extra, "must_not"),
    )


# =========================
# Dense + Lexical Hybrid
# =========================

def dense_retrieve_hybrid_multi(
    *,
    client: QdrantClient,
    emb_map: Dict[str, Any],
    expanded_text: str,
    keywords: List[str],
    collection_name: str,
    lexical_fields: Optional[List[str]] = None,
    lexical_field_weights: Optional[Dict[str, float]] = None,
    lexical_scoring_mode: str = "bm25",
    top_k_dense: int = _DEFAULT_TOPK_DENSE,
    top_k_lexical_candidates: int = _DEFAULT_TOPK_LEX_CAND,
    top_k_lexical: int = _DEFAULT_TOPK_LEX,
    sparse_vector_name: Optional[str] = None,
    sparse_topk: Optional[int] = None,
    query_filter: Optional[models.Filter] = None,
    timings: Optional[Dict[str, float]] = None,
    hybrid_once: Optional[bool] = None,
) -> Dict[str, Any]:
    """Run dense retrieval for multiple named vectors + sparse retrieval."""
    timings = timings if timings is not None else {}
    with_payload_dense_sparse = _with_payload_selector(_PAYLOAD_MODE_DENSE, _PAYLOAD_MIN_FIELDS)
    q = normalize_query(expanded_text)
    if not q:
        return {"dense": {}, "lexical": []}

    top_k_dense = _clamp_top_k(top_k_dense, minimum=0, fallback=_DEFAULT_TOPK_DENSE)
    top_k_lexical_candidates = _clamp_top_k(
        top_k_lexical_candidates,
        minimum=0,
        fallback=_DEFAULT_TOPK_LEX_CAND,
    )
    top_k_lexical = _clamp_top_k(top_k_lexical, minimum=0, fallback=_DEFAULT_TOPK_LEX)

    hybrid_once_eff = _HYBRID_QUERY_ONCE if hybrid_once is None else bool(hybrid_once)
    if hybrid_once_eff:
        if not sparse_vector_name or not emb_map:
            logger.warning(
                "[RETRIEVE.HYBRID] skipped: sparse_vector_name=%s emb_map=%s",
                bool(sparse_vector_name),
                bool(emb_map),
            )
            ret = {"dense": {}, "lexical": [], "hybrid": []}
            logger.info("[RETRIEVE.RET] keys=%s", list(ret.keys()))
            return ret
        t_hybrid0 = time.perf_counter()
        hybrid_points = _qdrant_hybrid_query_once(
            client,
            collection_name=collection_name,
            query_text=q,
            emb_map=emb_map,
            sparse_vector_name=str(sparse_vector_name),
            top_k_dense=top_k_dense,
            top_k_lexical_candidates=top_k_lexical_candidates,
            top_k_lexical=top_k_lexical,
            lexical_fields=lexical_fields,
            query_filter=query_filter,
        )
        timings["hybrid_once_total"] = time.perf_counter() - t_hybrid0
        if hybrid_points is None:
            logger.warning("[RETRIEVE.HYBRID] failed: empty result")
            ret = {"dense": {}, "lexical": [], "hybrid": []}
            logger.info("[RETRIEVE.RET] keys=%s", list(ret.keys()))
            return ret
        timings["hybrid_once_hits"] = float(len(hybrid_points))
        ret = {"dense": {}, "lexical": [], "hybrid": hybrid_points}
        logger.info("[RETRIEVE.RET] keys=%s", list(ret.keys()))
        return ret

    # -----------------------
    # Dense retrieval
    # -----------------------
    dense: Dict[str, List[models.ScoredPoint]] = {}
    t_dense0 = time.perf_counter()

    t_embed_sum = 0.0
    t_qdrant_sum = 0.0
    dense_queries = 0
    dense_points = 0

    # dense 비활성화: top_k_dense<=0이면 루프 자체를 건너뛰도록 emb_map을 비움
    if int(top_k_dense) <= 0:
        emb_map = {}

    q_text_for_dense = q or ""
    q_text_for_sparse = q or ""
    logger.info(
        "[RETRIEVE] q_text_for_dense=%s q_text_for_sparse=%s",
        q_text_for_dense,
        q_text_for_sparse,
    )
    logger.info("[RETRIEVE] filter=%s", _filter_brief(query_filter))

    for vec_name, emb in (emb_map or {}).items():
        t0 = time.perf_counter()
        v = embed_query(emb, q)
        t_embed = time.perf_counter() - t0
        t_embed_sum += t_embed
        timings[f"dense_embed_{vec_name}"] = t_embed
        if not v:
            continue

        t0 = time.perf_counter()
        logger.debug(
            "[retrieval] query_points args types: using=%r(%s) limit=%r(%s) timeout=%r(%s) query_len=%r",
            vec_name, type(vec_name).__name__,
            top_k_dense, type(top_k_dense).__name__,
            _DEFAULT_QDRANT_TIMEOUT, type(_DEFAULT_QDRANT_TIMEOUT).__name__,
            (len(v) if isinstance(v, (list, tuple)) else None),
        )

        try:
            try:
                res = client.query_points(
                    collection_name=collection_name,
                    query=v,
                    using=vec_name,
                    limit=int(top_k_dense),
                    with_payload=with_payload_dense_sparse,
                    with_vectors=False,
                    query_filter=query_filter,
                    timeout=_DEFAULT_QDRANT_TIMEOUT,
                )
            except Exception as e:  # pragma: no cover
                logger.warning(f"[retrieval] query_points failed: {e}")
                continue

            t_q = time.perf_counter() - t0
            t_qdrant_sum += t_q
            timings[f"dense_qdrant_{vec_name}"] = t_q

            pts = list(getattr(res, "points", []) or [])
            _ensure_collection_mark(pts, collection_name)
            for p in pts:
                _set_payload_hint(p, collection_name, vec_name)
            dense[vec_name] = pts
            logger.info("[DENSE] vec=%s hits=%d peek=%s", vec_name, len(pts), _peek(pts))

            dense_queries += 1
            dense_points += len(pts)

        except Exception as e:  # pragma: no cover
            t_q = time.perf_counter() - t0
            t_qdrant_sum += t_q
            timings[f"dense_qdrant_{vec_name}"] = t_q
            logger.warning(f"[retrieval] dense query failed: col={collection_name} vec={vec_name}: {e}")
            continue

    timings["dense_embed_total"] = t_embed_sum
    timings["dense_qdrant_total"] = t_qdrant_sum
    timings["dense_total"] = time.perf_counter() - t_dense0
    timings["dense_queries"] = float(dense_queries)
    timings["dense_points"] = float(dense_points)

    # -----------------------
    # Sparse retrieval (named sparse vector search only)
    # -----------------------
    if sparse_topk is not None:
        top_k_lexical = _clamp_top_k(
            sparse_topk,
            minimum=0,
            fallback=top_k_lexical,
        )

    t_lex0 = time.perf_counter()
    lex_points: List[models.ScoredPoint] = []

    lexical_fields_eff = list(
        lexical_fields
        or [
            "title_text",
            "content_text",
            "keyword_text",
            "flat_text",
        ]
    )
    with_payload_lex = _with_payload_selector(
        _PAYLOAD_MODE_LEX,
        _PAYLOAD_MIN_FIELDS,
        lexical_fields_eff,
    )
    logger.info(
        "[PAYLOAD] dense=%s lex=%s",
        type(with_payload_dense_sparse).__name__,
        type(with_payload_lex).__name__,
    )

    t_sparse0 = time.perf_counter()
    lex_cand = 0
    if sparse_vector_name:
        try:
            top_k_lexical_candidates_eff = int(top_k_lexical_candidates)
        except Exception:
            top_k_lexical_candidates_eff = int(top_k_lexical)
        sp_hits = _qdrant_sparse_search(
            client,
            collection_name=collection_name,
            query_text=q,
            sparse_vector_name=str(sparse_vector_name),
            limit=max(int(top_k_lexical_candidates_eff), int(top_k_lexical)),
            query_filter=query_filter,
            with_payload=with_payload_lex,
        )
        if sp_hits:
            lex_points = list(sp_hits)[: int(top_k_lexical)]
            _ensure_collection_mark(lex_points, collection_name)
            lex_cand = len(sp_hits)
        logger.info("[SPARSE] hits=%d cand=%d peek=%s", len(lex_points), lex_cand, _peek(lex_points))
    timings["lexical_sparse"] = time.perf_counter() - t_sparse0
    timings["lexical_candidates"] = float(lex_cand)
    timings["lexical_scored"] = float(len(lex_points))
    timings["lexical_total"] = time.perf_counter() - t_lex0

    ret = {"dense": dense, "lexical": lex_points}
    logger.info("[RETRIEVE.RET] keys=%s", list(ret.keys()))
    return ret


# =========================
# RRF rerank
# =========================

def _rrf_score(rank: int, rrf_k: int) -> float:
    return 1.0 / float(rrf_k + rank)

# =========================
# Context builder (docstyle)
# =========================

_META_CORE_KEYS = [
    "pjt_id",
    "pjt_no",
    "stan_yr",
    "org_nm",
    "kor_pjt_nm",
    "eng_pjt_nm",
    "pjt_prfrm_org_nm",
    "tot_rsch_start_dt",
    "tot_rsch_end_dt",
    "start_dt",
    "end_dt",
    "dt1",
    "dt2",
    "rndco_tot_amt",
    "keyword_text",
    "kor_kywd",
    "eng_kywd",
    "rsch_abstract",
    "rsch_goal_abstract",
    "rst_id",
    "paper_nm",
    "abstract_str",
    "jrnl_nm",
    "paper_regist_no",
    "paper_type_slct_nm",
    "dmabr_slct_nm",
    "prcd_nm",
    "prcd_venue_nat_nm",
    "prcd_pst_dt",
    "jrnl_pub_dt",
    "jrnl_vol_no",
    "issn",
    "sci_slct_nm",
    "doi",
]

_META_LONG_KEYS = {
    "rsch_abstract",
    "rsch_goal_abstract",
    "abstract_str",
}


def _merge_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key in ("meta_basic", "meta_detail"):
        v = payload.get(key)
        if isinstance(v, dict):
            merged.update(v)
    return merged


def _select_meta_fields(
    meta: Dict[str, Any],
    query_text: str,
    *,
    include_meta_long: Optional[bool] = None,
) -> List[str]:
    if not isinstance(meta, dict) or not meta:
        return []

    ql = (query_text or "").lower()
    out: List[str] = []

    def _normalize_year_value(value: Any) -> Any:
        if value is None:
            return value
        s = str(value).strip()
        if not s:
            return value
        match = re.match(r"^(\d{4})-\d{2}-\d{2}$", s)
        if match:
            return match.group(1)
        return value

    def _add(k: str) -> None:
        if k in meta and k not in out:
            out.append(k)

    if "stan_yr" in meta:
        meta["stan_yr"] = _normalize_year_value(meta.get("stan_yr"))

    for k in _META_CORE_KEYS:
        _add(k)

    if any(x in ql for x in ["기간", "시작", "종료", "언제"]):
        _add("tot_rsch_start_dt")
        _add("tot_rsch_end_dt")
        _add("start_dt")
        _add("end_dt")
    if any(x in ql for x in ["금액", "연구비", "예산", "비용"]):
        _add("rndco_tot_amt")
    if any(x in ql for x in ["기관", "대학", "출연", "주관"]):
        _add("pjt_prfrm_org_nm")
        _add("org_nm")
    if any(x in ql for x in ["키워드", "keyword"]):
        _add("kor_kywd")
        _add("eng_kywd")
        _add("keyword_text")

    include_long = _CTX_INCLUDE_META_LONG if include_meta_long is None else bool(include_meta_long)
    if include_long:
        for k in list(meta.keys()):
            if k in _META_LONG_KEYS:
                _add(k)

    return out[: _CTX_META_MAX_FIELDS]


def _format_meta_lines(meta: Dict[str, Any], keys: List[str]) -> str:
    lines: List[str] = []
    for k in keys:
        v = meta.get(k)
        if v is None or v == "":
            continue
        vv = _safe_str(v, max_chars=300)
        lines.append(f"- {k}: {vv}")
    return "\n".join(lines)


def build_context_docstyle(
        points: Sequence[Any],
        *,
        max_items: int = _CTX_MAX_ITEMS,
        token_budget: int = _CTX_TOKEN_BUDGET,
        per_doc_char_budget: int = _CTX_PER_DOC_CHARS,
        query_text: str = "",
        output_type: Optional[str] = None,
        fieldset_keys: Optional[Iterable[str]] = None,
        meta_source: Optional[str] = None,
        include_meta_long: Optional[bool] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    out_chunks: List[str] = []
    refs: List[Dict[str, Any]] = []

    total_tok = 0
    used = 0
    output_type_norm = (output_type or "").strip().lower() or None
    fieldset = {k for k in (fieldset_keys or []) if k}
    include_content = output_type_norm not in ("list", "stats")
    include_meta = True
    if fieldset:
        include_meta = any(k in fieldset for k in ("meta", "meta_basic", "meta_detail", "summary"))
        if "content" not in fieldset and "content_text" not in fieldset:
            include_content = False

    for p in points or []:
        if used >= int(max_items):
            break

        pl = p.payload if isinstance(getattr(p, "payload", None), dict) else {}
        if not pl:
            continue

        meta_basic = pl.get("meta_basic") if isinstance(pl.get("meta_basic"), dict) else {}
        meta_detail = pl.get("meta_detail") if isinstance(pl.get("meta_detail"), dict) else {}
        if meta_source == "detail_only" or (fieldset and "meta_detail" in fieldset and "meta_basic" not in fieldset):
            meta = dict(meta_detail)
        else:
            meta = _merge_meta(pl)
        title = _safe_str(
            pl.get("title_text")
            or pl.get("title1")
            or pl.get("title2")
            or meta.get("kor_pjt_nm")
            or meta.get("eng_pjt_nm")
            or "",
            max_chars=200,
        )
        doc_id = _safe_str(pl.get("doc_id") or "", max_chars=160)
        systems = pl.get("systems") if isinstance(pl.get("systems"), list) else []
        urls = pl.get("urls") if isinstance(pl.get("urls"), list) else []

        content_text = ""
        if include_content:
            content_text = _safe_str(
                pl.get("content_text")
                or pl.get("content1")
                or pl.get("content2")
                or "",
                max_chars=per_doc_char_budget,
            )
        meta_lines = ""
        if include_meta:
            meta_full = dict(meta)
            if not fieldset or "meta_basic" in fieldset or "meta" in fieldset:
                for key in ("category", "cetegory", "org_nm", "stan_yr", "start_dt", "end_dt", "dt1", "dt2", "keyword_text"):
                    if key not in meta_full and key in pl and pl.get(key) not in (None, ""):
                        meta_full[key] = pl.get(key)
            meta_keys = _select_meta_fields(
                meta_full,
                query_text=query_text,
                include_meta_long=include_meta_long,
            )
            meta_lines = _format_meta_lines(meta_full, meta_keys)

        chunk_parts: List[str] = []
        header = f"[DOC] {title}" if title else "[DOC]"
        if doc_id:
            header += f" (doc_id={doc_id})"
        chunk_parts.append(header)
        if meta_lines:
            chunk_parts.append(meta_lines)
        if content_text:
            chunk_parts.append(f"[CONTENT]\n{content_text}")

        chunk = "\n".join(chunk_parts).strip()
        if not chunk:
            continue

        tok = _approx_token_len(chunk)
        if total_tok + tok > int(token_budget):
            if content_text and len(content_text) > 200:
                content_text2 = _safe_str(content_text, max_chars=200)
                chunk_parts2 = [header]
                if meta_lines:
                    chunk_parts2.append(meta_lines)
                chunk_parts2.append(f"[CONTENT]\n{content_text2}")
                chunk2 = "\n".join(chunk_parts2).strip()
                tok2 = _approx_token_len(chunk2)
                if total_tok + tok2 <= int(token_budget):
                    chunk = chunk2
                    tok = tok2
                else:
                    break
            else:
                break

        out_chunks.append(chunk)
        total_tok += tok
        used += 1

        refs.append(
            {
                "doc_id": doc_id,
                "title": title,
                "score": float(getattr(p, "score", 0.0) or 0.0),
                "urls": urls,
                "systems": systems,
                "source_table": str(pl.get("tag") or ""),
            }
        )

    context = "\n\n---\n\n".join(out_chunks).strip()
    return context, refs


# Backward-compat builder
def build_context(points: Sequence[Any], max_items: int = _CTX_MAX_ITEMS) -> Tuple[str, List[Dict[str, Any]]]:
    return build_context_docstyle(points, max_items=max_items)


def build_context_mixed(
        points: Sequence[Any],
        max_items: int = _CTX_MAX_ITEMS,
        *,
        query_text: str = "",
        token_budget: int = _CTX_TOKEN_BUDGET,
        per_doc_char_budget: int = _CTX_PER_DOC_CHARS,
        output_type: Optional[str] = None,
        fieldset_keys: Optional[Iterable[str]] = None,
        meta_source: Optional[str] = None,
        include_meta_long: Optional[bool] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    return build_context_docstyle(
        points,
        max_items=max_items,
        token_budget=token_budget,
        per_doc_char_budget=per_doc_char_budget,
        query_text=query_text,
        output_type=output_type,
        fieldset_keys=fieldset_keys,
        meta_source=meta_source,
        include_meta_long=include_meta_long,
    )
