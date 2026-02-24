# -*- coding: utf-8 -*-
"""
rag_parts/filters.py

- qdrant_client 1.16.2 기준으로 NestedCondition/Nested를 사용한 nested 필터 지원
- org / people / join / perf / year range / title / tag 필터 빌더 제공
- filter_spec(JSON) -> Qdrant Filter 컴파일 지원 (nested 포함)
"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .query_intent import normalize_org_terms

from .constants import (
    COL_PERF,
    COL_PROJECT,
    PERF_TAGS,
    TAG_RI_PAPER,
    TAG_RI_IPR,
    TAG_RI_RSCH_RPT,
    TAG_RI_FCLT_EQUIP,
    TAG_RI_TECH_INFO,
    TAG_RI_SW,
    TAG_RI_NVR,
    TAG_RI_COMPOUND,
    TAG_RI_ORGSM_INFO,
    TAG_RI_ORGSM_RES,
)

# qdrant filter models (optional import)
try:
    from qdrant_client.http import models as qmodels
except Exception:  # pragma: no cover
    qmodels = None


# -----------------------------
# dataclasses (inputs)
# -----------------------------
@dataclass(frozen=True)
class OrgFilterInput:
    terms: List[str] = field(default_factory=list)
    role: Optional[str] = None  # performer/lead/participant/None


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
    join_key_mode: str = "instance"  # instance|group
    tag_filters: Optional[List[str]] = None
    people_terms: List[str] = field(default_factory=list)
    org_terms: List[str] = field(default_factory=list)
    relation: Optional[tuple[str, str]] = None
    filter_spec: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class PerfFilterInput:
    query: str = ""
    join_ids: List[str] = field(default_factory=list)


_PJT_ID_VALUE_RE = re.compile(r"^\d{8,12}$")


# -----------------------------
# qdrant helpers / feature detect
# -----------------------------
NestedCondition = getattr(qmodels, "NestedCondition", None) if qmodels else None
Nested = getattr(qmodels, "Nested", None) if qmodels else None

logger = logging.getLogger(__name__)
_PROJECT_KEY_POLICY_LOGGED = False

TITLE_MATCH_MODE_EXACT = "EXACT"
TITLE_MATCH_MODE_TEXT = "TEXT"
TITLE_MATCH_MODE_CONTAINS = "CONTAINS"
TITLE_MATCH_MODES = {TITLE_MATCH_MODE_EXACT, TITLE_MATCH_MODE_TEXT, TITLE_MATCH_MODE_CONTAINS}



def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "f", "no", "n", "off", ""}:
        return False
    return bool(default)


def _allow_legacy_meta_keys() -> bool:
    return _env_bool("RAG_ALLOW_LEGACY_META_KEYS", default=False)


def _log_project_key_policy_once() -> None:
    global _PROJECT_KEY_POLICY_LOGGED
    if _PROJECT_KEY_POLICY_LOGGED:
        return
    _PROJECT_KEY_POLICY_LOGGED = True
    mode = "legacy-enabled" if _allow_legacy_meta_keys() else "top-level-only"
    logger.info("project key policy mode=%s (RAG_ALLOW_LEGACY_META_KEYS)", mode)


def _project_key_candidates(kind: str) -> List[str]:
    if kind == "pjt_id":
        primary = str(os.getenv("RAG_KEY_PJT_ID", "pjt_id")).strip() or "pjt_id"
        legacy = "meta_basic.pjt_id"
    else:
        primary = str(os.getenv("RAG_KEY_PJT_NO", "pjt_no")).strip() or "pjt_no"
        legacy = "meta_basic.pjt_no"

    key_cands: List[str] = []
    for key in (primary, kind):
        if key and key not in key_cands:
            key_cands.append(key)

    if _allow_legacy_meta_keys() and legacy not in key_cands:
        key_cands.append(legacy)

    return key_cands


def make_match_any(values: List[str]):
    """qdrant_client 버전 차이를 고려한 MatchAny 생성."""
    if qmodels is None:
        return None
    try:
        return qmodels.MatchAny(any=values)
    except Exception:
        try:
            return qmodels.MatchAny(any_values=values)
        except Exception:
            # 최후의 fallback: 첫 값만 MatchValue로
            if values:
                return qmodels.MatchValue(value=values[0])
            return qmodels.MatchValue(value="")


def _make_nested_condition(key: str, flt: Any) -> Optional[Any]:
    """NestedCondition(nested=Nested(key=..., filter=...)) 생성."""
    if qmodels is None or NestedCondition is None or Nested is None:
        return None
    try:
        return NestedCondition(nested=Nested(key=key, filter=flt))
    except Exception:
        return None


# -----------------------------
# min_should + Filter builder (compat)
# -----------------------------
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

    # ✅ 실제로 Filter가 받아들이는 타입을 테스트로 확정
    dummy_should = [
        qmodels.FieldCondition(
            key="__dummy__", match=qmodels.MatchValue(value="__dummy__")
        )
    ]
    for cand in candidates:
        try:
            _ = qmodels.Filter(should=dummy_should, min_should=cand)
            return cand
        except Exception:
            continue

    return value


def _build_filter(
        *,
        must: Optional[List[Any]],
        should: Optional[List[Any]],
        must_not: Optional[List[Any]],
        min_should: Optional[Any] = None,
) -> Any:
    """qdrant_client 버전별 min_should 시그니처 차이를 흡수하는 Filter 생성."""
    if qmodels is None:
        return None

    kwargs: Dict[str, Any] = {
        "must": must or None,
        "should": should or None,
        "must_not": must_not or None,
    }

    normalized_min_should = (
        min_should
        if (min_should is not None and not isinstance(min_should, (str, int, dict)))
        else _normalize_min_should(min_should)
    )

    if normalized_min_should is not None and kwargs["should"]:
        kwargs["min_should"] = normalized_min_should

    try:
        return qmodels.Filter(**kwargs)
    except Exception:
        # min_should 미지원/타입 불일치 호환:
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
                return qmodels.Filter(
                    must=gate_must or None, should=None, must_not=kwargs.get("must_not")
                )
            except Exception:
                pass

        kwargs.pop("min_should", None)
        return qmodels.Filter(**kwargs)


def and_filter(a: Any, b: Any) -> Any:
    """Filter AND 결합 (must/should/must_not 단순 merge)."""
    if qmodels is None:
        return a or b
    if a is None:
        return b
    if b is None:
        return a

    must: List[Any] = []
    should: List[Any] = []
    must_not: List[Any] = []

    if getattr(a, "must", None):
        must.extend(a.must)
    if getattr(b, "must", None):
        must.extend(b.must)

    if getattr(a, "should", None):
        should.extend(a.should)
    if getattr(b, "should", None):
        should.extend(b.should)

    if getattr(a, "must_not", None):
        must_not.extend(a.must_not)
    if getattr(b, "must_not", None):
        must_not.extend(b.must_not)

    return qmodels.Filter(must=must or None, should=should or None, must_not=must_not or None)


# -----------------------------
# filter_spec compiler (JSON -> Qdrant Filter)
# -----------------------------
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


def compile_filter(filter_spec: Optional[Dict[str, Any]]) -> Optional[Any]:
    """구조화된 filter_spec을 Qdrant Filter로 직렬화한다.
    지원 스키마(예):
    {
      "must":[ {"field":"tag","match_any":["IRD_NAI_RI_PAPER"]},
              {"nested":{"key":"prtcp_mp","filter":{"must":[{"field":"hm_nm","match_any":["신동구"]}]}}}
      ],
      "should":[ ... ],
      "must_not":[ ... ],
      "min_should": 1
    }
    """
    if qmodels is None or not filter_spec:
        return None

    def _to_condition(spec: Any) -> Optional[Any]:
        if spec is None or not isinstance(spec, dict):
            return None

        # nested
        nested_spec = spec.get("nested")
        if isinstance(nested_spec, dict):
            nested_key = str(nested_spec.get("key") or "").strip()
            nested_filter = _to_filter(nested_spec.get("filter") or {})
            if not nested_key or nested_filter is None:
                return None
            return _make_nested_condition(nested_key, nested_filter)

        # field condition
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

        # fallback: treat spec as filter container
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

        min_should_raw = spec.get("min_should")
        min_should = _normalize_min_should(min_should_raw)

        return _build_filter(
            must=must or None,
            should=should or None,
            must_not=must_not or None,
            min_should=min_should if (min_should is not None and should) else None,
        )

    return _to_filter(filter_spec)


# -----------------------------
# org / people term extraction hook
# -----------------------------
def extract_org_terms(q: str, kws: List[str], *, max_terms: int = 3) -> List[str]:
    from .query_intent import extract_org_terms as extract_org_terms_llm

    return extract_org_terms_llm(q, kws, max_terms=max_terms)


# -----------------------------
# Org filters
# -----------------------------
def _build_prtcp_mp_org_nested_filter(terms: List[str]) -> Optional[Any]:
    """prtcp_mp[] 내부의 blng_org_nm 매칭 (Nested)."""
    if qmodels is None or not terms:
        return None
    org_cond = qmodels.FieldCondition(key="blng_org_nm", match=make_match_any(terms))
    nested_filter = qmodels.Filter(must=[org_cond])
    return _make_nested_condition("prtcp_mp", nested_filter)


def build_prtcp_org_nested_filter(spec: OrgFilterInput) -> Optional[Any]:
    """참여기관/참여인력소속기관을 nested로 매칭."""
    terms_norm = normalize_org_terms(spec.terms)
    if qmodels is None or not terms_norm:
        return None

    nested_conditions: List[Any] = []

    # prtcp_org[] -> org_nm
    should_org: List[Any] = [
        qmodels.FieldCondition(key="org_nm", match=make_match_any(terms_norm))
    ]
    nested_filter_org = _build_filter(must=None, should=should_org, must_not=None, min_should=1)
    nested_org = _make_nested_condition("prtcp_org", nested_filter_org)
    if nested_org is not None:
        nested_conditions.append(nested_org)

    # prtcp_mp[] -> blng_org_nm
    nested_mp_org = _build_prtcp_mp_org_nested_filter(terms_norm)
    if nested_mp_org is not None:
        nested_conditions.append(nested_mp_org)

    if not nested_conditions:
        return None
    if len(nested_conditions) == 1:
        return nested_conditions[0]
    return _build_filter(must=None, should=nested_conditions, must_not=None, min_should=1)


def build_org_filter(spec: OrgFilterInput) -> Optional[Any]:
    """기관명 필터.
    - performer/lead/performing: 최상위 org_nm 중심
    - participant: prtcp_org/prtcp_mp nested 중심
    - None: (최상위 org_nm) OR (nested 참여기관/참여인력소속기관)
    """
    terms_norm = normalize_org_terms(spec.terms)
    if qmodels is None or not terms_norm:
        return None

    role = (spec.role or "").strip().lower() or None
    if role == "participant":
        return build_prtcp_org_nested_filter(spec)

    should: List[Any] = []

    # 최상위 기관명
    should.append(qmodels.FieldCondition(key="org_nm", match=make_match_any(terms_norm)))

    # role=None 이면 nested도 함께 OR
    if role is None:
        nested_part = build_prtcp_org_nested_filter(spec)
        if nested_part is not None:
            should.append(nested_part)

    if not should:
        return None
    return _build_filter(must=None, should=should, must_not=None, min_should=1)


# -----------------------------
# Tag / Title / Year filters
# -----------------------------
def build_tag_only_filter(tags: List[str]) -> Optional[Any]:
    if qmodels is None or not tags:
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

    should: List[Any] = []

    # --- (A) ✅ year_keys: 문자열 매치는 항상 추가 (stan_yr="2025" 대응) ---
    y_values: List[str] = []
    if y_from is not None and y_to is not None:
        span = max(0, min(60, y_to - y_from))
        y_values = [str(y_from + i) for i in range(span + 1)]
    elif y_from is not None:
        y_values = [str(y_from)]
    elif y_to is not None:
        y_values = [str(y_to)]

    if y_values:
        for key in year_keys:
            should.append(qmodels.FieldCondition(key=key, match=make_match_any(y_values)))

    # --- (B) year_keys: 숫자 range도 같이 유지 (숫자 저장 케이스 대비) ---
    year_range = _make_range_filter(y_from, y_to)
    if year_range is not None:
        for key in year_keys:
            should.append(qmodels.FieldCondition(key=key, range=year_range))

    # --- (C) date_keys: datetime range (여긴 스키마에 따라 먹힐 수도/안 먹힐 수도 있음: 확실하지 않음) ---
    if y_from is not None or y_to is not None:
        date_from = f"{y_from}-01-01" if y_from is not None else None
        date_to = f"{y_to}-12-31" if y_to is not None else None
        date_range = _make_range_filter(date_from, date_to)
        if date_range is not None:
            for key in date_keys:
                should.append(qmodels.FieldCondition(key=key, range=date_range))

    if not should:
        return None

    # ✅ should-only 필터는 min_should=1로 게이트 권장 (호환 로직 재사용)
    return _build_filter(must=None, should=should, must_not=None, min_should=1)


def _build_title_filter_with_keys(terms: List[str], keys: List[str]) -> Optional[Any]:
    if qmodels is None:
        return None
    norm_terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
    if not norm_terms:
        return None
    should: List[Any] = []
    for key in keys:
        if not key:
            continue
        should.append(qmodels.FieldCondition(key=key, match=make_match_any(norm_terms)))
    if not should:
        return None
    return _build_filter(must=None, should=should, must_not=None, min_should=1)


def build_title_exact_filter(terms: List[str]) -> Optional[Any]:
    """문서 제목(과제/성과 공통) EXACT(match-any) 기반 서버단 필터."""
    keys = ["title_text", "title1", "title2"]
    return _build_title_filter_with_keys(terms, keys)


def build_title_text_filter(terms: List[str]) -> Optional[Any]:
    """문서 제목(과제/성과 공통) TEXT(match-text) 기반 서버단 필터."""
    if qmodels is None:
        return None
    norm_terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
    if not norm_terms:
        return None
    keys = ["title_text", "title1", "title2"]
    should: List[Any] = []
    match_text_cls = getattr(qmodels, "MatchText", None)
    if match_text_cls is None:
        return None
    for key in keys:
        if not key:
            continue
        for term in norm_terms:
            should.append(qmodels.FieldCondition(key=key, match=match_text_cls(text=term)))
    if not should:
        return None
    return _build_filter(must=None, should=should, must_not=None, min_should=1)


# -----------------------------
# People filters (nested prtcp_mp)
# -----------------------------
def _build_prtcp_mp_people_nested_filter(
        *,
        people_terms: Optional[List[str]] = None,
        person_ids: Optional[List[str]] = None,
        gender_terms: Optional[List[str]] = None,
        org_terms: Optional[List[str]] = None,
        min_should: Optional[Any] = None,
        promote_one_must: bool = False,
) -> Optional[Any]:
    """prtcp_mp[] 내부에서 사람/소속기관/성별 조건을 한 객체에 묶어 매칭."""
    if qmodels is None:
        return None

    people_terms = [str(x).strip() for x in (people_terms or []) if str(x).strip()]
    person_ids = [str(x).strip() for x in (person_ids or []) if str(x).strip()]
    gender_terms = [str(x).strip() for x in (gender_terms or []) if str(x).strip()]
    org_terms = normalize_org_terms([str(x).strip() for x in (org_terms or []) if str(x).strip()])

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
        # ⚠️ 실제 payload 키가 다르면 여기서 안 먹힘 (스키마 확인 필요)
        nested_should.append(qmodels.FieldCondition(key="gndr_slct_nm", match=make_match_any(gender_terms)))

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
    return _make_nested_condition("prtcp_mp", nested_filter)


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


# -----------------------------
# Perf type helpers
# -----------------------------
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


def build_perf_type_filter(perf_types: List[str]) -> Optional[Any]:
    """perf 컬렉션에서 tag 기반 성과 유형 필터."""
    if qmodels is None or not perf_types:
        return None
    key_tag = (os.getenv("RAG_KEY_TAG", "tag").strip() or "tag")
    return qmodels.Filter(must=[qmodels.FieldCondition(key=key_tag, match=make_match_any(perf_types))])


# -----------------------------
# Project id filters
# -----------------------------
def build_project_id_filter(pjt_ids: List[str], pjt_nos: List[str]) -> Optional[Any]:
    """PJT_ID/PJT_NO 기반 서버단 필터.

    pjt_id/pjt_no 혼합은 정책 위반으로 즉시 실패한다.
    """
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

    if pjt_id_values and pjt_no_values:
        raise ValueError("build_project_id_filter는 pjt_id 또는 pjt_no 단일 타입만 허용합니다.")

    _log_project_key_policy_once()
    id_key_cands = _project_key_candidates("pjt_id")
    no_key_cands = _project_key_candidates("pjt_no")

    should: List[Any] = []
    if pjt_id_values:
        should.extend(qmodels.FieldCondition(key=k, match=make_match_any(pjt_id_values)) for k in id_key_cands)
    if pjt_no_values:
        should.extend(qmodels.FieldCondition(key=k, match=make_match_any(pjt_no_values)) for k in no_key_cands)

    return _build_filter(must=None, should=should, must_not=None, min_should=1)


# -----------------------------
# JOIN / PERF filters (server-side)
# -----------------------------
def build_join_filter(spec: JoinFilterInput) -> "qmodels.Filter":
    """JOIN Hop2용 필터."""
    if qmodels is None:
        raise RuntimeError("qdrant_client is required for build_join_filter()")

    if spec.filter_spec is not None:
        compiled = compile_filter(spec.filter_spec)
        return compiled or qmodels.Filter(must=[])

    join_ids = list(dict.fromkeys(str(x).strip() for x in (spec.join_ids or []) if str(x).strip()))
    pjt_nos = list(dict.fromkeys(str(x).strip() for x in (spec.pjt_nos or []) if str(x).strip()))

    mode = str(spec.join_key_mode or "instance").strip().lower()
    if mode not in ("instance", "group"):
        raise ValueError(f"지원하지 않는 join_key_mode 입니다: {spec.join_key_mode}")

    validate_join_mode_key_inputs(mode=mode, join_ids=join_ids, pjt_nos=pjt_nos)

    _log_project_key_policy_once()
    primary_id = _project_key_candidates("pjt_id")[0]
    primary_no = _project_key_candidates("pjt_no")[0]

    must: List[Any] = []
    if mode == "group":
        must.append(qmodels.FieldCondition(key=primary_no, match=make_match_any(pjt_nos)))
    else:
        must.append(qmodels.FieldCondition(key=primary_id, match=make_match_any(join_ids)))

    validate_join_filter_must_keys(mode=mode, must_conditions=must)

    return qmodels.Filter(must=must)


def build_collection_join_filter(
        *,
        hop2_col: str,
        join_key_mode: str,
        join_ids: List[str],
        pjt_nos: List[str],
        query: str = "",
        fallback_spec: Optional[JoinFilterInput] = None,
) -> "qmodels.Filter":
    """Hop2 컬렉션 기준으로 JOIN 필터를 생성한다.

    - hop2_col=perf: perf 전용 필터를 사용
    - hop2_col=project: project 전용 필터를 사용
    - 그 외: build_join_filter 로 폴백
    """
    mode = str(join_key_mode or "instance").strip().lower()
    if mode not in ("instance", "group"):
        raise ValueError(f"지원하지 않는 join_key_mode 입니다: {join_key_mode}")

    validate_join_mode_key_inputs(mode=mode, join_ids=join_ids, pjt_nos=pjt_nos)

    col_norm = str(hop2_col or "").strip().lower()

    def _canonical_collection(name: str) -> str:
        norm = str(name or "").strip().lower()
        if not norm:
            return ""

        perf_aliases = {"ntis_perf", str(COL_PERF or "").strip().lower()}
        project_aliases = {"ntis_project", str(COL_PROJECT or "").strip().lower()}

        if norm in perf_aliases or norm.startswith("ntis_perf"):
            return "ntis_perf"
        if norm in project_aliases or norm.startswith("ntis_project"):
            return "ntis_project"
        return norm

    col_canonical = _canonical_collection(col_norm)
    if col_canonical == "ntis_perf":
        if mode == "group":
            return build_perf_filter_by_pjt_no(pjt_nos, query)
        return build_perf_filter_by_pjt_id(join_ids, query)

    if col_canonical == "ntis_project":
        pjt_filter = build_project_id_filter(join_ids if mode == "instance" else [], pjt_nos if mode == "group" else [])
        return pjt_filter or qmodels.Filter(must=[])

    if fallback_spec is None:
        fallback_spec = JoinFilterInput(
            join_ids=join_ids,
            pjt_nos=pjt_nos,
            join_key_mode=mode,
        )
    return build_join_filter(fallback_spec)


def _is_pjt_id_key(key: str) -> bool:
    k = str(key or "").strip().lower()
    return bool(k) and (k == "pjt_id" or k.endswith(".pjt_id"))


def _is_pjt_no_key(key: str) -> bool:
    k = str(key or "").strip().lower()
    return bool(k) and (k == "pjt_no" or k.endswith(".pjt_no"))


def validate_join_mode_key_inputs(*, mode: str, join_ids: List[str], pjt_nos: List[str]) -> None:
    """join_key_mode와 실제 join key 입력의 정합성을 검증한다."""
    mode_norm = str(mode or "").strip().lower()
    join_ids_norm = _dedupe_non_empty(join_ids)
    pjt_nos_norm = _dedupe_non_empty(pjt_nos)

    if mode_norm == "group":
        if not pjt_nos_norm:
            raise ValueError("join_key_mode=group 에서는 pjt_nos 가 반드시 필요합니다.")
        if join_ids_norm:
            raise ValueError("join_key_mode=group 에서는 pjt_no 계열 key만 허용합니다.")
        return

    if mode_norm == "instance":
        if not join_ids_norm:
            raise ValueError("join_key_mode=instance 에서는 join_ids(pjt_id) 가 반드시 필요합니다.")
        if pjt_nos_norm:
            raise ValueError("join_key_mode=instance 에서는 pjt_id 계열 key만 허용합니다.")
        return

    raise ValueError(f"지원하지 않는 join_key_mode 입니다: {mode}")


def validate_join_filter_must_keys(*, mode: str, must_conditions: List[Any]) -> None:
    """생성된 must 조건의 key가 join_key_mode와 일치하는지 검증한다."""
    mode_norm = str(mode or "").strip().lower()
    for cond in (must_conditions or []):
        key = getattr(cond, "key", None)
        if not key:
            continue
        if mode_norm == "group" and not _is_pjt_no_key(key):
            raise ValueError(f"group 모드에서는 pjt_no 계열 key만 허용합니다: {key}")
        if mode_norm == "instance" and not _is_pjt_id_key(key):
            raise ValueError(f"instance 모드에서는 pjt_id 계열 key만 허용합니다: {key}")


def _dedupe_non_empty(values: List[str]) -> List[str]:
    return list(dict.fromkeys(str(v).strip() for v in (values or []) if str(v).strip()))


def _build_perf_filter_for_keys(join_values: List[str], key_cands: List[str], query: str = "") -> "qmodels.Filter":
    if qmodels is None:
        raise RuntimeError("qdrant_client is required for perf filter build")

    must: List[Any] = []
    join_values_norm = _dedupe_non_empty(join_values)
    if join_values_norm:
        join_any = _build_filter(
            must=None,
            should=[
                qmodels.FieldCondition(
                    key=k,
                    match=make_match_any(join_values_norm),
                )
                for k in key_cands
            ],
            must_not=None,
            min_should=1,
        )
        must.append(join_any)

    tag_filters = pick_perf_tag_filters(query)
    if tag_filters:
        must.append(qmodels.FieldCondition(key="tag", match=make_match_any(tag_filters)))

    return qmodels.Filter(must=must, must_not=[])


def build_perf_filter_by_pjt_id(pjt_ids: List[str], query: str = "") -> "qmodels.Filter":
    """PJT_ID 키 계열만 사용해서 perf 필터를 생성한다."""
    _log_project_key_policy_once()
    return _build_perf_filter_for_keys(pjt_ids, _project_key_candidates("pjt_id"), query)


def build_perf_filter_by_pjt_no(pjt_nos: List[str], query: str = "") -> "qmodels.Filter":
    """PJT_NO 키 계열만 사용해서 perf 필터를 생성한다."""
    _log_project_key_policy_once()
    return _build_perf_filter_for_keys(pjt_nos, _project_key_candidates("pjt_no"), query)


def build_perf_filter(spec: PerfFilterInput) -> "qmodels.Filter":
    """(호환용) join_ids 타입을 검증해 pjt_id/pjt_no 전용 API로 위임한다."""
    join_ids = _dedupe_non_empty(spec.join_ids)
    if not join_ids:
        return _build_perf_filter_for_keys([], [], spec.query)

    has_pjt_id = any(_PJT_ID_VALUE_RE.fullmatch(v or "") for v in join_ids)
    has_pjt_no = any(not _PJT_ID_VALUE_RE.fullmatch(v or "") for v in join_ids)
    if has_pjt_id and has_pjt_no:
        raise ValueError("PerfFilterInput.join_ids는 pjt_id 또는 pjt_no 단일 타입만 허용합니다.")

    if has_pjt_id:
        return build_perf_filter_by_pjt_id(join_ids, spec.query)
    return build_perf_filter_by_pjt_no(join_ids, spec.query)
