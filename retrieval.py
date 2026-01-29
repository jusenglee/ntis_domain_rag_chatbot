#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from qdrant_client import QdrantClient
from qdrant_client.http import models


_ARRAY_PART_RE = re.compile(r"^(?P<k>.+)\[\]$")
# ---------------------------------------------------------------------
# Sparse (BM25) query support
# - If Qdrant has a named sparse vector (e.g., "bm25"), we can query it directly.
# - Query sparse vector is generated via fastembed (if available).
# - If fastembed is unavailable, we return empty sparse results (no legacy lexical fallback).
# ---------------------------------------------------------------------
_SPARSE_ENCODER = None

def _encode_sparse_query(text: str, *, model_name: str):
    """Encode query text into Qdrant SparseVector using fastembed if present."""
    if not text:
        return None
    try:
        from fastembed import SparseTextEmbedding  # type: ignore
    except Exception as e:  # pragma: no cover
        logger.warning(f"[retrieval] SparseText encode failed: {e}")
        return None

    global _SPARSE_ENCODER
    try:
        if _SPARSE_ENCODER is None or getattr(_SPARSE_ENCODER, "model_name", None) != model_name:
            _SPARSE_ENCODER = SparseTextEmbedding(model_name=model_name)

        # fastembed expects a list[str] and yields SparseEmbedding (indices/values)
        emb = next(_SPARSE_ENCODER.embed([text]))
        idx = emb.indices.tolist() if hasattr(emb.indices, "tolist") else list(emb.indices)
        val = emb.values.tolist() if hasattr(emb.values, "tolist") else list(emb.values)
        if not idx or not val:
            return None
        return models.SparseVector(indices=idx, values=val)
    except Exception as e:  # pragma: no cover
        logger.warning(f"[retrieval] SparseText encode failed: {e}")
        return None


def _qdrant_sparse_search(
    client: Any,
    *,
    collection_name: str,
    query_text: str,
    sparse_vector_name: str,
    limit: int,
    query_filter: Any = None,
    with_payload: Any = True,
):
    """Search Qdrant using a named sparse vector (BM25) if possible."""
    if not sparse_vector_name:
        return []
    model_name = str(os.getenv("RAG_SPARSE_EMBED_MODEL", "Qdrant/bm25")).strip() or "Qdrant/bm25"
    sv = _encode_sparse_query(query_text, model_name=model_name)
    if sv is None:
        return []

    qv = models.NamedSparseVector(
        name=str(sparse_vector_name),
        vector=sv,
    )
    logger.warning(
        "[retrieval] sparse_search query_points args types: "
        "using=%r(%s) limit=%r(%s) timeout=%r(%s) with_payload=%r(%s) nnz=%r filter=%s",
        sparse_vector_name, type(sparse_vector_name).__name__,
        limit, type(limit).__name__,
        _DEFAULT_QDRANT_TIMEOUT, type(_DEFAULT_QDRANT_TIMEOUT).__name__,
        with_payload, type(with_payload).__name__,
        qv,
        type(query_filter).__name__,
    )
    # qdrant-client API differs by version: filter vs query_filter
    try:
        res = client.query_points(
            collection_name=collection_name,
            query=qv,
            using=str(sparse_vector_name),
            limit=int(limit),
            with_payload=with_payload,
            with_vectors=False,
            query_filter=query_filter,
            timeout=int(_DEFAULT_QDRANT_TIMEOUT),
        )
        return list(getattr(res, "points", []) or [])
    except Exception as e:  # pragma: no cover
        logger.warning(f"[retrieval] client.search failed: {e}")
        return None

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
# Query normalization / keywords
# =========================

_SPACE_RE = re.compile(r"\s+")
_HANGUL_RE = re.compile(r"[\u3131-\u318E\uAC00-\uD7A3]")


def normalize_query(q: str) -> str:
    """Normalize user query: strip + collapse whitespaces."""
    if q is None:
        return ""
    q = str(q).strip()
    q = _SPACE_RE.sub(" ", q)
    return q


def _strip_whitespace_korean(text: str) -> str:
    if not text or not _HANGUL_RE.search(text):
        return ""
    return re.sub(r"\s+", "", text)


_KW_RE = re.compile(r"[\w\u3131-\u318E\uAC00-\uD7A3]+", re.UNICODE)


def extract_keywords(q: str, *, max_keywords: int = 12) -> List[str]:
    """Deterministic keyword extractor (간단 룰)."""
    text = normalize_query(q)
    if not text:
        return []

    raw = _KW_RE.findall(text)
    if not raw:
        return []

    stop = {
        "은", "는", "이", "가", "을", "를", "에", "의", "와", "과", "도", "만",
        "좀", "좀요", "주세요", "알려줘", "알려주세요",
        "방법", "절차", "어떻게", "무엇", "어떤", "관련",
    }

    out: List[str] = []
    seen: set[str] = set()
    for t in raw:
        tt = t.strip()
        if not tt:
            continue
        if tt in stop or tt.lower() in stop:
            continue
        key = tt.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tt)
        if len(out) >= max_keywords:
            break
    return out


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
) -> Dict[str, Any]:
    """Run dense retrieval for multiple named vectors + sparse retrieval."""
    timings = timings if timings is not None else {}
    with_payload_dense_spase = _with_payload_selector(_PAYLOAD_MODE_DENSE, _PAYLOAD_MIN_FIELDS)
    q = normalize_query(expanded_text)
    if not q:
        return {"dense": {}, "lexical": []}

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

    for vec_name, emb in (emb_map or {}).items():
        t0 = time.perf_counter()
        v = embed_query(emb, q)
        t_embed = time.perf_counter() - t0
        t_embed_sum += t_embed
        timings[f"dense_embed_{vec_name}"] = t_embed
        if not v:
            continue

        t0 = time.perf_counter()
        logger.warning(
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
                    with_payload=with_payload_dense_spase,
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
            for p in pts:
                _set_payload_hint(p, collection_name, vec_name)
            dense[vec_name] = pts

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
        try:
            top_k_lexical = int(sparse_topk)
        except Exception as e:
            top_k_lexical = int(top_k_lexical)
            logger.warning(f"[retrieval] [sparse_topk is failed] -> top_k_lexical = int(top_k_lexical) : {e}")

    t_lex0 = time.perf_counter()
    lex_points: List[models.ScoredPoint] = []

    lexical_fields_eff = list(
        lexical_fields
        or [
            "title_text",
            "content_text",
            "keyword_text",
            "flat_text",
            "category",
            "prtcp_mp[].hm_nm",
            "prtcp_mp[].blng_org_nm",
            "prtcp_org[].org_nm",
        ]
    )
    with_payload_lex = _with_payload_selector(
        _PAYLOAD_MODE_LEX,
        _PAYLOAD_MIN_FIELDS,
        lexical_fields_eff,
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
            lex_cand = len(sp_hits)
    timings["lexical_sparse"] = time.perf_counter() - t_sparse0
    timings["lexical_candidates"] = float(lex_cand)
    timings["lexical_scored"] = float(len(lex_points))
    timings["lexical_total"] = time.perf_counter() - t_lex0

    return {"dense": dense, "lexical": lex_points}


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
]

_META_LONG_KEYS = {
    "rsch_abstract",
    "rsch_goal_abstract",
}


def _merge_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key in ("meta_basic", "meta_detail"):
        v = payload.get(key)
        if isinstance(v, dict):
            merged.update(v)
    return merged


def _select_meta_fields(meta: Dict[str, Any], query_text: str) -> List[str]:
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

    if _CTX_INCLUDE_META_LONG:
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
) -> Tuple[str, List[Dict[str, Any]]]:
    out_chunks: List[str] = []
    refs: List[Dict[str, Any]] = []

    total_tok = 0
    used = 0

    for p in points or []:
        if used >= int(max_items):
            break

        pl = p.payload if isinstance(getattr(p, "payload", None), dict) else {}
        if not pl:
            continue

        meta = _merge_meta(pl)
        title = _safe_str(
            pl.get("title_text")
            or pl.get("title")
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

        content_text = _safe_str(
            pl.get("content_text")
            or pl.get("content1")
            or pl.get("content2")
            or "",
            max_chars=per_doc_char_budget,
        )
        meta_full = dict(meta)
        for key in ("category", "cetegory", "org_nm", "stan_yr", "start_dt", "end_dt", "dt1", "dt2", "keyword_text"):
            if key not in meta_full and key in pl and pl.get(key) not in (None, ""):
                meta_full[key] = pl.get(key)
        meta_keys = _select_meta_fields(meta_full, query_text=query_text)
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
) -> Tuple[str, List[Dict[str, Any]]]:
    return build_context_docstyle(
        points,
        max_items=max_items,
        token_budget=token_budget,
        per_doc_char_budget=per_doc_char_budget,
        query_text=query_text,
    )
