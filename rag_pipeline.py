# -*- coding: utf-8 -*-
"""
rag_pipeline.py (redesigned)

핵심 목표
- SEARCH / LOOKUP / JOIN 을 모드로 분리해 "필터의 역할"을 설계로 고정한다.
- SEARCH: 모든 컬렉션에서 얇고 넓게 후보 탐색 -> (약한 RRF) + (강한 키워드/소프트필터)로 최종 랭킹
- LOOKUP(list/stats/download, id query 등): 서버단 필터로 후보군을 먼저 좁힘 -> 소프트 랭킹으로 마무리
- JOIN(2-hop): Hop1=SEARCH로 join-key 확보 -> Hop2=JOIN 필터로 강제 제한 + 소프트 랭킹

의존
- build_rag_objects_dual(): qdr/emb 2종
- dense_retrieve_hybrid_multi(): dense+lexical 후보를 dict로 반환
- build_context_mixed(): 문서형 컨텍스트 빌더
- (옵션) rag_parts/* 유틸들
"""
from __future__ import annotations

import os
import re
import time
import inspect
import json
from pprint import pformat
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from settings import DEFAULT_MODEL_NAME, logger, MAX_TOKENS, get_ctx_token_budget
from rag_types import RagResult
from rag_store import build_rag_objects_dual
from retrieval import (
    normalize_query,
    extract_keywords,
    dense_retrieve_hybrid_multi,
    build_context_mixed,
)

# -------------------------
# rag_parts imports
# -------------------------
from rag_parts.constants import (
    KEY_ORG_NORM,
    COL_SUPPORT,
    COL_PROJECT,
    COL_PERF,
    TAG_PJT_INFO,
    TAG_PJT_MP,
    TAG_PJT_ORG,
)
from rag_parts.query_intent import (
    QueryIntent,
    classify_query as _classify_query,
)
from rag_parts.search_preset import (
    SearchPreset as _SearchPreset,
    build_search_preset as _build_search_preset,
)
from rag_parts.vecsets import named_vectors_in_collection as _named_vectors_in_collection
from rag_parts.post_policy import (
    dedup_by_doc_id as _dedup_by_doc_id,
    norm_tag_from_payload as _norm_tag_from_payload,
    tag_match_bonus as _tag_match_bonus,
)
from rag_parts.join import (
    extract_pjt_ids as _extract_pjt_ids,
    sanitize_query_by_terms as _sanitize_query_by_terms,
)
from rag_parts.filters import (
    extract_org_terms as _extract_org_terms,
    build_org_filter as _build_org_filter,
    build_people_filter as _build_people_filter,
    build_tag_only_filter as _build_tag_only_filter,
    build_join_filter as build_join_filter,
    build_perf_filter as build_perf_filter,
    and_filter as _and_filter,
)

try:
    from qdrant_client.http import models as qmodels
except Exception:
    qmodels = None
# =====================================================================
# Pretty / Section Logging (RAG)  ✅✅ 상세 로그 트래킹 유틸
# =====================================================================

def _rag_debug_on() -> bool:
    return str(os.getenv("RAG_DEBUG", "0")).strip().lower() in ("1", "true", "yes", "y")

def _rag_color_on() -> bool:
    # 파일 로깅이면 ANSI가 지저분할 수 있으니 기본 OFF
    return str(os.getenv("RAG_LOG_COLOR", "0")).strip().lower() in ("1", "true", "yes", "y")

def _clip_text(s: str, max_chars: int) -> str:
    if s is None:
        return ""
    s = str(s)
    if max_chars > 0 and len(s) > max_chars:
        return s[: max_chars - 1] + "…(trunc)"
    return s

def _safe_json(obj: object) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return pformat(obj, width=120, compact=True)

def log_section(title: str, content: object = None, *, level: str = "info", max_chars: int = None) -> None:
    """
    RAG_DEBUG=1 일 때만 출력.
    - content: str/dict/list/anything
    - max_chars: 환경변수 RAG_LOG_MAX_CHARS(기본 6000)로 제한
    """
    if not _rag_debug_on():
        return

    lvl = (level or "info").lower().strip()
    log_fn = getattr(logger, lvl, logger.info)

    max_chars = int(max_chars) if max_chars is not None else int(os.getenv("RAG_LOG_MAX_CHARS", "6000"))

    if content is None:
        body = ""
    elif isinstance(content, str):
        body = content
    else:
        body = _safe_json(content)

    body = _clip_text(body, max_chars)

    if _rag_color_on():
        header = f"\n\033[96m{'='*10} [{title}] {'='*10}\033[0m"
        footer = f"\033[96m{'='*36}\033[0m\n"
    else:
        header = f"\n{'='*10} [{title}] {'='*10}"
        footer = f"{'='*36}\n"

    log_fn(f"{header}\n{body}\n{footer}")

def log_kv(title: str, *, level: str = "info", **kwargs) -> None:
    """key=value를 한 섹션으로 예쁘게."""
    if not _rag_debug_on():
        return
    payload = {}
    for k, v in kwargs.items():
        if isinstance(v, str):
            payload[k] = _clip_text(v, int(os.getenv("RAG_LOG_KV_STR_MAX", "240")))
        else:
            payload[k] = v
    log_section(title, payload, level=level)

def _point_summary(p: Any) -> Dict[str, Any]:
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        pl = {}
    meta = pl.get("meta") if isinstance(pl.get("meta"), dict) else {}

    def pick(*vals):
        for v in vals:
            if v is None:
                continue
            s = str(v).strip()
            if s:
                return s
        return ""

    title = pick(
        pl.get("title"),
        meta.get("국문과제명"), meta.get("과제명"),
        meta.get("성과명"), meta.get("논문명"),
        meta.get("title"),
    )

    tag = pick(pl.get("tag"), meta.get("doc_type"), meta.get("source_table"))
    doc_id = pick(pl.get("doc_id"), getattr(p, "id", None))
    col = pick(pl.get("_collection"))

    sc = getattr(p, "score", None)
    try:
        sc = float(sc) if sc is not None else None
    except Exception:
        sc = None

    return {
        "col": col,
        "doc_id": doc_id,
        "tag": tag,
        "score": sc,
        "_rrf": pl.get("_rrf"),
        "_final_total": pl.get("_final_total"),
        "_final_rrf": pl.get("_final_rrf"),
        "_final_kw": pl.get("_final_kw"),
        "_final_f": pl.get("_final_f"),
        "title": _clip_text(title, int(os.getenv("RAG_LOG_TITLE_MAX", "180"))),
    }

def log_top_points(title: str, points: List[Any], *, topn: int = None, level: str = "info") -> None:
    """후보/리랭크 결과 TopN 요약."""
    if not _rag_debug_on():
        return
    topn = int(topn) if topn is not None else int(os.getenv("RAG_LOG_TOPN", "8"))
    arr = []
    for p in (points or [])[: max(0, topn)]:
        arr.append(_point_summary(p))
    log_section(title, arr, level=level)

# =====================================================================

# -------------------------
# Lightweight list/stats context
# -------------------------
def _classify_query_compat(q: str, kws: List[str], *, domain_hint: Optional[str], hint: Optional[Dict[str, Any]] = None) -> "QueryIntent":
    """query_intent.classify_query signature 호환 래퍼."""
    try:
        sig = inspect.signature(_classify_query)
        if hint is not None and "hint" in sig.parameters:
            return _classify_query(q, kws, domain_hint=domain_hint, hint=hint)
        return _classify_query(q, kws, domain_hint=domain_hint)
    except Exception:
        return _classify_query(q, kws, domain_hint=domain_hint)


def _clean_one_line(s: object, max_len: int = 160) -> str:
    if s is None:
        return ""
    t = str(s).replace("\r", " ").replace("\n", " ").strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) > max_len:
        t = t[: max_len - 1] + "…"
    return t

def _get_meta(pl: dict) -> dict:
    m = pl.get("meta") if isinstance(pl.get("meta"), dict) else {}
    return m if isinstance(m, dict) else {}

def _pick_first(*vals: object) -> str:
    for v in vals:
        if v is None:
            continue
        sv = str(v).strip()
        if sv:
            return sv
    return ""

def build_context_list_light(
        points: List[Any],
        *,
        kind: str,
        max_items: int,
        query_text: str = "",
) -> Tuple[str, List[Dict[str, Any]]]:
    """목록/통계형 질의용 경량 컨텍스트."""
    items: List[str] = []
    kind = (kind or "").lower().strip() or "project"

    def _pjt_id(meta, pl):
        return _pick_first(pl.get("pjt_id"), meta.get("PJT_ID"), meta.get("pjt_id"), meta.get("pjtId"), meta.get("PJTID"))

    def _refs(points: List[Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for p in (points or [])[: max(0, int(max_items))]:
            pl = getattr(p, "payload", None) or {}
            if not isinstance(pl, dict):
                pl = {}
            meta = _get_meta(pl)
            urls = pl.get("urls") or meta.get("urls") or []
            if isinstance(urls, str):
                urls = [urls]
            out.append({
                "doc_id": str(pl.get("doc_id") or ""),
                "tag": str(pl.get("tag") or meta.get("doc_type") or meta.get("source_table") or ""),
                "title": str(pl.get("title") or meta.get("국문과제명") or meta.get("성과명") or meta.get("논문명") or ""),
                "urls": urls if isinstance(urls, list) else [],
            })
        return out

    for p in (points or [])[: max(0, int(max_items))]:
        pl = getattr(p, "payload", None) or {}
        if not isinstance(pl, dict):
            continue
        meta = _get_meta(pl)

        if kind == "project":
            title = _pick_first(pl.get("title"), meta.get("국문과제명"), meta.get("과제명"), meta.get("title"))
            pjt_id = _pjt_id(meta, pl)
            org = _pick_first(meta.get("과제수행기관명"), meta.get("주관기관명"), meta.get("기관명"), meta.get("수행기관"))
            year = _pick_first(meta.get("연구개발기간시작년도"), meta.get("연도"), meta.get("시작년도"))
            line = f"- {_clean_one_line(title, 180)}"
            extra: List[str] = []
            if pjt_id: extra.append(f"PJT_ID={pjt_id}")
            if org: extra.append(_clean_one_line(org, 60))
            if year: extra.append(str(year))
            if extra: line += " (" + ", ".join(extra) + ")"
            items.append(line)
            continue

        if kind == "people":
            name = _pick_first(meta.get("인물명"), meta.get("참여연구자명"), meta.get("연구자명"), meta.get("성명"), meta.get("이름"), meta.get("NAME"), meta.get("name"))
            role = _pick_first(meta.get("역할"), meta.get("참여구분"), meta.get("참여유형"), meta.get("역할명"))
            org = _pick_first(meta.get("소속기관명"), meta.get("소속"), meta.get("기관명"), meta.get("참여기관명"))
            pjt_id = _pjt_id(meta, pl)
            line = f"- {_clean_one_line(name or '(이름없음)', 80)}"
            extra: List[str] = []
            if role: extra.append(_clean_one_line(role, 30))
            if org: extra.append(_clean_one_line(org, 50))
            if pjt_id: extra.append(f"PJT_ID={pjt_id}")
            if extra: line += " (" + ", ".join(extra) + ")"
            items.append(line)
            continue

        if kind == "org":
            org = _pick_first(meta.get("참여기관명"), meta.get("기관명"), meta.get("수행기관명"), meta.get("주관기관명"), meta.get("ORG_NAME"), meta.get("org"))
            role = _pick_first(meta.get("기관역할"), meta.get("역할"), meta.get("참여구분"), meta.get("유형"))
            pjt_id = _pjt_id(meta, pl)
            line = f"- {_clean_one_line(org or '(기관없음)', 100)}"
            extra: List[str] = []
            if role: extra.append(_clean_one_line(role, 30))
            if pjt_id: extra.append(f"PJT_ID={pjt_id}")
            if extra: line += " (" + ", ".join(extra) + ")"
            items.append(line)
            continue

        # perf default
        title = _pick_first(pl.get("title"), meta.get("성과명"), meta.get("논문명"), meta.get("프로그램명"), meta.get("특허명"), meta.get("title"))
        pjt_name = _pick_first(meta.get("국문과제명"), meta.get("과제명"))
        pjt_id = _pjt_id(meta, pl)
        perf_type = _pick_first(pl.get("tag"), meta.get("doc_type"), meta.get("source_table"), meta.get("성과유형"))
        year = _pick_first(meta.get("성과연도"), meta.get("발행년도"), meta.get("연도"))
        journal = _pick_first(meta.get("저널명"), meta.get("학술지명"), meta.get("발행처"))

        line = f"- {_clean_one_line(title, 180)}"
        extra: List[str] = []
        if perf_type: extra.append(_clean_one_line(perf_type, 32))
        if year: extra.append(str(year))
        if journal: extra.append(_clean_one_line(journal, 40))
        if pjt_name or pjt_id: extra.append(_clean_one_line(pjt_name or f"PJT_ID={pjt_id}", 60))
        if extra: line += " (" + ", ".join(extra) + ")"
        items.append(line)

    header = f"질의: {_clean_one_line(query_text, 120)}\n" if query_text else ""
    ctx = header + ("\n".join(items) if items else "(후보 없음)")
    return ctx, _refs(points)

# -------------------------
# Precomputed embedding wrapper
# -------------------------
class _PrecomputedEmbedding:
    def __init__(self, vec: List[float]):
        self._vec = vec
    def get_query_embedding(self, _text: str):
        return self._vec
    def get_text_embedding(self, _text: str):
        return self._vec

# -------------------------
# signature-safe wrapper
# -------------------------
_DENSE_MULTI_SIG = None
try:
    _DENSE_MULTI_SIG = inspect.signature(dense_retrieve_hybrid_multi)
except Exception:
    _DENSE_MULTI_SIG = None

def _call_dense_retrieve_hybrid_multi(
        *,
        qdr: Any,
        emb_map: Dict[str, Any],
        qtext: str,
        kws: List[str],
        collection: str,
        lexical_fields: List[str],
        lexical_field_weights: Dict[str, float],
        lexical_scoring_mode: str,
        top_k_dense: int,
        top_k_lex_cand: int,
        top_k_lex: int,
        query_filter: Any,
        timings_out: Dict[str, float],
) -> Dict[str, Any]:
    params = set(_DENSE_MULTI_SIG.parameters.keys()) if _DENSE_MULTI_SIG else set()

    if "client" in params and "collection_name" in params:
        return dense_retrieve_hybrid_multi(
            client=qdr,
            emb_map=emb_map,
            expanded_text=qtext,
            keywords=kws,
            collection_name=collection,
            lexical_fields=lexical_fields,
            lexical_field_weights=lexical_field_weights,
            lexical_scoring_mode=lexical_scoring_mode,
            top_k_dense=top_k_dense,
            top_k_lexical_candidates=top_k_lex_cand,
            top_k_lexical=top_k_lex,
            query_filter=query_filter,
            timings=timings_out,
        )

    if "qdr" in params and "collection" in params:
        return dense_retrieve_hybrid_multi(
            qdr=qdr,
            collection=collection,
            query_text=qtext,
            keywords=kws,
            emb_map=emb_map,
            lexical_fields=lexical_fields,
            lexical_field_weights=lexical_field_weights,
            lexical_scoring_mode=lexical_scoring_mode,
            top_k_dense=top_k_dense,
            top_k_lexical_candidates=top_k_lex_cand,
            top_k_lexical=top_k_lex,
            filter_obj=query_filter,
            timings=timings_out,
        )

    # last resort
    return dense_retrieve_hybrid_multi(
        client=qdr,
        emb_map=emb_map,
        expanded_text=qtext,
        keywords=kws,
        collection_name=collection,
        lexical_fields=lexical_fields,
        lexical_field_weights=lexical_field_weights,
        lexical_scoring_mode=lexical_scoring_mode,
        top_k_dense=top_k_dense,
        top_k_lexical_candidates=top_k_lex_cand,
        top_k_lexical=top_k_lex,
        query_filter=query_filter,
        timings=timings_out,
    )

def _ensure_collection_mark(points: List[Any], col: str) -> None:
    for p in points or []:
        pl = getattr(p, "payload", None)
        if isinstance(pl, dict):
            pl.setdefault("_collection", col)

# -------------------------
# RRF (independent / stable)
# -------------------------
@dataclass
class _RankSource:
    name: str
    weight: float
    points: List[Any]

def _hit_key(p: Any) -> Tuple[str, str]:
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        pl = {}
    col = str(pl.get("_collection") or "")
    pid = str(getattr(p, "id", "") or pl.get("doc_id") or "")
    return (col, pid)

def _rrf_merge(sources: List[_RankSource], *, rrf_k: int = 60, keep: int = 2000) -> List[Any]:
    """sources 내의 랭킹을 RRF로 합친다(가중치 지원)."""
    score: Dict[Tuple[str, str], float] = {}
    best_obj: Dict[Tuple[str, str], Any] = {}
    for src in sources:
        w = float(src.weight)
        for r, p in enumerate(src.points or []):
            k = _hit_key(p)
            if k not in best_obj:
                best_obj[k] = p
            score[k] = score.get(k, 0.0) + (w / float(rrf_k + r + 1))

    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    out: List[Any] = []
    for k, sc in ranked[: max(1, int(keep))]:
        p = best_obj.get(k)
        if p is None:
            continue
        pl = getattr(p, "payload", None)
        if isinstance(pl, dict):
            pl["_rrf"] = float(sc)
        out.append(p)
    return out

# -------------------------
# Keyword / Filter soft rerank
# -------------------------
def _to_text(v: object) -> str:
    if v is None:
        return ""
    s = str(v).replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()

def _prefer_meta_title(pl: Dict[str, Any], meta: Dict[str, Any]) -> str:
    title = _to_text(pl.get("title") or "")
    meta_title = _to_text(
        meta.get("국문과제명")
        or meta.get("성과명")
        or meta.get("논문명")
        or meta.get("title")
        or ""
    )
    if not title:
        return meta_title
    pjt_id = _to_text(pl.get("pjt_id") or meta.get("PJT_ID") or "")
    if meta_title and (
        title.isdigit()
        or title.lower().startswith("ntis:")
        or (pjt_id and title == pjt_id)
    ):
        return meta_title
    return title or meta_title

def _payload_text_bundle(p: Any) -> Dict[str, str]:
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        pl = {}
    meta = _get_meta(pl)

    title = _prefer_meta_title(pl, meta)
    meta_flat = _to_text(pl.get("meta_flat") or meta.get("meta_flat") or "")
    answer_public = _to_text(pl.get("answer_public") or pl.get("content") or meta.get("answer_public") or "")

    meta_kv = []
    for k in ("PJT_ID", "pjt_id", "과제수행기관명", "주관기관명", "기관명", "연도", "성과연도", "발행년도", "저널명", "학술지명"):
        if k in meta and meta.get(k) not in (None, ""):
            meta_kv.append(f"{k}:{_to_text(meta.get(k))}")
    meta_kv_s = _to_text("; ".join(meta_kv))[:800]

    return {
        "title": title,
        "meta_flat": meta_flat[:2000],
        "answer_public": answer_public[:2000],
        "meta_kv": meta_kv_s,
    }

def _count_term_hits(text: str, term: str) -> int:
    if not text or not term:
        return 0
    return text.lower().count(term.lower())

def _keyword_score(p: Any, kws: List[str], w: Dict[str, float]) -> float:
    tb = _payload_text_bundle(p)
    w_title = float(w.get("title", 2.0))
    w_meta = float(w.get("meta_flat", 0.6))
    w_ans = float(w.get("answer_public", 1.0))

    sc = 0.0
    for kw in (kws or [])[:30]:
        kw = kw.strip()
        if not kw:
            continue
        sc += w_title * min(_count_term_hits(tb["title"], kw), 2)
        sc += w_meta * min(_count_term_hits(tb["meta_flat"], kw), 4)
        sc += w_ans * min(_count_term_hits(tb["answer_public"], kw), 3)
        sc += 0.4 * min(_count_term_hits(tb["meta_kv"], kw), 2)
    return float(sc)

def _flatten_ids_from_intent(it: Any) -> List[str]:
    # ids_flat 우선, 없으면 ids_map/ids(dict) 평탄화
    flat = getattr(it, "ids_flat", None)
    if isinstance(flat, list) and flat:
        out = []
        for x in flat:
            s = str(x).strip()
            if s and s not in out:
                out.append(s)
        return out

    m = getattr(it, "ids_map", None)
    if not isinstance(m, dict):
        m = getattr(it, "ids", None)
    if not isinstance(m, dict):
        return []

    out = []
    for _, lst in m.items():
        if not isinstance(lst, list):
            continue
        for x in lst:
            s = str(x).strip()
            if s and s not in out:
                out.append(s)
    return out

def _filter_score(p: Any, it: QueryIntent, base_route: str, *, strict_ids: bool) -> float:
    tb = _payload_text_bundle(p)
    hay = " | ".join([tb["title"], tb["meta_flat"], tb["meta_kv"], tb["answer_public"]]).lower()

    sc = 0.0

    # IDs
    flat_ids = _flatten_ids_from_intent(it)
    for _id in flat_ids[:10]:
        if _id.lower() in hay:
            sc += 120.0
        else:
            sc += (-45.0 if strict_ids else -12.0)

    # years
    years = [str(y).strip() for y in (it.years or []) if str(y).strip()]
    for y in years[:6]:
        if y.lower() in hay:
            sc += 35.0

    # org
    org_terms = [t.strip() for t in (it.org_terms or []) if t.strip()]
    for ot in org_terms[:4]:
        if ot.lower() in hay:
            sc += 60.0
        elif strict_ids:
            sc -= 10.0

    # people
    people_terms = [t.strip() for t in (it.people_terms or []) if t.strip()]
    for pt in people_terms[:4]:
        if pt.lower() in hay:
            sc += 70.0
        elif strict_ids:
            sc -= 15.0

    # perf tag filters (exact) - base_route와 무관하게 적용
    if it.perf_tag_filters:
        pl = getattr(p, "payload", None) or {}
        if not isinstance(pl, dict):
            pl = {}
        meta = _get_meta(pl)
        tag = str(pl.get("tag") or meta.get("doc_type") or meta.get("source_table") or "")
        for t in list(it.perf_tag_filters)[:8]:
            if str(t) == tag:
                sc += 90.0
                break

    # project tag filters (exact) - base_route와 무관하게 적용
    if it.project_tag_filters:
        pl = getattr(p, "payload", None) or {}
        if not isinstance(pl, dict):
            pl = {}
        meta = _get_meta(pl)
        tag = str(pl.get("tag") or meta.get("doc_type") or meta.get("source_table") or "")
        for t in list(it.project_tag_filters)[:8]:
            if str(t) == tag:
                sc += 80.0
                break

    return float(sc)

def _family_bonus(p: Any, base_route: str) -> float:
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        return 0.0
    col = str(pl.get("_collection") or "")
    if base_route == "support" and col == COL_SUPPORT:
        return 6.0
    if base_route in ("project", "people", "org") and col == COL_PROJECT:
        return 4.0
    if base_route == "perf" and col == COL_PERF:
        return 4.0
    return 0.0

def _final_rerank(
        cands: List[Any],
        *,
        it: QueryIntent,
        kws: List[str],
        lex_w: Dict[str, float],
        base_route: str,
        mode: str,
        keep: int,
        tag_boost: float = 0.0,
        tag_mismatch_penalty: float = 0.0,
) -> List[Any]:
    if not cands:
        return []

    if mode in ("search", "lookup") and it.people_terms and base_route == "people":
        terms = [t.strip() for t in (it.people_terms or []) if t.strip()][:2]
        if terms:
            matched = [p for p in cands if _must_contain_terms(p, terms)]
            if matched:
                min_keep = max(2, min(int(keep), 5))
                if len(matched) >= min_keep:
                    cands = matched

    # mode별 가중치 (경험적으로 튜닝 가능)
    if mode == "lookup":
        w_rrf, w_kw, w_f = 0.55, 0.70, 1.35
        strict_ids = True
    elif mode == "join":
        w_rrf, w_kw, w_f = 0.45, 0.65, 1.60
        strict_ids = True
    else:  # search
        w_rrf, w_kw, w_f = 0.85, 1.00, 0.75
        strict_ids = False

    scored = []
    for idx, p in enumerate(cands):
        pl = getattr(p, "payload", None)
        rrf_sc = float(pl.get("_rrf", 0.0)) if isinstance(pl, dict) else 0.0
        kw_sc = _keyword_score(p, kws, lex_w)
        f_sc = _filter_score(p, it, base_route, strict_ids=strict_ids)
        fam = _family_bonus(p, base_route)
        tag_sc = _tag_match_bonus(
            p,
            tag_filters=getattr(it, "tag_filters", None),
            boost=tag_boost,
            mismatch_penalty=tag_mismatch_penalty,
        )
        tot = (w_rrf * rrf_sc) + (w_kw * kw_sc) + (w_f * f_sc) + fam + tag_sc

        if isinstance(pl, dict):
            pl["_final_rrf"] = rrf_sc
            pl["_final_kw"] = kw_sc
            pl["_final_f"] = f_sc
            pl["_final_tag"] = tag_sc
            pl["_final_total"] = tot

        scored.append((-tot, idx, p))

    scored.sort(key=lambda x: (x[0], x[1]))
    out = [x[2] for x in scored[: max(1, int(keep))]]
    return out

# -------------------------
# Plan
# -------------------------
@dataclass
class QueryPlan:
    mode: str  # "search" | "lookup" | "join"
    base_route: str
    action: str
    relation: Optional[Tuple[str, str]]
    target_collections: List[str]
    # server-side filters by collection (optional)
    filters: Dict[str, Any]

def _has_any_ids(it: QueryIntent) -> bool:
    ids_map = getattr(it, "ids_map", None)
    if isinstance(ids_map, dict) and any(v for v in ids_map.values() if v):
        return True
    ids_flat = getattr(it, "ids_flat", None)
    if isinstance(ids_flat, list) and len(ids_flat) > 0:
        return True
    # backward compat
    legacy = getattr(it, "ids", None)
    if isinstance(legacy, dict) and any(v for v in legacy.values() if v):
        return True
    return False

def _build_plan(it: QueryIntent) -> QueryPlan:
    action = it.action
    base_route = it.base_route
    rel = it.relation

    if rel:
        return QueryPlan(
            mode="join",
            base_route=base_route,
            action=action,
            relation=rel,
            target_collections=[COL_PROJECT, COL_PERF, COL_SUPPORT],
            filters={},
        )

    if action in ("list", "stats", "download") or bool(it.is_id_query) or _has_any_ids(it):
        return QueryPlan(
            mode="lookup",
            base_route=base_route,
            action=action,
            relation=None,
            target_collections=[COL_PROJECT, COL_PERF, COL_SUPPORT],
            filters={},
        )

    return QueryPlan(
        mode="search",
        base_route=base_route,
        action=action,
        relation=None,
        target_collections=[COL_PROJECT, COL_PERF, COL_SUPPORT],
        filters={},
    )

# -------------------------
# 2-hop JOIN helpers
# -------------------------
def _extract_quoted_terms(q: str) -> List[str]:
    if not q:
        return []
    out = re.findall(r'"([^"]+)"', q) + re.findall(r"'([^']+)'", q)
    return [t.strip() for t in out if t.strip()]

def _must_contain_terms(p: Any, terms: List[str]) -> bool:
    tb = _payload_text_bundle(p)
    hay = " | ".join([tb["title"], tb["meta_flat"], tb["meta_kv"], tb["answer_public"]]).lower()
    for t in (terms or []):
        if t.strip().lower() not in hay:
            return False
    return True


# -------------------------
# Main
# -------------------------
def _hydrate_points_payload(
        qdr: Any,
        points: List[Any],
        *,
        include_fields: Optional[List[str]] = None,
        chunk_size: int = 128,
) -> None:
    """
    points의 (collection, id) 기준으로 Qdrant retrieve를 돌려 payload를 갱신.
    - 후보 단계에서는 meta/answer_public을 빼고,
      최종 topN에서만 meta/answer_public을 채우는 용도.
    """
    if not points:
        return
    if qmodels is None or not hasattr(qdr, "retrieve"):
        return

    include_fields = include_fields or [
        "doc_id", "title", "tag",
        "meta", "meta_flat", "answer_public",
        "org_name_norm", "pjt_id",
        "urls", "systems",
    ]
    selector = qmodels.PayloadSelectorInclude(include=include_fields)

    # group by collection
    by_col: Dict[str, List[Any]] = {}
    for p in points:
        pl = getattr(p, "payload", None)
        if not isinstance(pl, dict):
            continue
        col = str(pl.get("_collection") or "")
        if not col:
            continue
        by_col.setdefault(col, []).append(p)

    for col, pts in by_col.items():
        # ids chunk
        ids = []
        for p in pts:
            pid = getattr(p, "id", None)
            if pid is None:
                continue
            ids.append(pid)

        t0 = time.perf_counter()
        for i in range(0, len(ids), int(chunk_size)):
            sub = ids[i:i+int(chunk_size)]
            try:
                recs = qdr.retrieve(
                    collection_name=col,
                    ids=sub,
                    with_payload=selector,
                    with_vectors=False,
                )
            except TypeError:
                recs = qdr.retrieve(
                    collection_name=col,
                    ids=sub,
                    with_payload=selector,
                    with_vectors=False,
                )

            # id -> payload
            mp: Dict[str, Dict[str, Any]] = {}
            for r in (recs or []):
                rid = getattr(r, "id", None)
                rpl = getattr(r, "payload", None)
                if rid is None or not isinstance(rpl, dict):
                    continue
                mp[str(rid)] = rpl

            for p in pts:
                pid = getattr(p, "id", None)
                if pid is None:
                    continue
                upd = mp.get(str(pid))
                if not upd:
                    continue
                pl = getattr(p, "payload", None)
                if isinstance(pl, dict):
                    # 기존 _collection/_rrf/_final_* 등은 유지되게 update만
                    keep_col = pl.get("_collection")
                    pl.update(upd)
                    pl["_collection"] = keep_col or col

        dt = time.perf_counter() - t0
        log_kv("RAG.HYDRATE", col=col, n=len(pts), seconds=dt, fields=include_fields[:10])


def _run_rag_with_vectors(
        *,
        query: str,
        model_name: str,
        hint: Any = None,
        stack: str,
        vector_names: List[str],
        w_dense_map: Dict[str, float],
        lexical_fields: Optional[List[str]] = None,
        lexical_field_weights: Optional[Dict[str, float]] = None,
        lexical_scoring_mode: Optional[str] = None,
) -> RagResult:
    t_all0 = time.time()
    timings: Dict[str, float] = {}

    q = normalize_query(query)
    if not q:
        return RagResult(
            stack=stack, keywords=[], hits=[], reranked_hits=[], context="", refs=[],
            timings={"total": 0.0, "fallback_chat": 1.0, "fallback_reason": "empty_query"}
        )

    # shared objects
    t0 = time.time()
    qdr_a, emb_a, _, qdr_b, emb_b, _ = build_rag_objects_dual()
    qdr = qdr_a
    timings["stack_init"] = time.time() - t0

    fallback_emb: Dict[str, Any] = {"e5i_qa": emb_a, "e5_qa": emb_b}

    def _get_attr(obj: Any, name: str, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    def _category_to_base_route(cats: List[Any]) -> str:
        norm = set()
        for c in (cats or []):
            s = str(c)
            s = s.replace("ContentCategory.", "").strip().lower()
            norm.add(s)

        if norm == {"project"}:
            return "project"
        if norm == {"performance"}:
            return "perf"
        if norm == {"researcher"}:
            return "people"
        if norm in ({"qna"}, {"qnt"}, {"qna "}, {"q&a"}):
            return "support"

        return "mixed"

    def _coerce_int(x: Any, default: int) -> int:
        try:
            return int(x)
        except Exception:
            return default

    # --- hint 적용 (single-pass) ---
    qa = hint
    qa_conf = float(_get_attr(qa, "confidence", 0.0) or 0.0)

    hinted_base = None
    hinted_limit = 0

    if qa and qa_conf >= float(os.getenv("RAG_HINT_MIN_CONF", "0.55")):
        q_for_retrieval = normalize_query(_get_attr(qa, "retrieval_query", "") or "") or q
        hinted_base = _category_to_base_route(_get_attr(qa, "category", []) or [])
        hinted_limit = _coerce_int(_get_attr(qa, "limit", 0), 0)
    else:
        q_for_retrieval = q

    # ✅ 표준 q 확정
    q = q_for_retrieval

    # -------------------------
    # ids_map / ids_flat 안전 접근 유틸
    # -------------------------
    log_kv(
        "RAG.INPUT",
        raw_query=query,
        normalized=q,
        hint_conf=qa_conf,
        hinted_base=hinted_base,
        hinted_limit=hinted_limit,
        model_name=model_name,
        stack=stack,
        vector_names=vector_names,
    )

    # keywords
    t0 = time.time()
    kws = extract_keywords(q)
    timings["kw_det"] = time.time() - t0

    # intent (hint는 query_intent에서 흡수)
    domain_hint = hinted_base if hinted_base in ("project", "perf", "people", "support") else None
    it = _classify_query_compat(q, kws, domain_hint=domain_hint, hint=hint)
    action = it.action
    base_route = it.base_route
    relation = it.relation

    # budget (for ctx builder)
    ctx_budget = int(get_ctx_token_budget(model_name, max_output_tokens=MAX_TOKENS))
    timings["ctx_budget"] = float(ctx_budget)

    # preset (topK etc)
    preset: _SearchPreset = _build_search_preset(it)
    lexical_fields_eff = list(lexical_fields) if lexical_fields is not None else list(preset.lexical_fields)
    lex_w_eff = dict(lexical_field_weights) if lexical_field_weights is not None else dict(preset.lexical_field_weights)
    lex_scoring_mode_eff = (lexical_scoring_mode or preset.lexical_scoring_mode or "bm25").strip().lower()

    if hinted_limit > 0:
        preset.top_k_lex_cand = min(int(preset.top_k_lex_cand), hinted_limit * 20)
        preset.top_k_lex = min(int(preset.top_k_lex), max(10, hinted_limit * 2))
        preset.max_ctx_items = min(int(preset.max_ctx_items), hinted_limit)
    # org terms/filter (필요 시)
    org_terms = [t.strip() for t in (list(it.org_terms or []) or _extract_org_terms(q, kws) or []) if str(t).strip()]
    it.org_terms = org_terms
    org_filter = _build_org_filter(org_terms) if org_terms else None

    # people terms/filter (필요 시)
    people_terms = [t.strip() for t in (list(it.people_terms or []) or []) if str(t).strip()]
    it.people_terms = people_terms
    people_ids = list((getattr(it, "ids_map", None) or {}).get("person_no") or [])
    people_filter = _build_people_filter(people_terms, people_ids) if (people_terms or people_ids) else None

    # perf tag filter (필요 시)
    perf_tag_filter = _build_tag_only_filter(list(it.perf_tag_filters)) if it.perf_tag_filters else None

    # -------------------------
    # 상세 로그: INTENT / PRESET / KEYWORDS
    # -------------------------
    log_section("RAG.KEYWORDS", kws)
    log_kv(
        "RAG.INTENT",
        action=action,
        base_route=base_route,
        relation=relation,
        domain_hint=domain_hint,
        is_id_query=getattr(it, "is_id_query", None),
        years=getattr(it, "years", None),
        people_terms=list(getattr(it, "people_terms", []) or []),
        org_terms=list(getattr(it, "org_terms", []) or []),
        perf_tag_filters=list(getattr(it, "perf_tag_filters", []) or []),
        tag_filters=list(getattr(it, "tag_filters", []) or []),
        ids_flat=_flatten_ids_from_intent(it)[:20],
    )
    log_kv(
        "RAG.PRESET/PLAN.PRE",
        top_k_dense=int(preset.top_k_dense),
        top_k_lex_cand=int(preset.top_k_lex_cand),
        top_k_lex=int(preset.top_k_lex),
        w_lex=float(preset.w_lex),
        tag_boost=float(getattr(preset, "tag_boost", 0.0)),
        tag_mismatch_penalty=float(getattr(preset, "tag_mismatch_penalty", 0.0)),
        max_ctx_items=int(preset.max_ctx_items),
        lexical_scoring_mode=lex_scoring_mode_eff,
        ctx_budget=int(ctx_budget),
    )

    # precompute embedding cache
    pre_vecs_cache: Dict[str, Dict[str, _PrecomputedEmbedding]] = {}

    def _get_pre_vecs(qtext: str) -> Dict[str, _PrecomputedEmbedding]:
        qtext = qtext or ""
        if qtext in pre_vecs_cache:
            return pre_vecs_cache[qtext]

        out: Dict[str, _PrecomputedEmbedding] = {}
        try:
            if "e5i_qa" in vector_names:
                v = emb_a.get_query_embedding(qtext) if hasattr(emb_a, "get_query_embedding") else emb_a.get_text_embedding(qtext)
                if hasattr(v, "tolist"):
                    v = v.tolist()
                out["e5i_qa"] = _PrecomputedEmbedding(list(v))
            if "e5_qa" in vector_names:
                v = emb_b.get_query_embedding(qtext) if hasattr(emb_b, "get_query_embedding") else emb_b.get_text_embedding(qtext)
                if hasattr(v, "tolist"):
                    v = v.tolist()
                out["e5_qa"] = _PrecomputedEmbedding(list(v))
        except Exception as e:
            timings["embed_precompute_error"] = 1.0
            logger.warning(f"[RAG] embed precompute failed (q='{qtext[:40]}'): {e}")
            out = {}

        pre_vecs_cache[qtext] = out
        return out

    pre_vecs = _get_pre_vecs(q)

    # plan
    plan = _build_plan(it)

    log_kv(
        "RAG.PRESET/PLAN.POST",
        mode=plan.mode,
        base_route=plan.base_route,
        action=plan.action,
        relation=plan.relation,
        target_cols=plan.target_collections,
    )

    # -------------------------
    # JOIN mode (2-hop)
    # -------------------------
    if plan.mode == "join" and relation:
        t_hop0 = time.time()
        pjt_ids = (getattr(it, "ids_map", None) or getattr(it, "ids", None) or {}).get("pjt_id") or []



        # relation mapping
        hop1_col = hop2_col = ""
        hop1_kind = hop2_kind = "project"
        hop1_tag_filters: Optional[List[str]] = None
        hop2_tag_filters: Optional[List[str]] = None
        hop2_label = ""

        if relation == ("project", "perf"):
            hop1_col, hop2_col = COL_PROJECT, COL_PERF
            hop1_kind, hop2_kind = "project", "perf"
            hop1_tag_filters, hop2_tag_filters = [TAG_PJT_INFO], None
            hop2_label = "성과(논문/특허/보고서 등) 목록"
        elif relation == ("project", "people"):
            hop1_col, hop2_col = COL_PROJECT, COL_PROJECT
            hop1_kind, hop2_kind = "project", "people"
            hop1_tag_filters, hop2_tag_filters = [TAG_PJT_INFO], [TAG_PJT_MP]
            hop2_label = "참여인력 목록"
        elif relation == ("project", "org"):
            hop1_col, hop2_col = COL_PROJECT, COL_PROJECT
            hop1_kind, hop2_kind = "project", "org"
            hop1_tag_filters, hop2_tag_filters = [TAG_PJT_INFO], [TAG_PJT_ORG]
            hop2_label = "참여기관 목록"
        elif relation == ("people", "perf"):
            # people -> perf (연구자/참여인력의 성과)
            hop1_col, hop2_col = COL_PROJECT, COL_PERF
            hop1_kind, hop2_kind = "people", "perf"
            hop1_tag_filters = [TAG_PJT_MP]
            hop2_tag_filters = []  # perf 유형은 build_perf_filter에서 q 기반으로 결정
            hop2_label = "연관 성과(논문/특허/보고서 등) 목록"

        elif relation == ("org", "perf"):
            # org -> perf (기관의 성과)
            hop1_col, hop2_col = COL_PROJECT, COL_PERF
            hop1_kind, hop2_kind = "org", "perf"
            hop1_tag_filters = [TAG_PJT_ORG]
            hop2_tag_filters = []
            hop2_label = "연관 성과(논문/특허/보고서 등) 목록"

        elif relation == ("perf", "project"):
            hop1_col, hop2_col = COL_PERF, COL_PROJECT
            hop1_kind, hop2_kind = "perf", "project"
            hop1_tag_filters, hop2_tag_filters = None, [TAG_PJT_INFO]
            hop2_label = "연관 과제(프로젝트) 정보"
        elif relation == ("perf", "people"):
            hop1_col, hop2_col = COL_PERF, COL_PROJECT
            hop1_kind, hop2_kind = "perf", "people"
            hop1_tag_filters, hop2_tag_filters = None, [TAG_PJT_MP]
            hop2_label = "연관 과제의 참여인력 목록"
        elif relation == ("perf", "org"):
            hop1_col, hop2_col = COL_PERF, COL_PROJECT
            hop1_kind, hop2_kind = "perf", "org"
            hop1_tag_filters, hop2_tag_filters = None, [TAG_PJT_ORG]
            hop2_label = "연관 과제의 참여기관 목록"
        elif relation == ("people", "project"):
            hop1_col, hop2_col = COL_PROJECT, COL_PROJECT
            hop1_kind, hop2_kind = "people", "project"
            hop1_tag_filters, hop2_tag_filters = [TAG_PJT_MP], [TAG_PJT_INFO]
            hop2_label = "참여 과제(프로젝트) 목록"
        elif relation == ("org", "project"):
            hop1_col, hop2_col = COL_PROJECT, COL_PROJECT
            hop1_kind, hop2_kind = "org", "project"
            hop1_tag_filters, hop2_tag_filters = [TAG_PJT_ORG], [TAG_PJT_INFO]
            hop2_label = "참여 과제(프로젝트) 목록"
        else:
            # unknown relation -> fall back to base SEARCH
            relation = None

        if relation:
            hop1_keep = int(os.getenv("RAG_HOP1_KEEP", "5"))
            hop2_keep = int(os.getenv("RAG_HOP2_KEEP", "10"))
            hop1_k_base = int(os.getenv("RAG_HOP1_TOPK_BASE", "250"))
            hop2_k_base = int(os.getenv("RAG_HOP2_TOPK_BASE", "300"))

            # Hop1 query sanitize (people/org head에서 잡음 제거)
            hop1_q = q
            if hop1_kind in ("people", "org"):
                remove_terms = list(getattr(it, 'remove_terms_for_head', []) or [])
                if remove_terms:
                    hop1_q2 = _sanitize_query_by_terms(q, remove_terms=remove_terms)
                    if hop1_q2:
                        hop1_q = hop1_q2

            hop2_q = q

            join_ids: List[str] = []
            hop1_top: List[Any] = []

            # 1) Hop1 (SEARCH) : 명시 PJT_ID 있으면 skip
            if pjt_ids:
                join_ids = pjt_ids[:]
                log_kv("RAG.JOIN.HOP1.SKIP", reason="explicit_pjt_ids", join_ids=join_ids[:10])
            else:
                hop1_filter = _build_tag_only_filter(hop1_tag_filters) if hop1_tag_filters else None
                if hop1_kind == "people" and people_filter:
                    hop1_filter = _and_filter(hop1_filter, people_filter)
                if hop1_kind == "org" and org_filter:
                    hop1_filter = _and_filter(hop1_filter, org_filter)

                log_kv(
                    "RAG.JOIN.HOP1",
                    hop1_col=hop1_col, hop1_kind=hop1_kind, hop1_q=hop1_q,
                    hop1_tag_filters=hop1_tag_filters,
                    hop1_filter=str(hop1_filter) if hop1_filter is not None else None,
                    hop1_k_base=hop1_k_base,
                    hop1_keep=hop1_keep,
                )

                vec_avail = _named_vectors_in_collection(qdr, hop1_col)
                use_vecs_h1 = [v for v in vector_names if (not isinstance(vec_avail, set) or v in vec_avail)]
                pre_vecs_h1 = _get_pre_vecs(hop1_q)
                emb_map_h1: Dict[str, Any] = {}
                for vname in use_vecs_h1:
                    pe = pre_vecs_h1.get(vname)
                    emb_map_h1[vname] = pe if pe is not None else fallback_emb.get(vname)
                emb_map_h1 = {k: v for k, v in emb_map_h1.items() if v is not None}

                local_timings_h1: Dict[str, float] = {}
                sr1 = _call_dense_retrieve_hybrid_multi(
                    qdr=qdr,
                    emb_map=emb_map_h1,
                    qtext=hop1_q,
                    kws=kws,
                    collection=hop1_col,
                    lexical_fields=lexical_fields_eff,
                    lexical_field_weights=lex_w_eff,
                    lexical_scoring_mode=lex_scoring_mode_eff,
                    top_k_dense=(preset.top_k_dense if emb_map_h1 else 0),
                    top_k_lex_cand=hop1_k_base,
                    top_k_lex=min(hop1_k_base, 80),
                    query_filter=hop1_filter,  # ✅ 실제 적용
                    timings_out=local_timings_h1,
                )
                _ensure_collection_mark((sr1.get("lexical") or []), hop1_col)
                for _, lst in (sr1.get("dense") or {}).items():
                    _ensure_collection_mark(lst or [], hop1_col)

                # Hop1 RRF merge
                sources_h1: List[_RankSource] = []
                for vname, lst in (sr1.get("dense") or {}).items():
                    sources_h1.append(_RankSource(name=f"{hop1_col}:{vname}", weight=float(w_dense_map.get(vname, 1.0)), points=lst or []))
                sources_h1.append(_RankSource(name=f"{hop1_col}:lex", weight=float(preset.w_lex), points=sr1.get("lexical") or []))
                h1_rrf = _rrf_merge(sources_h1, rrf_k=int(os.getenv("RAG_RRF_K", "60")), keep=500)

                h1_rrf = _dedup_by_doc_id(h1_rrf)
                hop1_reranked = _final_rerank(
                    h1_rrf,
                    it=it,
                    kws=kws,
                    lex_w=lex_w_eff,
                    base_route=("perf" if hop1_kind == "perf" else base_route),
                    mode="search",
                    keep=int(os.getenv("RAG_HOP1_FINAL_KEEP", "40")),
                    tag_boost=float(getattr(preset, "tag_boost", 0.0)),
                    tag_mismatch_penalty=float(getattr(preset, "tag_mismatch_penalty", 0.0)),
                )

                # head term 강제 포함(people/org head일 때만, 옵션)
                head_terms: List[str] = []
                if hop1_kind == "people":
                    head_terms = _extract_quoted_terms(q) or list(it.people_terms or [])
                    if not head_terms:
                        m = re.search(r"([가-힣]{2,4})\s*(?:이|가|은|는)?\s*(?:참여인력|참여연구|연구자|연구원)", q)
                        if m:
                            head_terms = [m.group(1)]
                elif hop1_kind == "org":
                    head_terms = (org_terms or [])[:2]

                if head_terms:
                    head_filtered = [p for p in hop1_reranked if _must_contain_terms(p, head_terms)]
                    if head_filtered:
                        min_keep = max(2, min(int(hop1_keep), 5))
                        if len(head_filtered) >= min_keep:
                            hop1_reranked = head_filtered

                hop1_top = hop1_reranked[: max(1, hop1_keep)]
                # ✅ hop1 상위만 join키 추출을 위해 meta 포함 payload 보강
                _hydrate_points_payload(qdr, hop1_reranked[: max(20, int(os.getenv("RAG_HOP1_FINAL_KEEP", "40")))])

                join_ids = _extract_pjt_ids(hop1_top, max_ids=50)

                log_top_points("RAG.JOIN.HOP1.TOP", hop1_top, topn=int(os.getenv("RAG_LOG_TOPN_HOP1", "6")))
                log_section("RAG.JOIN.JOIN_IDS", join_ids[: min(len(join_ids), 30)])
                log_kv(
                    "RAG.JOIN.HOP1.TIMINGS",
                    **{k: float(v) for k, v in (local_timings_h1 or {}).items()}
                )

            # Hop1 context
            hop1_ctx, hop1_refs = ("", [])
            if hop1_top:
                if action in ("list", "stats", "download"):
                    hop1_ctx, hop1_refs = build_context_list_light(hop1_top, kind=hop1_kind, max_items=max(1, hop1_keep), query_text=hop1_q)
                else:
                    hop1_ctx, hop1_refs = build_context_mixed(hop1_top[: max(1, hop1_keep)], max_items=max(1, hop1_keep), query_text=hop1_q)

            # join_ids 없으면 종료
            if not join_ids:
                context = (
                    f"### [Hop1] 검색 결과 요약\n{hop1_ctx or '(후보 없음)'}\n\n"
                    f"### [Hop2] {hop2_label}\n- 필터 PJT_ID 후보: (없음)\n\n"
                    "(조인 키(PJT_ID)를 추출하지 못해 Hop2를 생략했습니다.)"
                )
                timings["hop_total"] = time.time() - t_hop0
                timings["total"] = time.time() - t_all0
                hits = hop1_top[: max(1, hop1_keep)]
                return RagResult(stack=stack, keywords=kws, hits=hits, reranked_hits=hits, context=context, refs=hop1_refs, timings=timings)

            # 2) Hop2 (LOOKUP/JOIN): JOIN 필터로 강제 제한
            if relation in (("project", "perf"), ("people", "perf"), ("org", "perf")):
                hop2_filter = build_perf_filter(q, join_ids)
            else:
                hop2_filter = build_join_filter(join_ids, tag_filters=hop2_tag_filters)
                if hop2_kind in ("project", "org") and org_filter:
                    hop2_filter = _and_filter(hop2_filter, org_filter)

            log_kv(
                "RAG.JOIN.HOP2",
                hop2_col=hop2_col, hop2_kind=hop2_kind, hop2_q=hop2_q,
                hop2_tag_filters=hop2_tag_filters,
                hop2_filter=str(hop2_filter) if hop2_filter is not None else None,
                hop2_k_base=hop2_k_base,
                hop2_keep=hop2_keep,
                join_ids_preview=join_ids[:10],
            )

            vec_avail2 = _named_vectors_in_collection(qdr, hop2_col)
            use_vecs_h2 = [v for v in vector_names if (not isinstance(vec_avail2, set) or v in vec_avail2)]
            pre_vecs_h2 = _get_pre_vecs(hop2_q)
            emb_map_h2: Dict[str, Any] = {}
            for vname in use_vecs_h2:
                pe = pre_vecs_h2.get(vname)
                emb_map_h2[vname] = pe if pe is not None else fallback_emb.get(vname)
            emb_map_h2 = {k: v for k, v in emb_map_h2.items() if v is not None}

            local_timings_h2: Dict[str, float] = {}
            sr2 = _call_dense_retrieve_hybrid_multi(
                qdr=qdr,
                emb_map=emb_map_h2,
                qtext=hop2_q,
                kws=kws,
                collection=hop2_col,
                lexical_fields=lexical_fields_eff,
                lexical_field_weights=lex_w_eff,
                lexical_scoring_mode=lex_scoring_mode_eff,
                top_k_dense=(preset.top_k_dense if emb_map_h2 else 0),
                top_k_lex_cand=hop2_k_base,
                top_k_lex=min(hop2_k_base, 120),
                query_filter=hop2_filter,  # ✅ JOIN 필터 강제 적용
                timings_out=local_timings_h2,
            )
            _ensure_collection_mark((sr2.get("lexical") or []), hop2_col)
            for _, lst in (sr2.get("dense") or {}).items():
                _ensure_collection_mark(lst or [], hop2_col)

            # Hop2 RRF + JOIN 최종 rerank (mode="join")
            sources_h2: List[_RankSource] = []
            for vname, lst in (sr2.get("dense") or {}).items():
                sources_h2.append(_RankSource(name=f"{hop2_col}:{vname}", weight=float(w_dense_map.get(vname, 1.0)), points=lst or []))
            sources_h2.append(_RankSource(name=f"{hop2_col}:lex", weight=float(preset.w_lex), points=sr2.get("lexical") or []))
            h2_rrf = _rrf_merge(sources_h2, rrf_k=int(os.getenv("RAG_RRF_K", "60")), keep=800)
            h2_rrf = _dedup_by_doc_id(h2_rrf)

            hop2_reranked = _final_rerank(
                h2_rrf,
                it=it,
                kws=kws,
                lex_w=lex_w_eff,
                base_route=("perf" if hop2_kind == "perf" else base_route),
                mode="join",
                keep=int(os.getenv("RAG_HOP2_FINAL_KEEP", "80")),
                tag_boost=float(getattr(preset, "tag_boost", 0.0)),
                tag_mismatch_penalty=float(getattr(preset, "tag_mismatch_penalty", 0.0)),
            )
            hop2_reranked = _dedup_by_doc_id(hop2_reranked)
            hop2_top = hop2_reranked[: max(1, hop2_keep)]

            log_top_points("RAG.JOIN.HOP2.TOP", hop2_top, topn=int(os.getenv("RAG_LOG_TOPN_HOP2", "8")))
            log_kv(
                "RAG.JOIN.HOP2.TIMINGS",
                **{k: float(v) for k, v in (local_timings_h2 or {}).items()}
            )

            # Hop2 context
            if action in ("list", "stats", "download"):
                hop2_ctx, hop2_refs = build_context_list_light(hop2_top, kind=hop2_kind, max_items=max(1, hop2_keep), query_text=hop2_q)
            else:
                hop2_ctx, hop2_refs = build_context_mixed(hop2_top, max_items=max(1, hop2_keep), query_text=hop2_q)

            context = (
                f"### [Hop1] 검색 결과 요약\n{hop1_ctx or '(후보 없음)'}\n\n"
                f"### [Hop2] {hop2_label}\n"
                f"- 필터 PJT_ID 후보: {', '.join(join_ids[:10])}\n\n"
                f"{hop2_ctx or '(후보 없음)'}"
            )

            refs = (hop1_refs or []) + (hop2_refs or [])
            timings["hop_total"] = time.time() - t_hop0
            timings["total"] = time.time() - t_all0
            hits = (hop1_top or []) + (hop2_top or [])
            return RagResult(stack=stack, keywords=kws, hits=hits, reranked_hits=hits, context=context, refs=refs, timings=timings)

    # -------------------------
    # Base (SEARCH / LOOKUP) federated
    # -------------------------
    t0 = time.time()

    # collection list
    target_cols = [COL_PROJECT, COL_PERF, COL_SUPPORT]

    # topK caps
    topk_dense = int(preset.top_k_dense)
    topk_lex_cand = min(int(preset.top_k_lex_cand), int(os.getenv("RAG_FED_LEX_CAND_CAP", "260")))
    topk_lex = int(preset.top_k_lex)

    def _build_emb_map_for_collection(col: str) -> Dict[str, Any]:
        vec_avail = _named_vectors_in_collection(qdr, col)
        use_vecs = [v for v in vector_names if (not isinstance(vec_avail, set) or v in vec_avail)]
        out: Dict[str, Any] = {}
        for vname in use_vecs:
            pe = pre_vecs.get(vname)
            out[vname] = pe if pe is not None else fallback_emb.get(vname)
        return {k: v for k, v in out.items() if v is not None}

    def _lex_params_for_collection(col: str) -> Tuple[List[str], Dict[str, float]]:
        # 기관 질의가 보이면 project만 org_norm에 강가중
        if col == COL_PROJECT and org_terms:
            lf = list(dict.fromkeys(list(lexical_fields_eff) + [KEY_ORG_NORM, "meta_flat"]))
            lw = dict(lex_w_eff)
            lw.setdefault("title", float(os.getenv("RAG_W_TITLE", "2.2")))
            lw.setdefault("answer_public", float(os.getenv("RAG_W_ANSWER_PUBLIC", "1.2")))
            lw[KEY_ORG_NORM] = float(os.getenv("RAG_W_ORG_NORM", "3.0"))
            lw.setdefault("meta_flat", float(os.getenv("RAG_W_META_FLAT", str(lw.get("meta_flat", 0.35)))))
            return lf, lw
        return lexical_fields_eff, lex_w_eff

    # server-side filter policy (LOOKUP에서만 적극 적용)
    def _server_filter_for_col(col: str) -> Any:
        if plan.mode != "lookup":
            return None

        ids_map = getattr(it, "ids_map", {}) or {}
        pjt_ids = [str(x).strip() for x in (ids_map.get("pjt_id") or []) if str(x).strip()]
        if pjt_ids:
            # PJT_ID는 project/perf 모두 join 키로 쓰이니 tag 과제 제한은 하지 말고 PJT_ID만 먼저 강제
            return build_join_filter(pjt_ids, tag_filters=None)

        # (선택) perf_tag_filters가 있으면 perf 컬렉션에서만 tag_filter
        if col == COL_PERF and perf_tag_filter:
            return perf_tag_filter

        # (선택) org_filter는 project 컬렉션에서만
        if col == COL_PROJECT and org_filter:
            return org_filter

        # base_route가 명확하면 tag로 1차 후보 노이즈를 줄임 (lookup에서만)
        if col == COL_PROJECT:
            if base_route == "people":
                tag_filter_local = _build_tag_only_filter([TAG_PJT_MP])
                return _and_filter(tag_filter_local, people_filter) if people_filter else tag_filter_local
            if base_route == "org":
                return _build_tag_only_filter([TAG_PJT_ORG])
            if base_route == "project":
                # 프로젝트 목록/상세 조회면 INFO로 제한
                return _build_tag_only_filter([TAG_PJT_INFO])

        if col == COL_PERF and base_route == "perf" and perf_tag_filter:
            return perf_tag_filter
        return None


    # retrieve each collection
    sr_by_col: Dict[str, Dict[str, Any]] = {}
    per_col_stats: Dict[str, Dict[str, float]] = {}

    for col in target_cols:
        emb_map_col = _build_emb_map_for_collection(col)
        use_dense_k = topk_dense if emb_map_col else 0

        lf, lw = _lex_params_for_collection(col)
        qfilter = _server_filter_for_col(col)

        log_kv(
            "RAG.COL.RETRIEVE",
            col=col,
            mode=plan.mode,
            use_dense_k=use_dense_k,
            topk_lex_cand=topk_lex_cand,
            topk_lex=topk_lex,
            qfilter=str(qfilter) if qfilter is not None else None,
            lex_fields=lf,
            lex_w_preview={k: float(lw.get(k)) for k in list(lw.keys())[:8]},
            dense_vecs=list(emb_map_col.keys()),
        )

        local_timings: Dict[str, float] = {}
        sr = _call_dense_retrieve_hybrid_multi(
            qdr=qdr,
            emb_map=emb_map_col,
            qtext=q,
            kws=kws,
            collection=col,
            lexical_fields=lf,
            lexical_field_weights=lw,
            lexical_scoring_mode=lex_scoring_mode_eff,
            top_k_dense=use_dense_k,
            top_k_lex_cand=topk_lex_cand,
            top_k_lex=topk_lex,
            query_filter=qfilter,  # ✅ plan 기반 적용
            timings_out=local_timings,
        )

        _ensure_collection_mark((sr.get("lexical") or []), col)
        for _, lst in (sr.get("dense") or {}).items():
            _ensure_collection_mark(lst or [], col)

        sr_by_col[col] = sr

        # stats
        d_hit = sum(len(lst or []) for lst in (sr.get("dense") or {}).values())
        l_hit = len(sr.get("lexical") or [])
        best_dense = None
        for lst in (sr.get("dense") or {}).values():
            if lst:
                s0 = getattr(lst[0], "score", None)
                try:
                    best_dense = max(best_dense or -1.0, float(s0) if s0 is not None else -1.0)
                except Exception:
                    pass

        per_col_stats[col] = {
            "dense_hits": float(d_hit),
            "lex_hits": float(l_hit),
            "best_dense": float(best_dense) if best_dense is not None else -1.0,
            "total": float(local_timings.get("total", 0.0)),
        }

        log_kv(
            "RAG.COL.STATS",
            col=col,
            dense_hits=int(d_hit),
            lex_hits=int(l_hit),
            best_dense=float(best_dense) if best_dense is not None else -1.0,
            timings=local_timings,
        )

    timings["dense_search"] = time.time() - t0

    # federated RRF merge sources
    sources: List[_RankSource] = []
    for col, sr in sr_by_col.items():
        for vname, lst in (sr.get("dense") or {}).items():
            sources.append(_RankSource(name=f"{col}:{vname}", weight=float(w_dense_map.get(vname, 1.0)), points=lst or []))
        sources.append(_RankSource(name=f"{col}:lex", weight=float(preset.w_lex), points=sr.get("lexical") or []))

    log_section("RAG.PER_COL_STATS", per_col_stats)

    t0 = time.time()
    merged_rrf = _rrf_merge(
        sources,
        rrf_k=int(os.getenv("RAG_RRF_K", "60")),
        keep=int(os.getenv("RAG_MERGED_KEEP", "1200")),
    )
    merged_rrf = _dedup_by_doc_id(merged_rrf)
    timings["rrf_merge"] = time.time() - t0

    log_top_points("RAG.MERGED_RRF.TOP", merged_rrf, topn=int(os.getenv("RAG_LOG_TOPN_MERGED", "10")))

    # final rerank
    t0 = time.time()
    final_keep = int(os.getenv("RAG_RERANK_K", "80"))
    reranked = _final_rerank(
        merged_rrf,
        it=it,
        kws=kws,
        lex_w=lex_w_eff,
        base_route=base_route,
        mode=plan.mode,
        keep=final_keep,
        tag_boost=float(getattr(preset, "tag_boost", 0.0)),
        tag_mismatch_penalty=float(getattr(preset, "tag_mismatch_penalty", 0.0)),
    )
    reranked = _dedup_by_doc_id(reranked)
    timings["final_rerank"] = time.time() - t0

    log_top_points("RAG.FINAL_RERANK.TOP", reranked, topn=int(os.getenv("RAG_LOG_TOPN_FINAL", "10")))

    # fallback policy
    fallback_chat = False
    if not reranked:
        fallback_chat = True
        timings["fallback_reason"] = "no_reranked"

    timings["fallback_chat"] = 1.0 if fallback_chat else 0.0

    # ✅ 최종 컨텍스트에 들어갈 애들만 payload를 두껍게 채움
    if not fallback_chat:
        max_items = int(preset.max_ctx_items)
        _hydrate_points_payload(qdr, reranked[: max(1, max_items)], include_fields=[
            "doc_id","title","tag","meta","meta_flat","answer_public","org_name_norm","pjt_id","urls","systems"
        ])

    # build context
    t0 = time.time()
    if fallback_chat:
        context, refs = "", []
    else:
        max_items = int(preset.max_ctx_items)
        if action in ("list", "stats", "download") and base_route in ("project", "perf", "people", "org"):
            context, refs = build_context_list_light(reranked, kind=base_route, max_items=max_items, query_text=q)
        else:
            context, refs = build_context_mixed(reranked, max_items=max_items, query_text=q)

    timings["build_context"] = time.time() - t0
    timings["total"] = time.time() - t_all0

    log_kv(
        "RAG.CTX",
        ctx_len=len(context or ""),
        refs=len(refs or []),
        max_items=int(preset.max_ctx_items),
        fallback_chat=fallback_chat,
    )

    # merged raw hits (for trace)
    merged_hits: List[Any] = []
    seen: set[Tuple[str, str]] = set()
    for src in sources:
        for h in (src.points or []):
            k = _hit_key(h)
            if k in seen:
                continue
            seen.add(k)
            merged_hits.append(h)

    logger.info(
        f"[PERF][{stack}] mode={plan.mode} base={base_route} action={action} rel={relation} "
        f"kw_det={timings.get('kw_det',0):.4f}s, search={timings.get('dense_search',0):.4f}s, "
        f"rrf={timings.get('rrf_merge',0):.4f}s, rerank={timings.get('final_rerank',0):.4f}s, "
        f"ctx={timings.get('build_context',0):.4f}s, total={timings.get('total',0):.4f}s"
    )

    # timings에 per_col_stats도 넣고 싶으면(옵션)
    for col, st in per_col_stats.items():
        for k, v in st.items():
            timings[f"col_{col}_{k}"] = float(v)

    return RagResult(
        stack=stack,
        keywords=kws,
        hits=merged_hits,
        reranked_hits=reranked,
        context=context,
        refs=refs,
        timings=timings,
    )

# -------------------------
# Public entry
# -------------------------
def run_rag_once(query: str, model_name: str = DEFAULT_MODEL_NAME, hint: Any = None) -> RagResult:
    return _run_rag_with_vectors(
        query=query,
        model_name=model_name,
        hint=hint,
        stack="M",
        vector_names=["e5i_qa", "e5_qa"],
        w_dense_map={"e5i_qa": 1.0, "e5_qa": 0.8},
        lexical_fields=None,
        lexical_field_weights=None,
    )

def run_rag_ab_compare(query: str, model_name: str = DEFAULT_MODEL_NAME, hint: Any = None) -> Dict[str, RagResult]:
    res_m = run_rag_once(query=query, model_name=model_name, hint=hint)
    return {"M": res_m}
