import os
# -*- coding: utf-8 -*-

from typing import Any, Dict, List, Optional

from .constants import (
    ORG_RE, KEY_ORG_NORM,
    PERF_TAGS,
    TAG_RI_PAPER, TAG_RI_IPR, TAG_RI_RSCH_RPT, TAG_RI_FCLT_EQUIP, TAG_RI_TECH_INFO,
    TAG_RI_SW, TAG_RI_NVR, TAG_RI_COMPOUND, TAG_RI_ORGSM_INFO, TAG_RI_ORGSM_RES,
)

# qdrant filter models (optional import)
try:
    from qdrant_client.http import models as qmodels
except Exception:  # pragma: no cover
    qmodels = None

def make_match_any(values: List[str]):
    try:
        return qmodels.MatchAny(any=values)
    except Exception:
        try:
            return qmodels.MatchAny(any_values=values)
        except Exception:
            if values:
                return qmodels.MatchValue(value=values[0])
            return qmodels.MatchValue(value="")

def extract_org_terms(q: str, kws: List[str], *, max_terms: int = 3) -> List[str]:
    q = (q or "").strip()
    if not q:
        return []

    cands: List[str] = []
    for m in ORG_RE.finditer(q):
        s = (m.group(1) or "").strip()
        if s and s not in cands:
            cands.append(s)
        if len(cands) >= max_terms:
            return cands

    for kw in (kws or []):
        t = (kw or "").strip()
        if not t:
            continue
        if ("대학교" in t) or ("대학" in t) or ("연구원" in t) or ("연구소" in t):
            if t not in cands:
                cands.append(t)
            if len(cands) >= max_terms:
                break

    return cands[:max_terms]

def build_org_filter(org_terms: List[str]) -> Optional[Any]:
    if qmodels is None or not org_terms:
        return None
    return qmodels.Filter(
        must=[qmodels.FieldCondition(key=KEY_ORG_NORM, match=make_match_any(org_terms))]
    )

def build_tag_only_filter(tags: List[str]) -> Optional[Any]:
    if qmodels is None:
        return None
    if not tags:
        return None
    key_tag = (os.getenv("RAG_KEY_TAG", "tag").strip() or "tag")
    return qmodels.Filter(must=[qmodels.FieldCondition(key=key_tag, match=make_match_any(tags))])

def and_filter(a: Any, b: Any) -> Any:
    if qmodels is None:
        return a or b
    if a is None:
        return b
    if b is None:
        return a

    must = []
    if getattr(a, "must", None):
        must.extend(a.must)
    if getattr(b, "must", None):
        must.extend(b.must)

    should = []
    if getattr(a, "should", None):
        should.extend(a.should)
    if getattr(b, "should", None):
        should.extend(b.should)

    must_not = []
    if getattr(a, "must_not", None):
        must_not.extend(a.must_not)
    if getattr(b, "must_not", None):
        must_not.extend(b.must_not)

    return qmodels.Filter(
        must=must or None,
        should=should or None,
        must_not=must_not or None,
    )

def pick_perf_tag_filters(q: str) -> List[str]:
    t = (q or "").lower()
    tags: List[str] = []

    def _add(tag: str):
        if tag and (tag in PERF_TAGS) and (tag not in tags):
            tags.append(tag)

    if "논문" in t or "paper" in t:
        _add(TAG_RI_PAPER)
    if "특허" in t or "patent" in t:
        _add(TAG_RI_IPR)
    if ("보고서" in t) or ("연구보고서" in t) or ("report" in t) or ("rpt" in t):
        _add(TAG_RI_RSCH_RPT)
    if ("장비" in t) or ("시설" in t) or ("equip" in t) or ("equipment" in t):
        _add(TAG_RI_FCLT_EQUIP)
    if ("기술요약" in t) or ("기술" in t and "요약" in t) or ("tech" in t):
        _add(TAG_RI_TECH_INFO)
    if ("소프트웨어" in t) or ("software" in t) or ("sw" in t):
        _add(TAG_RI_SW)
    if ("신품종" in t) or ("nvr" in t):
        _add(TAG_RI_NVR)
    if ("화합물" in t) or ("compound" in t):
        _add(TAG_RI_COMPOUND)
    if ("생명정보" in t):
        _add(TAG_RI_ORGSM_INFO)
    if ("생물자원" in t) or ("resource" in t):
        _add(TAG_RI_ORGSM_RES)

    return tags

def build_join_filter(join_ids: List[str], *, tag_filters: Optional[List[str]] = None) -> Optional[Any]:
    """Build a hard filter for joining by project id.

    IMPORTANT:
    - In Qdrant, `should` conditions may behave as a soft preference when `must` exists.
      That can cause cross-project leakage (tag matches, but PJT_ID doesn't).
    - Therefore we enforce PJT_ID as a MUST on the canonical key.
    """
    if qmodels is None:
        return None
    if not join_ids:
        return None

    key_pjt_id = (os.getenv("RAG_KEY_PJT_ID", "meta.PJT_ID").strip() or "meta.PJT_ID")
    key_tag = (os.getenv("RAG_KEY_TAG", "tag").strip() or "tag")

    must_conds: List[Any] = [
        qmodels.FieldCondition(key=key_pjt_id, match=make_match_any(join_ids))
    ]

    if tag_filters:
        must_conds.append(qmodels.FieldCondition(key=key_tag, match=make_match_any(tag_filters)))

    return qmodels.Filter(must=must_conds or None)

def build_perf_filter(pjt_ids: List[str], query: str) -> Optional[Any]:
    """Perf-side filter: constrain by PJT_ID + optional performance tags."""
    if qmodels is None:
        return None
    if not pjt_ids:
        return None

    key_pjt_id = (os.getenv("RAG_KEY_PJT_ID", "meta.PJT_ID").strip() or "meta.PJT_ID")
    key_tag = (os.getenv("RAG_KEY_TAG", "tag").strip() or "tag")

    must_conds: List[Any] = [
        qmodels.FieldCondition(key=key_pjt_id, match=make_match_any(pjt_ids))
    ]

    tag_filters = pick_perf_tag_filters(query)
    if tag_filters:
        must_conds.append(qmodels.FieldCondition(key=key_tag, match=make_match_any(tag_filters)))

    return qmodels.Filter(must=must_conds or None)

