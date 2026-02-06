import os
import re
# -*- coding: utf-8 -*-

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .constants import (
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
    filter_spec: Optional[Dict[str, Any]] = None
    min_should: Optional[int] = None
    promote_one_must: bool = False


@dataclass(frozen=True)
class JoinFilterInput:
    join_ids: List[str] = field(default_factory=list)
    pjt_nos: List[str] = field(default_factory=list)
    join_key_mode: str = "instance"
    tag_filters: Optional[List[str]] = None
    people_terms: List[str] = field(default_factory=list)
    org_terms: List[str] = field(default_factory=list)
    relation: Optional[tuple[str, str]] = None
    filter_spec: Optional[Dict[str, Any]] = None


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


def compile_filter(filter_spec: Optional[Dict[str, Any]]) -> Optional[Any]:
    """구조화된 filter_spec을 Qdrant 모델로 직렬화한다."""
    if qmodels is None or not filter_spec:
        return None

    def _to_condition(spec: Any) -> Optional[Any]:
        if spec is None:
            return None
        if not isinstance(spec, dict):
            return None

        nested_spec = spec.get("nested")
        if isinstance(nested_spec, dict):
            nested_cls = getattr(qmodels, "NestedCondition", None)
            nested_filter_cls = getattr(qmodels, "NestedFilter", None)
            if nested_cls is None:
                return None
            nested_key = str(nested_spec.get("key") or "").strip()
            nested_filter = _to_filter(nested_spec.get("filter") or {})
            if not nested_key or nested_filter is None:
                return None
            return _make_nested_condition(nested_cls, nested_filter_cls, nested_key, nested_filter)

        key = spec.get("field") or spec.get("key")
        if key:
            key = str(key).strip()
            if not key:
                return None

            match_values = spec.get("match_any")
            if isinstance(match_values, list) and match_values:
                return qmodels.FieldCondition(key=key, match=make_match_any(match_values))

            match_value = spec.get("match")
            if match_value not in (None, ""):
                return qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=match_value))

            range_payload = spec.get("range")
            if isinstance(range_payload, dict):
                range_obj = _make_range_filter(range_payload.get("gte"), range_payload.get("lte"))
                if range_obj is None:
                    range_obj = _make_range_filter(range_payload.get("min"), range_payload.get("max"))
                if range_obj is not None:
                    return qmodels.FieldCondition(key=key, range=range_obj)
        return _to_filter(spec)

    def _to_filter(spec: Any) -> Optional[Any]:
        if not isinstance(spec, dict):
            return None

        must = [_to_condition(item) for item in (spec.get("must") or [])]
        must = [item for item in must if item is not None]
        should = [_to_condition(item) for item in (spec.get("should") or [])]
        should = [item for item in should if item is not None]
        must_not = [_to_condition(item) for item in (spec.get("must_not") or [])]
        must_not = [item for item in must_not if item is not None]

        if not (must or should or must_not):
            return None

        kwargs: Dict[str, Any] = {
            "must": must or None,
            "should": should or None,
            "must_not": must_not or None,
        }
        min_should_raw = spec.get("min_should")
        min_should = _normalize_min_should(min_should_raw)
        if min_should is not None and should:
            kwargs["min_should"] = min_should
        return qmodels.Filter(**kwargs)

    return _to_filter(filter_spec)

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
        ]
    elif role is None:
        keys = [
            "org_nm",
            "prtcp_org[].org_nm",
            "prtcp_mp[].blng_org_nm",
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
    return _build_filter(must=None, should=should, must_not=None, min_should=1)

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
    nested_filter = _build_filter(must=None, should=should, must_not=None, min_should=1)
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
    return _build_filter(must=None, should=nested_conditions, must_not=None, min_should=1)

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

_PERF_TYPE_ALIASES: List[Dict[str, Any]] = [
    {"tag": TAG_RI_PAPER, "category": "논문", "aliases": ["논문", "paper"]},
    {"tag": TAG_RI_IPR, "category": "특허", "aliases": ["특허", "patent", "지식재산"]},
    {"tag": TAG_RI_RSCH_RPT, "category": "연구보고서", "aliases": ["연구보고서", "보고서", "report", "rpt"]},
    {"tag": TAG_RI_FCLT_EQUIP, "category": "시설/장비", "aliases": ["시설", "장비", "equip", "equipment"]},
    {"tag": TAG_RI_TECH_INFO, "category": "기술요약", "aliases": ["기술요약", "기술정보", "tech", "technology"]},
    {"tag": TAG_RI_SW, "category": "소프트웨어", "aliases": ["소프트웨어", "software", "sw"]},
    {"tag": TAG_RI_NVR, "category": "신품종", "aliases": ["신품종", "nvr"]},
    {"tag": TAG_RI_COMPOUND, "category": "화합물", "aliases": ["화합물", "compound"]},
    {"tag": TAG_RI_ORGSM_INFO, "category": "생명정보", "aliases": ["생명정보"]},
    {"tag": TAG_RI_ORGSM_RES, "category": "생물자원", "aliases": ["생물자원", "resource"]},
]


def build_perf_type_filter(perf_types: List[str]) -> Optional[Any]:
    if qmodels is None or not perf_types:
        return None
    # Qdrant perf 컬렉션의 성과 유형은 payload.tag 기준으로 필터링한다.
    key_tag = (os.getenv("RAG_KEY_TAG", "tag").strip() or "tag")
    return qmodels.Filter(
        must=[qmodels.FieldCondition(key=key_tag, match=make_match_any(perf_types))]
    )

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


def _build_prtcp_mp_people_nested_filter(
    *,
    people_terms: Optional[List[str]] = None,
    person_ids: Optional[List[str]] = None,
    gender_terms: Optional[List[str]] = None,
    org_terms: Optional[List[str]] = None,
    min_should: Optional[Any] = None,
    promote_one_must: bool = False,
) -> Optional[Any]:
    if qmodels is None:
        return None

    nested_cls = getattr(qmodels, "NestedCondition", None)
    nested_filter_cls = getattr(qmodels, "NestedFilter", None)
    if nested_cls is None:
        return None

    people_terms = [str(x).strip() for x in (people_terms or []) if str(x).strip()]
    person_ids = [str(x).strip() for x in (person_ids or []) if str(x).strip()]
    gender_terms = [str(x).strip() for x in (gender_terms or []) if str(x).strip()]
    org_terms = [str(x).strip() for x in (org_terms or []) if str(x).strip()]

    force_one_must = bool(promote_one_must and not person_ids and len(people_terms) == 1)

    nested_must: List[Any] = []
    nested_should: List[Any] = []

    if people_terms:
        name_cond = qmodels.FieldCondition(key="hm_nm", match=make_match_any(people_terms))
        if force_one_must:
            nested_must.append(name_cond)
        else:
            nested_should.append(name_cond)

    if person_ids:
        nested_must.append(qmodels.FieldCondition(key="hm_id", match=make_match_any(person_ids)))

    if gender_terms:
        nested_should.append(qmodels.FieldCondition(key="gender_slct_nm", match=make_match_any(gender_terms)))

    if org_terms:
        nested_should.append(qmodels.FieldCondition(key="blng_org_nm", match=make_match_any(org_terms)))

    if not nested_must and not nested_should:
        return None

    min_should_value = min_should if nested_should else None
    if min_should_value is None and nested_should:
        min_should_value = 1

    nested_filter = _build_filter(
        must=nested_must,
        should=nested_should,
        must_not=None,
        min_should=min_should_value,
    )
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

    if spec.filter_spec is not None:
        return compile_filter(spec.filter_spec)

    nested_people = _build_prtcp_mp_people_nested_filter(
        people_terms=list(spec.people_terms or []),
        person_ids=list(spec.person_ids or []),
        gender_terms=list(spec.gender_terms or []),
        org_terms=list(spec.org_terms or []),
        min_should=spec.min_should,
        promote_one_must=bool(spec.promote_one_must),
    )
    if nested_people is not None:
        return _build_filter(must=[nested_people], should=None, must_not=None)

    return None


def _normalize_min_should(min_should: Any) -> Optional[Any]:
    if qmodels is None:
        return None
    if min_should in (None, ""):
        return None

    # dict로 들어온 경우도 숫자만 추출
    if isinstance(min_should, dict):
        for key in ("min_count", "count", "value"):
            if key in min_should:
                return _normalize_min_should(min_should.get(key))
        return None

    try:
        value = int(min_should)
    except Exception:
        return None
    if value <= 0:
        return None

    # 1) MinShould 시도
    min_should_cls = getattr(qmodels, "MinShould", None)
    candidates: List[Any] = []
    if min_should_cls is not None:
        # 보통 min_count, 일부 count
        for kwargs in ({"min_count": value}, {"count": value}):
            try:
                candidates.append(min_should_cls(**kwargs))
            except Exception:
                pass

    # 2) dict 시도 (신버전에서 흔히 먹힘)
    candidates.append({"min_count": value})
    candidates.append({"count": value})

    # 3) int (구버전 호환용)
    candidates.append(value)

    # ✅ 중요한 부분: "실제로 Filter가 받아들이는 타입"을 테스트로 확정
    dummy_should = [qmodels.FieldCondition(key="__dummy__", match=qmodels.MatchValue(value="__dummy__"))]
    for cand in candidates:
        try:
            _ = qmodels.Filter(should=dummy_should, min_should=cand)
            return cand
        except Exception:
            continue

    # client에서 min_should 시그니처를 거부해도 _build_filter()의 TypeError fallback으로
    # OR gate를 보존할 수 있도록 원시 값은 반환한다.
    return value


def _build_filter(
    *,
    must: Optional[List[Any]],
    should: Optional[List[Any]],
    must_not: Optional[List[Any]],
    min_should: Optional[Any] = None,
) -> Any:
    kwargs: Dict[str, Any] = {
        "must": must or None,
        "should": should or None,
        "must_not": must_not or None,
    }
    normalized_min_should = _normalize_min_should(min_should)
    if normalized_min_should is not None and kwargs["should"]:
        kwargs["min_should"] = normalized_min_should
    try:
        return qmodels.Filter(**kwargs)
    except TypeError:
        # min_should 미지원 client 호환:
        # should를 제거하지 않고 must=[Filter(should=...)] 게이트 형태로 재구성 시도
        if kwargs.get("min_should") is not None and kwargs.get("should"):
            should_only_kwargs: Dict[str, Any] = {
                "must": None,
                "should": kwargs.get("should"),
                "must_not": None,
            }
            try:
                should_gate = qmodels.Filter(**should_only_kwargs)
                gate_must = list(kwargs.get("must") or []) + [should_gate]
                return qmodels.Filter(must=gate_must or None, should=None, must_not=kwargs.get("must_not"))
            except Exception:
                pass
        kwargs.pop("min_should", None)
        return qmodels.Filter(**kwargs)


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

def _build_title_filter_with_keys(terms: List[str], keys: List[str]) -> Optional[Any]:
    if qmodels is None:
        return None
    norm_terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
    if not norm_terms:
        return None
    should: List["qmodels.Condition"] = []
    for key in keys:
        if not key:
            continue
        should.append(qmodels.FieldCondition(key=key, match=make_match_any(norm_terms)))
    if not should:
        return None
    return qmodels.Filter(should=should)


def build_title_filter(terms: List[str]) -> Optional[Any]:
    """문서 제목(과제/성과 공통) 기반 서버단 필터를 구성합니다."""
    keys = [
        "title_text"
    ]
    return _build_title_filter_with_keys(terms, keys)

def build_join_filter(spec: JoinFilterInput) -> "qmodels.Filter":
    """JOIN Hop2용 필터를 명시 스펙 기반으로 직렬화한다."""
    if qmodels is None:
        raise RuntimeError("qdrant_client is required for build_join_filter()")

    if spec.filter_spec is not None:
        compiled = compile_filter(spec.filter_spec)
        return compiled or qmodels.Filter(must=[])

    join_ids = list(dict.fromkeys(str(x).strip() for x in (spec.join_ids or []) if str(x).strip()))
    pjt_nos = list(dict.fromkeys(str(x).strip() for x in (spec.pjt_nos or []) if str(x).strip()))

    if not join_ids and not pjt_nos:
        return qmodels.Filter(must=[])

    mode = str(spec.join_key_mode or "instance").strip().lower()
    if mode not in ("instance", "group"):
        mode = "instance"

    primary_id = os.getenv("RAG_KEY_PJT_ID", "pjt_id")
    primary_no = os.getenv("RAG_KEY_PJT_NO", "pjt_no")

    must: List["qmodels.Condition"] = []
    if mode == "group":
        if pjt_nos:
            must.append(
                qmodels.FieldCondition(
                    key=primary_no,
                    match=make_match_any(pjt_nos),
                )
            )
        elif join_ids:
            must.append(
                qmodels.FieldCondition(
                    key=primary_id,
                    match=make_match_any(join_ids),
                )
            )
    else:
        if join_ids:
            must.append(
                qmodels.FieldCondition(
                    key=primary_id,
                    match=make_match_any(join_ids),
                )
            )
        elif pjt_nos:
            must.append(
                qmodels.FieldCondition(
                    key=primary_no,
                    match=make_match_any(pjt_nos),
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
