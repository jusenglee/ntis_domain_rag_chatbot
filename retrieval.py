#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retrieval.py (refactored)

NTIS/일반 문서형 RAG 검색 모듈.

핵심 목표
1) 멀티-벡터 dense 검색 + 텍스트(lexical) 후보를 결합해 RRF로 재정렬
2) 토큰/컨텍스트 폭발을 줄이기 위해 *컨텍스트 빌더*를 "예산 기반"으로 구성

설계 포인트
- Qdrant MatchText는 '필터' 성격이 강하므로(스코어 없음) scroll로 후보를 모은 뒤,
  클라이언트에서 lexical_score를 계산해 가중치 합산합니다.
- rag_pipeline.py가 S3 query_filter(qdrant Filter)를 전달할 수 있게 파라미터를 유지합니다.

호환성
- rag_pipeline.py가 import 하는 함수 이름/시그니처를 유지합니다:
  normalize_query, extract_keywords,
  dense_retrieve_hybrid_multi, rrf_rerank_multi,
  build_context_mixed (alias), build_context
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from qdrant_client import QdrantClient
from qdrant_client.http import models

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover
    fuzz = None


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

# 0이면 meta의 긴 텍스트(요약류)를 기본적으로 제외
_CTX_INCLUDE_META_LONG = os.getenv("RAG_CTX_INCLUDE_META_LONG", "0") == "1"

# Qdrant call timeout (seconds)
_DEFAULT_QDRANT_TIMEOUT = float(os.getenv("RAG_QDRANT_TIMEOUT", "12.0"))


# =========================
# Exact-match helpers for ID-like queries
# =========================

# long numeric ids (exclude years)
_ID_NUM_RE = re.compile(r"\b\d{6,}\b")
# hyphen/slash ids (biz reg etc.)
_ID_HYPHEN_RE = re.compile(r"\b\d{2,}[-/]\d{2,}[-/]\d{2,}\b")
# ISSN
_ID_ISSN_RE = re.compile(r"\b\d{4}-\d{3}[0-9Xx]\b")
# DOI core form
_ID_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s;]+", re.IGNORECASE)
# common NTIS-ish RST_ID style (e.g., JNL-2019-00111061650)
_ID_RST_RE = re.compile(r"\b[A-Za-z]{2,5}-\d{2,6}-\d{6,}\b")

# DOI full URL prefix
_DOI_URL_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)



# =========================
# Payload selector (min/full)
# =========================
_PAYLOAD_MODE_DENSE = os.getenv("RAG_PAYLOAD_MODE_DENSE", "min").strip().lower()  # min|full|true
_PAYLOAD_MODE_LEX   = os.getenv("RAG_PAYLOAD_MODE_LEX", "min").strip().lower()    # min|full|true

# 후보 단계에 필요한 최소 키들(메타/본문 제외)
_PAYLOAD_MIN_FIELDS = [s.strip() for s in os.getenv(
    "RAG_PAYLOAD_MIN_FIELDS",
    "doc_id,tag,title_text,title,title1,title2,content_text,keyword_text,flat_text,category,cetegory,org_nm,org_name_norm,pjt_id,meta_basic,meta_detail,meta_flat,urls,systems"
).split(",") if s.strip()]

# 최종 컨텍스트용(필요하면 meta/answer_public 포함)
_PAYLOAD_FULL_FIELDS = [s.strip() for s in os.getenv(
    "RAG_PAYLOAD_FULL_FIELDS",
    "doc_id,tag,title_text,title,title1,title2,content_text,content1,content2,keyword_text,keyword1,keyword2,flat_text,category,cetegory,meta,meta_basic,meta_detail,meta_flat,answer_public,org_nm,org_name_norm,pjt_id,stan_yr,start_dt,end_dt,dt1,dt2,urls,systems"
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
        except Exception:
            return True
    # default: min
    try:
        return models.PayloadSelectorInclude(include=merged_fields)
    except Exception:
        return True



def _extract_id_tokens(q: str, *, cap: int = 8) -> List[str]:
    """Extract ID-like tokens from query text for exact matching.

    - 필드 열거 없이(meta_flat 등) 동작하도록 토큰 기반으로 설계.
    - ISSN 하이픈/무하이픈, DOI URL/코어형을 같이 인식합니다.
    """
    q = (q or "").strip()
    if not q:
        return []

    toks: List[str] = []

    # DOI: allow URL form too (normalize later)
    for m in _ID_DOI_RE.finditer(q):
        toks.append(m.group(0))

    # also capture doi.org/... if user pasted full URL
    for m in re.finditer(r"https?://(dx\.)?doi\.org/[^\s;]+", q, flags=re.IGNORECASE):
        toks.append(m.group(0))

    # ISSN / RST / hyphen ids
    toks += [m.group(0) for m in _ID_ISSN_RE.finditer(q)]
    toks += [m.group(0) for m in _ID_RST_RE.finditer(q)]
    toks += [m.group(0) for m in _ID_HYPHEN_RE.finditer(q)]

    # long numeric ids
    for m in _ID_NUM_RE.finditer(q):
        t = m.group(0)
        # ignore year-like numbers
        if len(t) == 4 and t.isdigit() and 1900 <= int(t) <= 2099:
            continue
        toks.append(t)

    # normalize / de-dup while preserving order
    seen = set()
    out: List[str] = []
    for t in toks:
        t = t.strip()
        if not t:
            continue

        # DOI normalize: url -> core
        if _DOI_URL_PREFIX_RE.search(t):
            t = _DOI_URL_PREFIX_RE.sub("", t).strip()
        # strip trailing punctuation
        t = t.rstrip(").,;]}>")

        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
        # ISSN normalization: meta_flat이 표준형(하이픈 포함)을 가진다는 전제라면,
        # 무하이픈 변형을 추가로 넣지 않는다(오탐/확장 방지).

        # DOI normalization: add url form? (not needed for exact; we normalize text too)
        if _ID_DOI_RE.fullmatch(t):
            # also add lowercase variant for safety (matching will be case-insensitive)
            tl = t.lower()
            if tl not in seen:
                seen.add(tl)
                out.append(tl)

        if len(out) >= cap:
            break

    return out


def _contains_token_exact(text: str, token: str) -> bool:
    """True if token appears in text under "identifier-safe" equivalence.

    - ISSN: hyphen/none-hyphen equivalence
    - DOI: doi.org URL prefix equivalence, case-insensitive
    - Digits: enforce digit boundaries to avoid substring matches
    """
    if not text or not token:
        return False

    t = token.strip()
    if not t:
        return False

    # ISSN equivalence: 1738-2270 <-> 17382270
    if _ID_ISSN_RE.fullmatch(t) or (t.isdigit() and len(t) == 8):
        t_norm = t.replace("-", "")
        text_norm = text.replace("-", "")
        return t_norm in text_norm

    # DOI equivalence: https://doi.org/10.... <-> 10....
    # We compare in lowercase and strip URL prefix in both sides.
    t_l = t.lower()
    if _DOI_URL_PREFIX_RE.search(t_l):
        t_l = _DOI_URL_PREFIX_RE.sub("", t_l).strip()
    if _ID_DOI_RE.fullmatch(t_l) or t_l.startswith("10."):
        text_l = text.lower()
        text_l = text_l.replace("dx.doi.org/", "doi.org/")  # minor normalization
        text_l2 = _DOI_URL_PREFIX_RE.sub("", text_l)
        return (t_l in text_l) or (t_l in text_l2)

    # Pure digits: boundary-safe
    if t.isdigit():
        return re.search(rf"(?<!\d){re.escape(t)}(?!\d)", text) is not None

    # General tokens: try non-alnum boundaries; fall back to substring
    return (
            re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", text) is not None
            or t in text
    )


def _safe_str(x: Any, *, max_chars: Optional[int] = None) -> str:
    if x is None:
        return ""
    s = str(x)
    if max_chars is not None and len(s) > max_chars:
        return s[: max_chars - 1] + "…"
    return s


def _payload_get(pl: Dict[str, Any], key: str, default: Any = "") -> Any:
    """Get nested payload value. (meta.xxx 지원)"""
    if not isinstance(pl, dict):
        return default
    if "." not in key:
        return pl.get(key, default)
    cur: Any = pl
    for part in key.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
        if cur is None:
            return default
    return cur


def _id_exact_score(
        query_text: str,
        payload: Dict[str, Any],
        fields: Sequence[str],
        weights: Dict[str, float],
) -> float:
    """Exact-match score for ID-like queries using only payload fields (meta_flat etc.).

    로직(중요):
    - ID 토큰이 존재하는 질의는 *fuzzy*로 내려가면 오염(허위 양성)이 급증함.
    - 따라서 "동치 정규화 포함 exact"만으로 점수화하고,
      exact=0이면 0으로 종료하는 상위 로직을 권장.
    """
    toks = _extract_id_tokens(query_text)
    if not toks:
        return 0.0

    found = {t: False for t in toks}
    base = 0.0

    for f in fields:
        txt = _safe_str(_payload_get(payload, f, ""), max_chars=_SNIP_MAX_CHARS)
        if not txt:
            continue
        w = float(weights.get(f, 1.0))

        any_hit = False
        kv_hit = False

        for t in toks:
            if found[t]:
                continue
            if _contains_token_exact(txt, t):
                found[t] = True
                any_hit = True
                # KV style bonus: "...: <token>"
                if re.search(rf":\s*{re.escape(t)}(?![0-9A-Za-z])", txt, flags=re.IGNORECASE):
                    kv_hit = True

        if any_hit:
            base += 0.9 * w
            if kv_hit:
                base += 0.2 * w

    hit_cnt = sum(1 for v in found.values() if v)
    if hit_cnt == 0:
        return 0.0

    # if all tokens matched somewhere, bump confidence.
    if hit_cnt == len(found):
        base *= 1.5
    else:
        base *= (0.6 + 0.4 * (hit_cnt / max(1, len(found))))

    return float(base)


# =========================
# Query normalization / keywords
# =========================

_SPACE_RE = re.compile(r"\s+")


def normalize_query(q: str) -> str:
    """Normalize user query: strip + collapse whitespaces."""
    if q is None:
        return ""
    q = str(q).strip()
    q = _SPACE_RE.sub(" ", q)
    return q


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
    """Annotate payload with collection/vector hints for debug/dedup."""
    try:
        if not isinstance(point.payload, dict):
            return
        point.payload.setdefault("_collection", collection)
        if vec_name:
            point.payload.setdefault("_vec", vec_name)
    except Exception:
        return


# =========================
# Lexical scoring
# =========================

_TOKEN_RE = re.compile(r"[A-Za-z]+|[0-9]+|[가-힣]+", re.UNICODE)


def _tokenize_simple(text: str) -> List[str]:
    if not text:
        return []
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def _fuzzy_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if fuzz is None:
        aset = set(a.lower().split())
        bset = set(b.lower().split())
        if not aset or not bset:
            return 0.0
        return len(aset & bset) / len(aset | bset)
    return float(fuzz.partial_ratio(a, b)) / 100.0


def lexical_score_weighted(
        query_text: str,
        payload: Dict[str, Any],
        fields: Sequence[str],
        weights: Dict[str, float],
        *,
        systems_hint: Optional[Sequence[str]] = None,
        lexical_scoring_mode: str = "bm25",
) -> float:
    """Compute a weighted lexical score.

    로직(중요):
    - ID 토큰(ISSN/DOI/긴 숫자/하이픈 번호/RST_ID 등)이 있는 질의는
      fuzzy 점수를 허용하면 허위 양성이 급증하므로,
      *동치 정규화 포함 exact 매칭*만 허용합니다.
    """
    qt = normalize_query(query_text)
    if not qt:
        return 0.0

    id_toks = _extract_id_tokens(qt)
    if id_toks:
        exact = _id_exact_score(qt, payload, fields, weights)
        if exact > 0.0:
            # small system hint boost
            if systems_hint:
                ql = qt.lower()
                for s in systems_hint:
                    if s and s.lower() in ql:
                        exact += 0.05
                        break
            return float(exact)
        # ✅ ID 질의인데 exact=0이면 fuzzy로 내려가지 않는다.
        return 0.0

    mode = (lexical_scoring_mode or "bm25").strip().lower()
    query_tokens = _tokenize_simple(qt)
    if not query_tokens:
        return 0.0

    field_tokens: Dict[str, List[str]] = {}
    field_counts: Dict[str, Dict[str, int]] = {}
    for f in fields:
        txt = _safe_str(_payload_get(payload, f, ""), max_chars=_SNIP_MAX_CHARS)
        tokens = _tokenize_simple(txt)
        if not tokens:
            continue
        field_tokens[f] = tokens
        counts: Dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1
        field_counts[f] = counts

    if not field_tokens:
        return 0.0

    # BM25-like (field-level) scoring
    num_fields = len(field_tokens)
    avg_dl = sum(len(toks) for toks in field_tokens.values()) / float(max(1, num_fields))
    df: Dict[str, int] = {}
    for t in set(query_tokens):
        df[t] = sum(1 for toks in field_tokens.values() if t in toks)

    k1 = 1.2
    b = 0.75

    bm25_score = 0.0
    qtf: Dict[str, int] = {}
    for t in query_tokens:
        qtf[t] = qtf.get(t, 0) + 1

    for f, counts in field_counts.items():
        dl = float(len(field_tokens.get(f, [])))
        if dl <= 0:
            continue
        w = float(weights.get(f, 1.0))
        denom_norm = k1 * (1.0 - b + b * (dl / max(1.0, avg_dl)))
        for t, qt_count in qtf.items():
            tf = float(counts.get(t, 0))
            if tf <= 0:
                continue
            df_t = float(df.get(t, 0))
            idf = math.log(1.0 + (num_fields - df_t + 0.5) / (df_t + 0.5))
            bm25_score += w * idf * ((tf * (k1 + 1.0)) / (tf + denom_norm)) * float(qt_count)

    base = 0.0
    if mode in ("bm25", "bm25_only"):
        base = bm25_score
    elif mode in ("mix", "hybrid", "bm25_fuzzy"):
        fuzzy_score = 0.0
        total_w = 0.0
        for f in fields:
            txt = _safe_str(_payload_get(payload, f, ""), max_chars=_SNIP_MAX_CHARS)
            if not txt:
                continue
            w = float(weights.get(f, 1.0))
            total_w += w
            fuzzy_score += w * _fuzzy_ratio(qt, txt)
        fuzzy_norm = fuzzy_score / max(1.0, total_w)
        bm25_norm = bm25_score / (1.0 + bm25_score)
        base = (0.65 * bm25_norm) + (0.35 * fuzzy_norm)
    else:
        # fallback: fuzzy only
        for f in fields:
            txt = _safe_str(_payload_get(payload, f, ""), max_chars=_SNIP_MAX_CHARS)
            if not txt:
                continue
            w = float(weights.get(f, 1.0))
            base += w * _fuzzy_ratio(qt, txt)

    if systems_hint:
        ql = qt.lower()
        for s in systems_hint:
            if s and s.lower() in ql:
                base += 0.05
                break

    return float(base)


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
        query_filter: Optional[models.Filter] = None,
        timings: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Run dense retrieval for multiple named vectors + lexical retrieval."""
    timings = timings if timings is not None else {}

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
        with_payload_dense = _with_payload_selector(_PAYLOAD_MODE_DENSE, _PAYLOAD_MIN_FIELDS)
        try:
            try:
                res = client.query_points(
                    collection_name=collection_name,
                    query=v,
                    using=vec_name,
                    limit=int(top_k_dense),
                    with_payload=with_payload_dense,
                    with_vectors=False,
                    query_filter=query_filter,
                    timeout=_DEFAULT_QDRANT_TIMEOUT,
                )
            except TypeError:
                res = client.query_points(
                    collection_name=collection_name,
                    query=v,
                    using=vec_name,
                    limit=int(top_k_dense),
                    with_payload=with_payload_dense,
                    with_vectors=False,
                    query_filter=query_filter,
                )
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
    # Lexical retrieval (MatchText filter + client-side scoring)
    # -----------------------
    t_lex0 = time.perf_counter()
    lex_points: List[models.ScoredPoint] = []

    lexical_fields_eff = list(
        lexical_fields or ["title_text", "content_text", "keyword_text", "flat_text", "title", "answer_public", "meta_flat"]
    )
    lexical_weights_eff = dict(
        lexical_field_weights
        or {
            "title_text": 2.2,
            "content_text": 1.2,
            "keyword_text": 0.8,
            "flat_text": 0.35,
            "title": 2.2,
            "answer_public": 1.2,
            "meta_flat": 0.35,
        }
    )

    t_scroll = 0.0
    t_score = 0.0
    lex_cand = 0
    lex_scored = 0

    if keywords:
        t0 = time.perf_counter()

        # ID 토큰이 있으면: scroll 단계에서도 "AND 문자열"이 아니라 "OR 토큰"으로 후보를 모은다.
        id_toks = _extract_id_tokens(q, cap=4)

        if id_toks:
            top_k_lexical_candidates_eff = min(int(top_k_lexical_candidates), 120)
        else:
            top_k_lexical_candidates_eff = int(top_k_lexical_candidates)

        should_conds: List[models.FieldCondition] = []

        if id_toks:
            # ✅ 필드×토큰으로 should(OR) 구성
            for f in lexical_fields_eff:
                for t in id_toks:
                    should_conds.append(models.FieldCondition(key=f, match=models.MatchText(text=str(t)[:128])))
        else:
            query_text = " ".join(keywords[:12]).strip() or q
            if len(query_text) > 128:
                query_text = query_text[:128]
            for f in lexical_fields_eff:
                should_conds.append(models.FieldCondition(key=f, match=models.MatchText(text=query_text)))

        lex_filter = models.Filter(should=should_conds)
        final_filter = _combine_filters(query_filter, lex_filter)
        lex_base_fields = _PAYLOAD_FULL_FIELDS if _PAYLOAD_MODE_LEX == "full" else _PAYLOAD_MIN_FIELDS
        with_payload_lex = _with_payload_selector(
            _PAYLOAD_MODE_LEX,
            lex_base_fields,
            lexical_fields_eff,
        )
        try:
            try:
                scroll_res, _ = client.scroll(
                    collection_name=collection_name,
                    scroll_filter=final_filter,
                    limit=int(top_k_lexical_candidates_eff),
                    with_payload=with_payload_lex,
                    with_vectors=False,
                    timeout=_DEFAULT_QDRANT_TIMEOUT,
                )
            except TypeError:
                scroll_res, _ = client.scroll(
                    collection_name=collection_name,
                    scroll_filter=final_filter,
                    limit=int(top_k_lexical_candidates_eff),
                    with_payload=with_payload_lex,
                    with_vectors=False,
                )
            cand = list(scroll_res or [])
        except Exception as e:  # pragma: no cover
            logger.warning(f"[retrieval] lexical scroll failed: col={collection_name}: {e}")
            cand = []

        t_scroll = time.perf_counter() - t0
        timings["lexical_scroll"] = t_scroll
        lex_cand = len(cand)

        # scoring
        t0 = time.perf_counter()

        systems_hint = None
        for p in cand:
            if not isinstance(getattr(p, "payload", None), dict):
                continue
            systems_hint = p.payload.get("systems") if isinstance(p.payload.get("systems"), list) else None
            break

        scored: List[models.ScoredPoint] = []
        for p in cand:
            pl = p.payload if isinstance(p.payload, dict) else {}
            if not pl:
                continue
            s = lexical_score_weighted(
                q,
                pl,
                lexical_fields_eff,
                lexical_weights_eff,
                systems_hint=systems_hint,
                lexical_scoring_mode=lexical_scoring_mode,
            )
            if s <= 0:
                continue
            sp = _make_scored_point_from_payload(pid=p.id, payload=pl, score=s, version=getattr(p, "version", 0))
            _set_payload_hint(sp, collection_name, "lex")
            scored.append(sp)

        scored.sort(key=lambda x: float(getattr(x, "score", 0.0)), reverse=True)
        lex_points = scored[: int(top_k_lexical)]

        t_score = time.perf_counter() - t0
        timings["lexical_score"] = t_score
        lex_scored = len(scored)

    timings["lexical_candidates"] = float(lex_cand)
    timings["lexical_scored"] = float(lex_scored)
    timings["lexical_total"] = time.perf_counter() - t_lex0

    return {"dense": dense, "lexical": lex_points}


# =========================
# RRF rerank
# =========================

def _rrf_score(rank: int, rrf_k: int) -> float:
    return 1.0 / float(rrf_k + rank)


def rrf_rerank_multi(
        search_res: Dict[str, Any],
        *,
        k: int = _DEFAULT_RERANK_K,
        rrf_k: int = _DEFAULT_RRF_K,
        w_dense_map: Optional[Dict[str, float]] = None,
        w_lex: float = 0.25,
        query_text: str = "",
) -> List[models.ScoredPoint]:
    """RRF across dense(vecs) and lexical list."""
    w_dense_map = dict(w_dense_map or {"e5i_qa": 1.0, "e5_qa": 0.8})
    dense_map: Dict[str, List[Any]] = search_res.get("dense") or {}
    lexical_list: List[Any] = search_res.get("lexical") or []

    acc: Dict[Tuple[str, str], Tuple[float, models.ScoredPoint]] = {}

    def _key(p: Any) -> Tuple[str, str]:
        pl = p.payload if isinstance(getattr(p, "payload", None), dict) else {}
        doc_id = str(pl.get("doc_id") or "")
        col = str(pl.get("_collection") or "")
        if doc_id:
            return ("doc", doc_id)
        return (col, str(getattr(p, "id", "")))

    # dense
    for dense_name, pts in dense_map.items():
        if not pts:
            continue
        vec_name = str(dense_name).split("@", 1)[0]
        w = float(w_dense_map.get(vec_name, 1.0))
        for i, p in enumerate(pts, start=1):
            kk = _key(p)
            add = w * _rrf_score(i, rrf_k)
            if kk in acc:
                s0, p0 = acc[kk]
                acc[kk] = (s0 + add, p0)
            else:
                acc[kk] = (add, p)

    # lexical
    for i, p in enumerate(lexical_list, start=1):
        kk = _key(p)
        add = float(w_lex) * _rrf_score(i, rrf_k)
        if kk in acc:
            s0, p0 = acc[kk]
            acc[kk] = (s0 + add, p0)
        else:
            acc[kk] = (add, p)

    ranked = sorted(acc.values(), key=lambda x: x[0], reverse=True)
    out: List[models.ScoredPoint] = []
    for s, p in ranked[: int(k)]:
        try:
            p.score = float(s)
        except Exception:
            pass
        out.append(p)
    return out


# =========================
# Context builder (docstyle)
# =========================

_META_CORE_KEYS = [
    "pjt_id",
    "PJT_ID",
    "PJT_NO",
    "과제번호",
    "source_pk",
    "source_table",
    "doc_type",
    "기준년도",
    "STAN_YR",
    "org_nm",
    "KOR_PJT_NM",
    "ENG_PJT_NM",
    "국문과제명",
    "영문과제명",
    "PJT_PRFRM_ORG_NM",
    "과제수행기관명",
    "연구수행주체",
    "TOT_RSCH_START_DT",
    "TOT_RSCH_END_DT",
    "start_dt",
    "end_dt",
    "dt1",
    "dt2",
    "총연구기간시작일",
    "총연구기간종료일",
    "연구비합계금액",
    "연구개발단계",
    "국가중점과학기술",
    "6T관련기술",
    "keyword_text",
    "RST_ID",
    "등록번호",
    "성과명",
    "논문명",
    "발명의명칭",
]

_META_LONG_KEYS = {"연구내용요약", "연구목표요약", "내용", "요약", "RSCH_ABSTRACT", "RSCH_GOAL_ABSTRACT"}


def _merge_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key in ("meta", "metadata", "meta_basic", "meta_detail"):
        v = payload.get(key)
        if isinstance(v, dict):
            merged.update(v)
    return merged


def _select_meta_fields(meta: Dict[str, Any], query_text: str) -> List[str]:
    if not isinstance(meta, dict) or not meta:
        return []

    ql = (query_text or "").lower()
    out: List[str] = []

    def _add(k: str) -> None:
        if k in meta and k not in out:
            out.append(k)

    for k in _META_CORE_KEYS:
        _add(k)

    if any(x in ql for x in ["기간", "시작", "종료", "언제"]):
        _add("총연구기간시작일")
        _add("총연구기간종료일")
        _add("TOT_RSCH_START_DT")
        _add("TOT_RSCH_END_DT")
        _add("start_dt")
        _add("end_dt")
    if any(x in ql for x in ["금액", "연구비", "예산", "비용"]):
        _add("연구비합계금액")
    if any(x in ql for x in ["기관", "대학", "출연", "주관"]):
        _add("과제수행기관명")
        _add("연구수행주체")
        _add("PJT_PRFRM_ORG_NM")
        _add("org_nm")
    if any(x in ql for x in ["분야", "기술", "6t", "중점"]):
        _add("국가중점과학기술")
        _add("6T관련기술")
    if any(x in ql for x in ["키워드", "keyword"]):
        _add("한글키워드")
        _add("영문키워드")
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
            or meta.get("KOR_PJT_NM")
            or meta.get("국문과제명")
            or "",
            max_chars=200,
        )
        doc_id = _safe_str(pl.get("doc_id") or "", max_chars=160)
        systems = pl.get("systems") if isinstance(pl.get("systems"), list) else []
        urls = pl.get("urls") if isinstance(pl.get("urls"), list) else []

        answer_public = _safe_str(
            pl.get("content_text")
            or pl.get("content1")
            or pl.get("content2")
            or pl.get("answer_public")
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
        if answer_public:
            chunk_parts.append(f"[CONTENT]\n{answer_public}")

        chunk = "\n".join(chunk_parts).strip()
        if not chunk:
            continue

        tok = _approx_token_len(chunk)
        if total_tok + tok > int(token_budget):
            if answer_public and len(answer_public) > 200:
                answer_public2 = _safe_str(answer_public, max_chars=200)
                chunk_parts2 = [header]
                if meta_lines:
                    chunk_parts2.append(meta_lines)
                chunk_parts2.append(f"[CONTENT]\n{answer_public2}")
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
                "source_table": _payload_get(pl, "meta.source_table", "") or _payload_get(pl, "meta_basic.source_table", ""),
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
