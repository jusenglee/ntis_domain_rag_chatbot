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
)
from rag_parts.join import (
    extract_pjt_ids as _extract_pjt_ids,
    sanitize_query_by_terms as _sanitize_query_by_terms,
)
from rag_parts.filters import (
    extract_org_terms as _extract_org_terms,
    build_org_filter as _build_org_filter,
    build_tag_only_filter as _build_tag_only_filter,
    build_join_filter as build_join_filter,
    build_perf_filter as build_perf_filter,
)

# -------------------------
# Lightweight list/stats context
# -------------------------
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
            name = _pick_first(meta.get("참여연구자명"), meta.get("연구자명"), meta.get("성명"), meta.get("이름"), meta.get("NAME"), meta.get("name"))
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

def _payload_text_bundle(p: Any) -> Dict[str, str]:
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        pl = {}
    meta = _get_meta(pl)

    title = _to_text(pl.get("title") or meta.get("국문과제명") or meta.get("성과명") or meta.get("논문명"))
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

def _filter_score(p: Any, it: QueryIntent, base_route: str, *, strict_ids: bool) -> float:
    tb = _payload_text_bundle(p)
    hay = " | ".join([tb["title"], tb["meta_flat"], tb["meta_kv"], tb["answer_public"]]).lower()

    sc = 0.0

    # IDs
    flat_ids = _flatten_ids(it.ids)
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

    # perf tag filters (exact)
    if base_route == "perf" and it.perf_tag_filters:
        pl = getattr(p, "payload", None) or {}
        if not isinstance(pl, dict):
            pl = {}
        meta = _get_meta(pl)
        tag = str(pl.get("tag") or meta.get("doc_type") or meta.get("source_table") or "")
        for t in list(it.perf_tag_filters)[:8]:
            if str(t) == tag:
                sc += 90.0

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
) -> List[Any]:
    if not cands:
        return []

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
        tot = (w_rrf * rrf_sc) + (w_kw * kw_sc) + (w_f * f_sc) + fam

        if isinstance(pl, dict):
            pl["_final_rrf"] = rrf_sc
            pl["_final_kw"] = kw_sc
            pl["_final_f"] = f_sc
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
    return bool(it.ids) and any((v for v in (it.ids or {}).values()))

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

def _flatten_ids(ids: Dict[str, List[str]]) -> List[str]:
    out: List[str] = []
    for _, lst in (ids or {}).items():
        for x in (lst or []):
            s = str(x).strip()
            if s and s not in out:
                out.append(s)
    return out
# -------------------------
# 2-hop JOIN
# -------------------------
def _extract_quoted_terms(q: str) -> List[str]:
    if not q:
        return []
    # "..." 또는 '...' 내부
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
) -> RagResult:
    t_all0 = time.time()
    timings: Dict[str, float] = {}

    q = normalize_query(query)
    if not q:
        return RagResult(stack=stack, keywords=[], hits=[], reranked_hits=[], context="", refs=[], timings={"total": 0.0, "fallback_chat": 1.0, "fallback_reason": "empty_query"})

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
        s = {str(c) for c in (cats or [])}
        if s == {"PROJECT"}:
            return "project"
        if s == {"PERFORMANCE"}:
            return "perf"
        if s == {"RESEARCHER"}:
            return "people"
        if s == {"QNA"}:
            return "support"
        # 복수면 SEARCH로 넓게
        return "mixed"

    def _coerce_int(x: Any, default: int) -> int:
        try:
            return int(x)
        except Exception:
            return default

    # --- hint 적용 ---
    qa = hint
    qa_conf = float(_get_attr(qa, "confidence", 0.0) or 0.0)

    if qa and qa_conf >= float(os.getenv("RAG_HINT_MIN_CONF", "0.55")):
        # 1) 검색에 쓸 텍스트는 retrieval_query 우선
        hinted_q = normalize_query(_get_attr(qa, "retrieval_query", "") or "") or normalize_query(query)

        # 2) base_route는 category로 강제(여기서 “분석” 대부분 끝)
        hinted_base = _category_to_base_route(_get_attr(qa, "category", []) or [])

        # 3) limit은 topK/ctx를 직접 제한하는데 사용
        hinted_limit = _coerce_int(_get_attr(qa, "limit", 0), 0)

        # 4) keywords는 raw query가 아니라 retrieval_query에서 뽑는 게 더 안정적
        q_for_retrieval = hinted_q
    else:
        q_for_retrieval = normalize_query(query)
        hinted_base = None
        hinted_limit = 0

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

    # ✅ 여기서부터는 q_for_retrieval을 표준 q로 쓰자
    q = q_for_retrieval

    # keywords
    t0 = time.time()
    kws = extract_keywords(q)
    timings["kw_det"] = time.time() - t0

    # intent (hint는 query_intent에서 흡수)
    it = _classify_query(q, kws, domain_hint=(hinted_base if hinted_base not in (None, "mixed") else None), hint=hint)
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

    # org terms/filter (필요 시)
    org_terms = list(it.org_terms or []) or _extract_org_terms(q, kws) or []
    org_filter = _build_org_filter(org_terms) if org_terms else None

    # perf tag filter (필요 시)
    tag_filter = _build_tag_only_filter(list(it.perf_tag_filters)) if it.perf_tag_filters else None

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

    # -------------------------
    # JOIN mode (2-hop)
    # -------------------------
    if plan.mode == "join" and relation:
        t_hop0 = time.time()
        pjt_ids: List[str] = [str(x).strip() for x in (it.ids or {}).get("pjt_id", []) if str(x).strip()]


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
                hop1_q = _sanitize_query_by_terms(q, remove_terms=list(it.remove_terms_for_head or [])) if hasattr(it, "remove_terms_for_head") else hop1_q

            hop2_q = q

            join_ids: List[str] = []
            hop1_top: List[Any] = []

            # 1) Hop1 (SEARCH) : 명시 PJT_ID 있으면 skip
            if pjt_ids:
                join_ids = pjt_ids[:]
            else:
                hop1_filter = _build_tag_only_filter(hop1_tag_filters) if hop1_tag_filters else None

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
                )

                # head term 강제 포함(people/org head일 때만, 옵션)
                head_terms: List[str] = []
                if hop1_kind == "people":
                    head_terms = _extract_quoted_terms(q)
                    if not head_terms:
                        m = re.search(r"([가-힣]{2,4})\s*(?:이|가|은|는)?\s*(?:참여인력|참여연구|연구자|연구원)", q)
                        if m:
                            head_terms = [m.group(1)]
                elif hop1_kind == "org":
                    head_terms = (org_terms or [])[:2]

                if head_terms:
                    hop1_reranked = [p for p in hop1_reranked if _must_contain_terms(p, head_terms)]

                hop1_top = hop1_reranked[: max(1, hop1_keep)]
                join_ids = _extract_pjt_ids(hop1_top, max_ids=50)

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
            if relation == ("project", "perf"):
                hop2_filter = build_perf_filter(join_ids, q)
            else:
                hop2_filter = build_join_filter(join_ids, tag_filters=hop2_tag_filters)

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
            )
            hop2_reranked = _dedup_by_doc_id(hop2_reranked)
            hop2_top = hop2_reranked[: max(1, hop2_keep)]

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
    if col == COL_PERF and tag_filter:
        return tag_filter

    # (선택) org_filter는 project 컬렉션에서만
    if col == COL_PROJECT and org_filter:
        return org_filter

    return None


    # retrieve each collection
    sr_by_col: Dict[str, Dict[str, Any]] = {}
    per_col_stats: Dict[str, Dict[str, float]] = {}

    for col in target_cols:
        emb_map_col = _build_emb_map_for_collection(col)
        use_dense_k = topk_dense if emb_map_col else 0

        lf, lw = _lex_params_for_collection(col)
        qfilter = _server_filter_for_col(col)

        local_timings: Dict[str, float] = {}
        sr = _call_dense_retrieve_hybrid_multi(
            qdr=qdr,
            emb_map=emb_map_col,
            qtext=q,
            kws=kws,
            collection=col,
            lexical_fields=lf,
            lexical_field_weights=lw,
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

    timings["dense_search"] = time.time() - t0

    # federated RRF merge sources
    sources: List[_RankSource] = []
    for col, sr in sr_by_col.items():
        for vname, lst in (sr.get("dense") or {}).items():
            sources.append(_RankSource(name=f"{col}:{vname}", weight=float(w_dense_map.get(vname, 1.0)), points=lst or []))
        sources.append(_RankSource(name=f"{col}:lex", weight=float(preset.w_lex), points=sr.get("lexical") or []))

    t0 = time.time()
    merged_rrf = _rrf_merge(
        sources,
        rrf_k=int(os.getenv("RAG_RRF_K", "60")),
        keep=int(os.getenv("RAG_MERGED_KEEP", "1200")),
    )
    merged_rrf = _dedup_by_doc_id(merged_rrf)
    timings["rrf_merge"] = time.time() - t0

    # final rerank (mode = search / lookup)
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
    )
    reranked = _dedup_by_doc_id(reranked)
    timings["final_rerank"] = time.time() - t0

    # fallback policy
    fallback_chat = False
    if not reranked:
        fallback_chat = True
        timings["fallback_reason"] = "no_reranked"

    timings["fallback_chat"] = 1.0 if fallback_chat else 0.0

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

def run_rag_ab_compare(query: str, model_name: str = DEFAULT_MODEL_NAME) -> Dict[str, RagResult]:
    res_m = run_rag_once(query=query, model_name=model_name)
    return {"M": res_m}
