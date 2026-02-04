import os
import re
# -*- coding: utf-8 -*-

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .constants import (
    KEY_ORG_NORM,
    PERF_TAGS,
    TAG_RI_PAPER, TAG_RI_IPR, TAG_RI_RSCH_RPT, TAG_RI_FCLT_EQUIP, TAG_RI_TECH_INFO,
    TAG_RI_SW, TAG_RI_NVR, TAG_RI_COMPOUND, TAG_RI_ORGSM_INFO, TAG_RI_ORGSM_RES,
)


@dataclass(frozen=True)
class OrgFilterInput:
    terms: List[str] = field(default_factory=list)
    role: Optional[str] = None


@dataclass(frozen=True)
class PeopleFilterInput:
    people_terms: List[str] = field(default_factory=list)
    person_ids: List[str] = field(default_factory=list)
    gender_terms: List[str] = field(default_factory=list)
    org_terms: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class JoinFilterInput:
    join_ids: List[str] = field(default_factory=list)
    tag_filters: Optional[List[str]] = None
    people_terms: List[str] = field(default_factory=list)
    org_terms: List[str] = field(default_factory=list)
    relation: Optional[tuple[str, str]] = None


@dataclass(frozen=True)
class PerfFilterInput:
    query: str = ""
    join_ids: List[str] = field(default_factory=list)

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
    from .query_intent import extract_org_terms as extract_org_terms_llm

    return extract_org_terms_llm(q, kws, max_terms=max_terms)

def build_org_filter(spec: OrgFilterInput) -> Optional[Any]:
    if qmodels is None or not spec.terms:
        return None
    role = (spec.role or "").strip().lower() or None
    if role == "participant":
        return build_prtcp_org_nested_filter(spec)

    keys = [
        "org_nm",
    ]
    if role in ("performer", "lead", "performing"):
        keys = [
            "org_nm",
            "meta_basic.pjt_prfrm_org_nm",
        ]
    elif role is None:
        keys = [
            KEY_ORG_NORM,
            "org_nm",
            "prtcp_org[].org_nm",
            "prtcp_mp[].blng_org_nm",
            "meta_basic.pjt_prfrm_org_nm",
        ]
    should: List["qmodels.Condition"] = []
    for key in keys:
        if not key:
            continue
        should.append(qmodels.FieldCondition(key=key, match=make_match_any(spec.terms)))
    if role is None:
        nested_mp_org = _build_prtcp_mp_org_nested_filter(spec.terms)
        if nested_mp_org is not None:
            should.append(nested_mp_org)
    if not should:
        return None
    return qmodels.Filter(should=should)

def build_prtcp_org_nested_filter(spec: OrgFilterInput) -> Optional[Any]:
    if qmodels is None or not spec.terms:
        return None
    nested_cls = getattr(qmodels, "NestedCondition", None)
    nested_filter_cls = getattr(qmodels, "NestedFilter", None)
    if nested_cls is None:
        return None

    nested_keys = [
        "org_nm",
    ]
    should: List["qmodels.Condition"] = []
    for key in nested_keys:
        if not key:
            continue
        should.append(qmodels.FieldCondition(key=key, match=make_match_any(spec.terms)))
    if not should:
        return None
    nested_filter = qmodels.Filter(should=should)
    nested_conditions: List[Any] = []
    nested_org = _make_nested_condition(nested_cls, nested_filter_cls, "prtcp_org", nested_filter)
    if nested_org is not None:
        nested_conditions.append(nested_org)

    nested_mp_org = _build_prtcp_mp_org_nested_filter(spec.terms)
    if nested_mp_org is not None:
        nested_conditions.append(nested_mp_org)

    if not nested_conditions:
        return None
    if len(nested_conditions) == 1:
        return nested_conditions[0]
    return qmodels.Filter(should=nested_conditions)

def build_tag_only_filter(tags: List[str]) -> Optional[Any]:
    if qmodels is None:
        return None
    if not tags:
        return None
    key_tag = (os.getenv("RAG_KEY_TAG", "tag").strip() or "tag")
    return qmodels.Filter(must=[qmodels.FieldCondition(key=key_tag, match=make_match_any(tags))])

def _coerce_year_value(value: Any) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    match = re.search(r"(19\d{2}|20\d{2})", s)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None

def _make_range_filter(gte: Any, lte: Any):
    if qmodels is None:
        return None
    range_cls = getattr(qmodels, "Range", None)
    if range_cls is None:
        return None
    kwargs_list = [
        {"gte": gte, "lte": lte},
        {"min": gte, "max": lte},
        {"from": gte, "to": lte},
    ]
    for kwargs in kwargs_list:
        payload = {k: v for k, v in kwargs.items() if v is not None}
        if not payload:
            continue
        try:
            return range_cls(**payload)
        except Exception:
            continue
    return None

def build_year_range_filter(
    year_from: Any,
    year_to: Any,
    *,
    year_keys: Optional[List[str]] = None,
    date_keys: Optional[List[str]] = None,
) -> Optional[Any]:
    if qmodels is None:
        return None

    y_from = _coerce_year_value(year_from)
    y_to = _coerce_year_value(year_to)
    if y_from is None and y_to is None:
        return None
    if y_from is not None and y_to is not None and y_from > y_to:
        y_from, y_to = y_to, y_from

    year_keys = year_keys or ["stan_yr", "meta_basic.stan_yr"]
    date_keys = date_keys or [
        "dt1",
        "dt2",
        "meta_basic.tot_rsch_start_dt",
        "meta_basic.tot_rsch_end_dt",
    ]

    should: List["qmodels.Condition"] = []
    year_range = _make_range_filter(y_from, y_to)
    if year_range is not None:
        for key in year_keys:
            should.append(qmodels.FieldCondition(key=key, range=year_range))

    if y_from is not None or y_to is not None:
        date_from = f"{y_from}-01-01" if y_from is not None else None
        date_to = f"{y_to}-12-31" if y_to is not None else None
        date_range = _make_range_filter(date_from, date_to)
        if date_range is not None:
            for key in date_keys:
                should.append(qmodels.FieldCondition(key=key, range=date_range))

    if not should and y_from is not None:
        y_values: List[str] = []
        if y_to is None:
            y_values = [str(y_from)]
        else:
            span = max(0, min(60, y_to - y_from))
            y_values = [str(y_from + i) for i in range(span + 1)]
        if y_values:
            for key in year_keys:
                should.append(qmodels.FieldCondition(key=key, match=make_match_any(y_values)))

    return qmodels.Filter(should=should) if should else None

def build_perf_type_filter(perf_types: List[str]) -> Optional[Any]:
    if qmodels is None or not perf_types:
        return None
    # Qdrant perf 컬렉션의 성과 유형은 payload.tag 기준으로 필터링한다.
    key_tag = (os.getenv("RAG_KEY_TAG", "tag").strip() or "tag")
    return qmodels.Filter(
        must=[qmodels.FieldCondition(key=key_tag, match=make_match_any(perf_types))]
    )

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


def _build_prtcp_mp_org_nested_filter(terms: List[str]) -> Optional[Any]:
    if qmodels is None or not terms:
        return None
    nested_cls = getattr(qmodels, "NestedCondition", None)
    nested_filter_cls = getattr(qmodels, "NestedFilter", None)
    if nested_cls is None:
        return None
    org_cond = qmodels.FieldCondition(key="blng_org_nm", match=make_match_any(terms))
    nested_filter = qmodels.Filter(must=[org_cond])
    return _make_nested_condition(nested_cls, nested_filter_cls, "prtcp_mp", nested_filter)


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


def build_people_filter(spec: PeopleFilterInput) -> Optional[Any]:
    if qmodels is None:
        return None

    terms = list(spec.people_terms)
    ids = list(spec.person_ids)
    genders = list(spec.gender_terms)
    orgs = list(spec.org_terms)

    should: List["qmodels.Condition"] = []

    if terms:
        name_keys = [
            "prtcp_mp[].hm_nm",
        ]
        for key in name_keys:
            should.append(qmodels.FieldCondition(key=key, match=make_match_any(terms)))

    if ids:
        id_keys = [
            "prtcp_mp[].hm_id",
        ]
        for key in id_keys:
            should.append(qmodels.FieldCondition(key=key, match=make_match_any(ids)))

    if orgs:
        org_keys = [
            "prtcp_mp[].blng_org_nm",
        ]
        for key in org_keys:
            should.append(qmodels.FieldCondition(key=key, match=make_match_any(orgs)))
        nested_mp_org = _build_prtcp_mp_org_nested_filter(orgs)
        if nested_mp_org is not None:
            should.append(nested_mp_org)

    nested_filter = _build_prtcp_mp_nested_filter(
        people_terms=terms,
        person_ids=ids,
        gender_terms=genders,
        org_terms=orgs,
    )
    if genders and nested_filter is not None:
        return qmodels.Filter(must=[nested_filter])

    if genders:
        for key in ["gender_slct", "gender_slct_nm"]:
            should.append(qmodels.FieldCondition(key=key, match=make_match_any(genders)))

    if nested_filter is not None:
        should.append(nested_filter)

    if not should:
        return None

    filter_fields = None
    for attr in ("model_fields", "__fields__"):
        fields = getattr(qmodels.Filter, attr, None)
        if isinstance(fields, dict):
            filter_fields = fields
            break

    if filter_fields and "min_should" in filter_fields:
        min_should_cls = getattr(qmodels, "MinShould", None)
        if min_should_cls is not None:
            try:
                min_should_value = min_should_cls(conditions=should, min_count=1)
            except Exception:
                try:
                    min_should_value = min_should_cls(min_should=1)
                except Exception:
                    min_should_value = min_should_cls(value=1)
        else:
            min_should_value = {"min_should": 1}
        return qmodels.Filter(should=should, min_should=min_should_value)

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


def build_project_id_filter(pjt_ids: List[str], pjt_nos: List[str]) -> Optional[Any]:
    """PJT_ID/PJT_NO 기반 서버단 필터를 구성합니다."""
    if qmodels is None:
        return None

    pjt_id_values: List[str] = []
    for val in (pjt_ids or []):
        sval = str(val).strip()
        if sval and sval not in pjt_id_values:
            pjt_id_values.append(sval)
    pjt_no_values: List[str] = []
    for val in (pjt_nos or []):
        sval = str(val).strip()
        if sval and sval not in pjt_no_values:
            pjt_no_values.append(sval)

    if not pjt_id_values and not pjt_no_values:
        return None

    primary_id = os.getenv("RAG_KEY_PJT_ID", "pjt_id")
    primary_no = os.getenv("RAG_KEY_PJT_NO", "pjt_no")
    id_key_cands: List[str] = []
    for k in [primary_id, "meta_basic.pjt_id", "pjt_id"]:
        if k and k not in id_key_cands:
            id_key_cands.append(k)

    no_key_cands: List[str] = []
    for k in [primary_no, "meta_basic.pjt_no", "pjt_no"]:
        if k and k not in no_key_cands:
            no_key_cands.append(k)

    should: List["qmodels.Condition"] = []
    if pjt_id_values:
        should.extend(
            qmodels.FieldCondition(key=k, match=make_match_any(pjt_id_values))
            for k in id_key_cands
        )
    if pjt_no_values:
        should.extend(
            qmodels.FieldCondition(key=k, match=make_match_any(pjt_no_values))
            for k in no_key_cands
        )

    return qmodels.Filter(should=should)

def build_project_title_filter(terms: List[str]) -> Optional[Any]:
    """과제명(국문/영문) 기반 서버단 필터를 구성합니다."""
    if qmodels is None:
        return None
    norm_terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
    if not norm_terms:
        return None

    keys = [
        "kor_pjt_nm",
        "eng_pjt_nm",
        "title1",
        "title2",
        "title_text",
        "meta_basic.kor_pjt_nm",
        "meta_basic.eng_pjt_nm",
        "meta_basic.title1",
        "meta_basic.title2",
        "meta_basic.title_text",
        "meta_basic.pjt_nm",
        "pjt_nm",
    ]
    should: List["qmodels.Condition"] = []
    for key in keys:
        if not key:
            continue
        should.append(qmodels.FieldCondition(key=key, match=make_match_any(norm_terms)))
    if not should:
        return None
    return qmodels.Filter(should=should)

def build_join_filter(spec: JoinFilterInput) -> "qmodels.Filter":
    """JOIN Hop2용 필터: PJT_ID 기반으로 후보군을 강제 제한합니다.

    누락 방지를 위해 PJT_ID 키 변형(meta_basic/payload)을 OR로 묶습니다.
    """
    if qmodels is None:
        raise RuntimeError("qdrant_client is required for build_join_filter()")

    join_ids = list(spec.join_ids)
    if not join_ids:
        return qmodels.Filter(must=[])

    people_terms = list(spec.people_terms or [])
    org_terms = list(spec.org_terms or [])
    relation = spec.relation

    primary_id = os.getenv("RAG_KEY_PJT_ID", "pjt_id")
    primary_no = os.getenv("RAG_KEY_PJT_NO", "pjt_no")
    id_key_cands = []
    for k in [primary_id, "meta_basic.pjt_id", "pjt_id"]:
        if k and k not in id_key_cands:
            id_key_cands.append(k)
    no_key_cands = []
    for k in [primary_no, "meta_basic.pjt_no", "pjt_no"]:
        if k and k not in no_key_cands:
            no_key_cands.append(k)

    join_should: List["qmodels.Condition"] = []
    if id_key_cands:
        join_should.extend(
            qmodels.FieldCondition(
                key=k,
                match=qmodels.MatchAny(any=join_ids),
            )
            for k in id_key_cands
        )
    if no_key_cands:
        join_should.extend(
            qmodels.FieldCondition(
                key=k,
                match=qmodels.MatchAny(any=join_ids),
            )
            for k in no_key_cands
        )

    join_any = qmodels.Filter(should=join_should)

    must: List["qmodels.Condition"] = [join_any]

    apply_people_org = True
    if relation:
        apply_people_org = any(part in ("people", "org") for part in relation)

    if apply_people_org:
        nested_people_org = _build_prtcp_mp_nested_filter(
            people_terms=people_terms,
            person_ids=[],
            gender_terms=[],
            org_terms=org_terms,
        )
        if nested_people_org is not None:
            must.append(nested_people_org)

    if spec.tag_filters:
        must.append(
            qmodels.FieldCondition(
                key="tag",
                match=qmodels.MatchAny(any=spec.tag_filters),
            )
        )

    return qmodels.Filter(must=must)

def build_perf_filter(spec: PerfFilterInput) -> "qmodels.Filter":
    """성과(perf) 컬렉션에서 LOOKUP/JOIN 시 사용할 서버단 필터입니다.

    - join_ids가 있으면 PJT_ID 기반으로 후보군을 강제 제한 (키 변형 OR)
    - query에서 감지한 성과 하위 유형(논문/특허/보고서/소프트웨어 등)이 있으면 tag로 추가 제한
    """
    if qmodels is None:
        raise RuntimeError("qdrant_client is required for build_perf_filter()")

    must: List["qmodels.Condition"] = []
    must_not: List["qmodels.Condition"] = []

    join_ids = list(spec.join_ids)
    if join_ids:
        primary = os.getenv("RAG_KEY_PJT_ID", "pjt_id")
        key_cands = []
        for k in [
            primary,
            "meta_basic.pjt_id",
            "meta_basic.pjt_no",
            "pjt_id",
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

    tag_filters = pick_perf_tag_filters(spec.query)
    if tag_filters:
        must.append(
            qmodels.FieldCondition(
                key="tag",
                match=qmodels.MatchAny(any=tag_filters),
            )
        )

    return qmodels.Filter(must=must, must_not=must_not)
