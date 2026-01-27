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
    keys = [
        KEY_ORG_NORM,
        "org_nm",
        "prfrm_org_nm",
        "org_name_raw",
        "blng_org_nm",
        "prtcp_org.org_nm",
        "meta.과제수행기관명",
        "meta.참여연구기관명",
        "meta.발행기관명",
        "meta.등록기관명",
        "meta.기탁기관명",
        "meta.소속기관명",
        "meta_basic.PJT_PRFRM_ORG_NM",
        "meta_basic.prfrm_org_nm",
        "meta_basic.org_name",
    ]
    should: List["qmodels.Condition"] = []
    for key in keys:
        if not key:
            continue
        should.append(qmodels.FieldCondition(key=key, match=make_match_any(org_terms)))
    if not should:
        return None
    return qmodels.Filter(should=should)

def build_tag_only_filter(tags: List[str]) -> Optional[Any]:
    if qmodels is None:
        return None
    if not tags:
        return None
    key_tag = (os.getenv("RAG_KEY_TAG", "tag").strip() or "tag")
    return qmodels.Filter(must=[qmodels.FieldCondition(key=key_tag, match=make_match_any(tags))])

def _build_prtcp_mp_nested_filter(
    *,
    people_terms: List[str],
    person_ids: List[str],
    gender_terms: List[str],
    org_terms: Optional[List[str]] = None,
) -> Optional[Any]:
    if qmodels is None:
        return None
    nested_cls = getattr(qmodels, "NestedCondition", None)
    nested_filter_cls = getattr(qmodels, "NestedFilter", None)
    if nested_cls is None:
        return None

    name_cond = (
        qmodels.FieldCondition(key="hm_nm", match=make_match_any(people_terms))
        if people_terms
        else None
    )
    id_cond = (
        qmodels.FieldCondition(key="hm_id", match=make_match_any(person_ids))
        if person_ids
        else None
    )
    org_cond = (
        qmodels.FieldCondition(key="blng_org_nm", match=make_match_any(org_terms))
        if org_terms
        else None
    )
    base_must = [c for c in (name_cond, id_cond, org_cond) if c is not None]
    if not base_must and not gender_terms:
        return None

    if gender_terms:
        nested_should: List["qmodels.Condition"] = []
        for key in ("gender_slct", "gender_slct_nm"):
            must = list(base_must)
            must.append(qmodels.FieldCondition(key=key, match=make_match_any(gender_terms)))
            nested_cond = _make_nested_condition(nested_cls, nested_filter_cls, "prtcp_mp", qmodels.Filter(must=must))
            if nested_cond is not None:
                nested_should.append(nested_cond)
        return qmodels.Filter(should=nested_should) if nested_should else None

    return _make_nested_condition(nested_cls, nested_filter_cls, "prtcp_mp", qmodels.Filter(must=base_must))


def _make_nested_condition(nested_cls, nested_filter_cls, key: str, flt):
    if nested_filter_cls is not None:
        try:
            return nested_cls(nested=nested_filter_cls(key=key, filter=flt))
        except Exception:
            return None
    try:
        return nested_cls(key=key, filter=flt)
    except Exception:
        return None


def build_people_filter(
    people_terms: List[str],
    person_ids: Optional[List[str]] = None,
    gender_terms: Optional[List[str]] = None,
    org_terms: Optional[List[str]] = None,
) -> Optional[Any]:
    if qmodels is None:
        return None

    terms = [str(t).strip() for t in (people_terms or []) if str(t).strip()]
    ids = [str(v).strip() for v in (person_ids or []) if str(v).strip()]
    genders = [str(g).strip() for g in (gender_terms or []) if str(g).strip()]
    orgs = [str(o).strip() for o in (org_terms or []) if str(o).strip()]

    should: List["qmodels.Condition"] = []

    if terms:
        name_keys = [
            "flat_text",
            "meta_flat",
            "meta.인물명",
            "meta.참여연구자명",
            "meta.연구자명",
            "meta.성명",
            "인물명",
            "hm_nm",
            "prtcp_mp.hm_nm",
        ]
        for key in name_keys:
            should.append(qmodels.FieldCondition(key=key, match=make_match_any(terms)))

    if ids:
        id_keys = [
            "flat_text",
            "meta_flat",
            "meta.국가연구자번호",
            "meta.과학기술인등록번호",
            "meta.인물ID",
            "meta.참여인력일련번호",
            "국가연구자번호",
            "과학기술인등록번호",
            "인물ID",
            "참여인력일련번호",
            "hm_id",
            "prtcp_mp.hm_id",
        ]
        for key in id_keys:
            should.append(qmodels.FieldCondition(key=key, match=make_match_any(ids)))

    if orgs:
        org_keys = [
            KEY_ORG_NORM,
            "org_nm",
            "blng_org_nm",
            "meta.소속기관명",
            "meta.소속",
            "meta.기관명",
            "prtcp_mp.blng_org_nm",
        ]
        for key in org_keys:
            should.append(qmodels.FieldCondition(key=key, match=make_match_any(orgs)))

    nested_filter = _build_prtcp_mp_nested_filter(
        people_terms=terms,
        person_ids=ids,
        gender_terms=genders,
        org_terms=orgs,
    )
    if genders and nested_filter is not None:
        return qmodels.Filter(must=[nested_filter])

    if genders:
        for key in ["flat_text", "meta_flat", "gender_slct", "gender_slct_nm"]:
            should.append(qmodels.FieldCondition(key=key, match=make_match_any(genders)))

    if nested_filter is not None:
        should.append(nested_filter)

    if not should:
        return None

    return qmodels.Filter(should=should)

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

def build_join_filter(join_ids: List[str], *, tag_filters: Optional[List[str]] = None) -> "qmodels.Filter":
    """JOIN Hop2용 필터: PJT_ID 기반으로 후보군을 강제 제한합니다.

    누락 방지를 위해 PJT_ID 키 변형(meta.PJT_ID / meta.pjt_id / PJT_ID / pjt_id 등)을 OR로 묶습니다.
    """
    if qmodels is None:
        raise RuntimeError("qdrant_client is required for build_join_filter()")

    join_ids = [str(x).strip() for x in (join_ids or []) if str(x).strip()]
    if not join_ids:
        return qmodels.Filter(must=[])

    primary = os.getenv("RAG_KEY_PJT_ID", "meta.PJT_ID")
    key_cands = []
    for k in [
        primary,
        "meta.PJT_ID",
        "meta.pjt_id",
        "meta_basic.PJT_ID",
        "meta_basic.pjt_id",
        "PJT_ID",
        "pjt_id",
        "meta.pjtId",
        "pjtId",
    ]:
        if k and k not in key_cands:
            key_cands.append(k)

    join_any = qmodels.Filter(
        should=[
            qmodels.FieldCondition(
                key=k,
                match=qmodels.MatchAny(any=join_ids),
            )
            for k in key_cands
        ]
    )

    must: List["qmodels.Condition"] = [join_any]

    if tag_filters:
        must.append(
            qmodels.FieldCondition(
                key="tag",
                match=qmodels.MatchAny(any=tag_filters),
            )
        )

    return qmodels.Filter(must=must)

def build_perf_filter(query: str, join_ids: Optional[List[str]] = None) -> "qmodels.Filter":
    """성과(perf) 컬렉션에서 LOOKUP/JOIN 시 사용할 서버단 필터입니다.

    - join_ids가 있으면 PJT_ID 기반으로 후보군을 강제 제한 (키 변형 OR)
    - query에서 감지한 성과 하위 유형(논문/특허/보고서/소프트웨어 등)이 있으면 tag로 추가 제한
    """
    if qmodels is None:
        raise RuntimeError("qdrant_client is required for build_perf_filter()")

    must: List["qmodels.Condition"] = []
    must_not: List["qmodels.Condition"] = []

    join_ids = [str(x).strip() for x in (join_ids or []) if str(x).strip()]
    if join_ids:
        primary = os.getenv("RAG_KEY_PJT_ID", "meta.PJT_ID")
        key_cands = []
        for k in [
            primary,
            "meta.PJT_ID",
            "meta.pjt_id",
            "meta_basic.PJT_ID",
            "meta_basic.pjt_id",
            "PJT_ID",
            "pjt_id",
            "meta.pjtId",
            "pjtId",
        ]:
            if k and k not in key_cands:
                key_cands.append(k)

        join_any = qmodels.Filter(
            should=[
                qmodels.FieldCondition(
                    key=k,
                    match=qmodels.MatchAny(any=join_ids),
                )
                for k in key_cands
            ]
        )
        must.append(join_any)

    tag_filters = pick_perf_tag_filters(query)
    if tag_filters:
        must.append(
            qmodels.FieldCondition(
                key="tag",
                match=qmodels.MatchAny(any=tag_filters),
            )
        )

    return qmodels.Filter(must=must, must_not=must_not)
