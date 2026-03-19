# -*- coding: utf-8 -*-
"""Qdrant filter builders for planner and executor runtime.\n\n- Supports org, people, join, performance, year-range, title, and tag filters.\n- Converts JSON-style filter specs into Qdrant filter objects, including nested filters.\n"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from apps.core.query_intent import normalize_org_terms

from apps.core.rag_constants import (
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
from apps.core.planner_contract import StrategyViolation

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
    """기관명 필터링에 필요한 입력을 묶는다.
    
    role=None이면 상위 기관과 참여 기관 경로를 함께 허용하고,
    role=participant이면 참여기관 nested 경로로만 조회를 고정한다."""
    terms: List[str] = field(default_factory=list)
    role: Optional[str] = None  # performer/lead/participant/None


@dataclass(frozen=True)
class PeopleFilterInput:
    """참여인력 nested 필터의 입력을 묶는다.
    
    이름, 식별자, 성별, 소속기관, min_should 정책을 함께 넘기며,
    filter_spec가 주어지면 휴리스틱 조합 대신 그 스펙을 그대로 우선한다."""
    people_terms: List[str] = field(default_factory=list)
    person_ids: List[str] = field(default_factory=list)
    gender_terms: List[str] = field(default_factory=list)
    org_terms: List[str] = field(default_factory=list)
    filter_spec: Optional[Dict[str, Any]] = None
    min_should: Optional[int] = None
    promote_one_must: bool = False


@dataclass(frozen=True)
class JoinFilterInput:
    """JOIN hop2 필터 조립에 쓰는 입력 모델이다.
    
    join_key_mode가 `instance`면 pjt_id, `group`이면 pjt_no 계약을 강제한다."""
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
    """성과 컬렉션 필터 조립에 쓰는 최소 입력이다."""
    query: str = ""
    join_ids: List[str] = field(default_factory=list)


_PJT_ID_VALUE_RE = re.compile(r"^(?:\d{8,12}|(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]{4,64})$")
_PJT_NO_VALUE_RE = re.compile(r"^PJT[-_/]?[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+){1,}$", re.IGNORECASE)


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


def _env_int(name: str, default: int) -> int:
    """환경변수를 정수로 읽고 파싱 실패 시 기본값으로 돌아간다."""
    raw = str(os.getenv(name, str(default))).strip()
    try:
        return int(raw)
    except Exception:
        return int(default)

def _log_project_key_policy_once() -> None:
    """프로젝트 키 정책을 한 번만 로그에 남긴다."""
    global _PROJECT_KEY_POLICY_LOGGED
    if _PROJECT_KEY_POLICY_LOGGED:
        return
    _PROJECT_KEY_POLICY_LOGGED = True
    logger.info("project key policy mode=top-level-only")


def _project_key_candidates(kind: str) -> List[str]:
    """환경변수 alias와 기본 키를 함께 보존해 pjt_id/pjt_no 후보 목록을 만든다."""
    if kind == "pjt_id":
        primary = str(os.getenv("RAG_KEY_PJT_ID", "pjt_id")).strip() or "pjt_id"
    else:
        primary = str(os.getenv("RAG_KEY_PJT_NO", "pjt_no")).strip() or "pjt_no"

    key_cands: List[str] = []
    for key in (primary, kind):
        if key and key not in key_cands:
            key_cands.append(key)

    return key_cands


def make_match_any(values: List[str]):
    """Qdrant client 버전 차이를 흡수하며 MatchAny 객체를 만든다.
    
    MatchAny가 없는 경우에는 MatchValue로 후퇴해 실행 실패를 피한다."""
    if qmodels is None:
        return None
    try:
        return qmodels.MatchAny(any=values)
    except Exception:
        try:
            return qmodels.MatchAny(any_values=values)
        except Exception:
            # Fallback path: downgrade to MatchValue when MatchAny is unavailable.
            if values:
                return qmodels.MatchValue(value=values[0])
            return qmodels.MatchValue(value="")


def _make_nested_condition(key: str, flt: Any) -> Optional[Any]:
    """Qdrant client가 nested filter를 지원할 때만 NestedCondition으로 감싼다."""
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
    """min_should 입력을 client 버전에 맞는 형태로 정규화한다.
    
    dict, int, MinShould 모델 후보를 순차적으로 시도하고,
    실제 qmodels.Filter가 받을 수 있는 값만 돌려준다."""
    if qmodels is None:
        return None
    if min_should in (None, ""):
        return None

    # Dict specs are treated as explicit field operators instead of scalar matches.
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

    # 1) Prefer the native MinShould model when the client supports it.
    min_should_cls = getattr(qmodels, "MinShould", None)
    candidates: List[Any] = []
    if min_should_cls is not None:
        for kwargs in ({"min_count": value}, {"count": value}):
            try:
                candidates.append(min_should_cls(**kwargs))
            except Exception:
                pass

    # 2) exact dict match
    candidates.append({"min_count": value})
    candidates.append({"count": value})

    # 3) exact integer match
    candidates.append(value)

    # 4) explicit Filter object passed through as-is
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
    """must/should/must_not 입력을 합쳐 Qdrant Filter를 만든다.
    
    min_should을 직접 못 받는 client는 should-only gate filter로 우회한다."""
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
        # min_should semantics:
        # should clauses are wrapped through must=[Filter(should=...)] when needed.
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
    """두 Filter의 must/should/must_not 조건을 보존하며 AND 로 합성한다."""
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
    """Qdrant Range 생성자 차이를 흡수하며 범위 객체를 조립한다."""
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
    """JSON 형태의 filter_spec을 Qdrant Filter로 컴파일한다.
    
    nested, match, match_any, range 구문을 해석하며 빈 조건은 제거한다."""
    if qmodels is None or not filter_spec:
        return None

    def _to_condition(spec: Any) -> Optional[Any]:
        """field 조건, nested 조건, 하위 filter container를 개별 condition으로 바꾼다."""
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
        """must/should/must_not 배열을 순회해 실제 Filter 객체를 조립한다."""
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


def _expand_org_partial_terms(terms: List[str]) -> List[str]:
    """기관명 원문과 prefix 파생값을 함께 만든다.
    
    파생 prefix는 환경변수 정책으로 제한하고 로그에 남긴다."""
    terms_norm = normalize_org_terms(terms)
    if not terms_norm:
        return []

    prefix_len = max(1, _env_int("RAG_ORG_PARTIAL_PREFIX_LEN", 6))
    min_len = max(1, _env_int("RAG_ORG_PARTIAL_MIN_LEN", 4))
    expanded: List[str] = []
    for term in terms_norm:
        if term not in expanded:
            expanded.append(term)
        if len(term) >= min_len:
            prefix = term[:prefix_len].strip()
            if prefix and prefix != term and prefix not in expanded:
                expanded.append(prefix)

    logger.info(
        "RAG.ORG.MATCH.POLICY prefix_len=%s min_len=%s terms_preview=%s expanded_terms_preview=%s",
        prefix_len,
        min_len,
        terms_norm[:4],
        expanded[:8],
    )
    return expanded


def _build_match_text_conditions(key: str, terms: List[str]) -> List[Any]:
    """MatchText를 지원하는 client에서 텍스트 조건 목록을 만든다."""
    if qmodels is None:
        return []
    match_text_cls = getattr(qmodels, "MatchText", None)
    if match_text_cls is None:
        return []
    conds: List[Any] = []
    for term in terms:
        conds.append(qmodels.FieldCondition(key=key, match=match_text_cls(text=term)))
    return conds


# -----------------------------
# Org filters
# -----------------------------
def _build_prtcp_mp_org_nested_filter(terms: List[str]) -> Optional[Any]:
    """참여인력 소속기관 `blng_org_nm` 경로용 nested 필터를 만든다."""
    if qmodels is None or not terms:
        return None
    should = _build_match_text_conditions("blng_org_nm", terms)
    if not should:
        return None
    nested_filter = _build_filter(must=None, should=should, must_not=None, min_should=1)
    return _make_nested_condition("prtcp_mp", nested_filter)


def build_prtcp_org_nested_filter(spec: OrgFilterInput) -> Optional[Any]:
    """참여기관 경로와 참여인력 소속기관 경로를 OR로 묶는 nested 필터다."""
    terms_norm = _expand_org_partial_terms(spec.terms)
    if qmodels is None or not terms_norm:
        return None

    nested_conditions: List[Any] = []

    # prtcp_org[] -> org_nm
    should_org = _build_match_text_conditions("org_nm", terms_norm)
    if not should_org:
        return None
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
    """기관명 조건을 상위 `org_nm`과 nested 참여기관 경로에 배치한다.
    
    role=participant이면 nested 경로로만 조회하고, role=None이면 두 경로를 OR로 허용한다."""
    terms_norm = _expand_org_partial_terms(spec.terms)
    if qmodels is None or not terms_norm:
        return None

    role = (spec.role or "").strip().lower() or None
    if role == "participant":
        return build_prtcp_org_nested_filter(spec)

    should: List[Any] = []

    # Top-level organization filter path.
    should.extend(_build_match_text_conditions("org_nm", terms_norm))

    # role=None keeps the nested paths in an OR relationship.
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
    """tag 필드에 대한 단순 must 필터를 만든다."""
    if qmodels is None or not tags:
        return None
    key_tag = (os.getenv("RAG_KEY_TAG", "tag").strip() or "tag")
    return qmodels.Filter(must=[qmodels.FieldCondition(key=key_tag, match=make_match_any(tags))])


def _coerce_year_value(value: Any) -> Optional[int]:
    """연도로 해석할 수 있는 값만 4자리 정수로 바꾼다."""
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
    # Expand org terms conservatively and keep nested filters in should clauses when needed.
    # Notes:
    # - min_should=1 keeps a should-only filter semantically active.
    """연도와 날짜 필드를 함께 거는 should-only 범위 필터다.
    
    year key와 date key에 동시에 적용하고, should만 있는 필터가 무효화되지 않도록 min_should=1을 걸어둔다."""
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

    # --- (A) year_keys: year-like numeric range fields ---
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

    # --- (B) year_keys: integer-like range fields ---
    year_range = _make_range_filter(y_from, y_to)
    if year_range is not None:
        for key in year_keys:
            should.append(qmodels.FieldCondition(key=key, range=year_range))

    # --- (C) date_keys: datetime range fields ---
    if y_from is not None or y_to is not None:
        date_from = f"{y_from}-01-01" if y_from is not None else None
        date_to = f"{y_to}-12-31" if y_to is not None else None
        date_range = _make_range_filter(date_from, date_to)
        if date_range is not None:
            for key in date_keys:
                should.append(qmodels.FieldCondition(key=key, range=date_range))

    if not should:
        return None

    # should-only filters require min_should=1 to stay semantically active.
    return _build_filter(must=None, should=should, must_not=None, min_should=1)


def _build_title_filter_with_keys(terms: List[str], keys: List[str]) -> Optional[Any]:
    """제목 텍스트를 여러 필드 후보에 대해 OR 조건으로 조립한다."""
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
    """주요 제목 필드에 대한 정확 매칭 필터를 만든다."""
    keys = ["title_text", "title1", "title2"]
    return _build_title_filter_with_keys(terms, keys)


def build_title_text_filter(terms: List[str]) -> Optional[Any]:
    """MatchText 지원 여부와 match mode에 맞춰 제목 텍스트 필터를 만든다."""
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
    """참여인력 nested 필터의 핵심 조합기다.
    
    hm_id는 보통 must로, hm_nm/gender/org 조건은 should 또는 선택적 must로 실어 인물 조건을 조합한다."""
    if qmodels is None:
        return None

    people_terms = [str(x).strip() for x in (people_terms or []) if str(x).strip()]
    person_ids = [str(x).strip() for x in (person_ids or []) if str(x).strip()]
    gender_terms = [str(x).strip() for x in (gender_terms or []) if str(x).strip()]
    org_terms = normalize_org_terms([str(x).strip() for x in (org_terms or []) if str(x).strip()])
    org_terms = _expand_org_partial_terms(org_terms)

    force_one_must = bool(promote_one_must and not person_ids and len(people_terms) == 1)

    nested_must: List[Any] = []
    nested_should: List[Any] = []

    # Join and people filters use a mix of must and should rules:
    # - person_ids(hm_id): exact identifier match, usually promoted to must.
    # - people_terms(hm_nm): text match that may stay should or be promoted to must.
    # - org/gender conditions usually stay in should unless the caller hardens them.
    if people_terms:
        name_cond = qmodels.FieldCondition(key="hm_nm", match=make_match_any(people_terms))
        if force_one_must:
            nested_must.append(name_cond)
        else:
            nested_should.append(name_cond)

    if person_ids:
        nested_must.append(qmodels.FieldCondition(key="hm_id", match=make_match_any(person_ids)))

    if gender_terms:
        # Normalize raw values so payload matching stays stable across scalar and list forms.
        nested_should.append(qmodels.FieldCondition(key="gndr_slct_nm", match=make_match_any(gender_terms)))

    if org_terms:
        nested_should.extend(_build_match_text_conditions("blng_org_nm", org_terms))

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
    """PeopleFilterInput을 participant member nested 필터로 바꾸거나 직접 filter_spec을 컴파일한다."""
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
    """한국어/영어 키워드를 성과 tag 필터 목록으로 바꾼다."""
    t = (q or "").lower()
    tags: List[str] = []

    def _add(tag: str):
        """유효한 태그만 중복 없이 추가한다."""
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
    if ("기술요약" in t) or (("기술" in t) and ("요약" in t)) or ("tech" in t):
        _add(TAG_RI_TECH_INFO)
    if ("소프트웨어" in t) or ("software" in t) or ("sw" in t):
        _add(TAG_RI_SW)
    if ("생명정보" in t) or ("nvr" in t):
        _add(TAG_RI_NVR)
    if ("화합물" in t) or ("compound" in t):
        _add(TAG_RI_COMPOUND)
    if "생물정보" in t:
        _add(TAG_RI_ORGSM_INFO)
    if ("생물자원" in t) or ("resource" in t):
        _add(TAG_RI_ORGSM_RES)

    return tags


def build_perf_type_filter(perf_types: List[str]) -> Optional[Any]:
    """정규화된 성과 태그 목록을 tag must 필터로 만든다."""
    if qmodels is None or not perf_types:
        return None
    key_tag = (os.getenv("RAG_KEY_TAG", "tag").strip() or "tag")
    return qmodels.Filter(must=[qmodels.FieldCondition(key=key_tag, match=make_match_any(perf_types))])


# -----------------------------
# Project id filters
# -----------------------------
def build_project_id_filter(pjt_ids: List[str], pjt_nos: List[str]) -> Optional[Any]:
    """project 컬렉션에서 pjt_id 또는 pjt_no 하나만 쓰는 식별자 필터다.
    
    두 키를 함께 받으면 의미 혼합으로 간주하고 예외를 올린다."""
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
        raise ValueError("build_project_id_filter requires either pjt_id or pjt_no, but not both")

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
    """planner/executor가 확정한 join_key_mode를 그대로 따르는 strict JOIN 필터다.
    
    instance는 pjt_id, group은 pjt_no must 조건만 허용한다."""
    if qmodels is None:
        raise RuntimeError("qdrant_client is required for build_join_filter()")

    if spec.filter_spec is not None:
        compiled = compile_filter(spec.filter_spec)
        return compiled or qmodels.Filter(must=[])

    join_ids = list(dict.fromkeys(str(x).strip() for x in (spec.join_ids or []) if str(x).strip()))
    pjt_nos = list(dict.fromkeys(str(x).strip() for x in (spec.pjt_nos or []) if str(x).strip()))

    mode = str(spec.join_key_mode or "").strip().lower()
    if mode not in ("instance", "group"):
        raise ValueError(f"JOIN_KEY_MODE_INVALID: unsupported join_key_mode={spec.join_key_mode}")

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
        resolved_pjt_ids: Optional[List[str]] = None,
        perf_group_strategy: str = "prefer_pjt_no",
        query: str = "",
        apply_query_tag_inference: bool = False,
        fallback_spec: Optional[JoinFilterInput] = None,
) -> "qmodels.Filter":
    """hop2 컬렉션 종류에 따라 project/perf JOIN 필터 전략을 고른다.
    
    group JOIN의 perf hop2는 pjt_no를 우선하고, 허용된 경우에만 resolved pjt_id fallback을 쓴다."""
    _ = apply_query_tag_inference
    mode = str(join_key_mode or "").strip().lower()
    if mode not in ("instance", "group"):
        raise ValueError(f"JOIN_KEY_MODE_INVALID: unsupported join_key_mode={join_key_mode}")

    resolved_pjt_ids = _dedupe_non_empty(resolved_pjt_ids or [])
    pjt_nos_norm = _dedupe_non_empty(pjt_nos)

    col_norm = str(hop2_col or "").strip().lower()

    def _canonical_collection(name: str) -> str:
        """컬렉션 alias를 runtime에서 쓰는 canonical 이름으로 맞춘다."""
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
            # Group joins prefer pjt_no and only use pjt_id as a last-resort fallback when allowed.
            validate_group_join_runtime_keys(pjt_nos=pjt_nos_norm, pjt_ids=resolved_pjt_ids)
            if not pjt_nos_norm:
                return build_perf_filter_by_pjt_id(
                    resolved_pjt_ids,
                    query,
                    apply_query_tag_inference=False,
                )
            return build_perf_filter_group_resolved(
                pjt_nos_norm,
                resolved_pjt_ids,
                strategy=perf_group_strategy,
                query=query,
                apply_query_tag_inference=False,
            )
        return build_perf_filter_by_pjt_id(join_ids, query, apply_query_tag_inference=False)

    # For non-perf hop2 paths, planner join_key_mode still defines the runtime key contract.
    validate_join_mode_key_inputs(mode=mode, join_ids=join_ids, pjt_nos=pjt_nos_norm)

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
    """필드 키가 pjt_id 계열인지 판단한다."""
    k = str(key or "").strip().lower()
    return bool(k) and (k == "pjt_id" or k.endswith(".pjt_id"))


def _is_pjt_no_key(key: str) -> bool:
    """필드 키가 pjt_no 계열인지 판단한다."""
    k = str(key or "").strip().lower()
    return bool(k) and (k == "pjt_no" or k.endswith(".pjt_no"))


def _normalize_ids_map(ids_map: Any) -> Dict[str, List[str]]:
    """ids_map 입력을 빈 값·`none`·중복을 제거한 문자열 리스트로 정리한다."""
    if not isinstance(ids_map, dict):
        return {}

    normalized: Dict[str, List[str]] = {}
    for key, values in ids_map.items():
        seq = values if isinstance(values, (list, tuple, set)) else [values]
        cleaned: List[str] = []
        seen: set[str] = set()
        for value in seq:
            text = str(value).strip()
            if not text or text.lower() == "none" or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        if cleaned:
            normalized[str(key)] = cleaned

    return normalized

def validate_planner_join_keys(mode: str, ids_map: Any) -> Dict[str, List[str]]:
    """planner가 lookup/join 모드에서 pjt_id와 pjt_no를 혼용하지 못하게 검증한다."""
    mode_norm = str(mode or "").strip().lower()
    normalized_ids_map = _normalize_ids_map(ids_map)

    has_pjt_id = bool(normalized_ids_map.get("pjt_id"))
    has_pjt_no = bool(normalized_ids_map.get("pjt_no"))
    if has_pjt_id and has_pjt_no and mode_norm in ("lookup", "join"):
        raise ValueError(
            f"PLANNER_MIXED_PROJECT_KEYS: ids_map.pjt_id and ids_map.pjt_no cannot coexist for planner mode={mode_norm}"
        )

    return normalized_ids_map


def validate_resolved_join_keys(mode: str, pjt_nos: List[str], pjt_ids: List[str]) -> None:
    """executor 런타임에서 join_key_mode별 실제 키가 맞는지 검증한다."""
    mode_norm = str(mode or "").strip().lower()
    pjt_ids_norm = _dedupe_non_empty(pjt_ids)
    pjt_nos_norm = _dedupe_non_empty(pjt_nos)

    if mode_norm == "group":
        if not (pjt_nos_norm or pjt_ids_norm):
            raise ValueError(
                "EXECUTOR_GROUP_RUNTIME_KEY_REQUIRED: join_key_mode=group requires pjt_nos or equivalent resolved pjt_ids"
            )
        return

    if mode_norm == "instance":
        if not pjt_ids_norm:
            raise ValueError("EXECUTOR_INSTANCE_PJT_ID_REQUIRED: join_key_mode=instance requires non-empty pjt_ids")
        if pjt_nos_norm:
            raise ValueError("EXECUTOR_INSTANCE_PJT_NO_FORBIDDEN: join_key_mode=instance forbids pjt_no runtime keys")
        return

    raise ValueError(f"EXECUTOR_JOIN_KEY_MODE_INVALID: unsupported join_key_mode={mode}")


def validate_group_join_runtime_keys(*, pjt_nos: List[str], pjt_ids: List[str]) -> None:
    """group JOIN hop2는 pjt_no 또는 동치인 resolved pjt_id가 반드시 있어야 한다."""
    pjt_nos_norm = _dedupe_non_empty(pjt_nos)
    pjt_ids_norm = _dedupe_non_empty(pjt_ids)
    if pjt_nos_norm or pjt_ids_norm:
        return
    raise ValueError(
        "EXECUTOR_GROUP_RUNTIME_KEY_REQUIRED: group hop2 execution requires pjt_nos or an equivalent resolved runtime key"
    )


def validate_join_mode_key_inputs(*, mode: str, join_ids: List[str], pjt_nos: List[str]) -> None:
    """join_key_mode와 join_ids/pjt_nos 조합이 strict 계약에 맞는지 검증한다."""
    mode_norm = str(mode or "").strip().lower()
    join_ids_norm = _dedupe_non_empty(join_ids)
    pjt_nos_norm = _dedupe_non_empty(pjt_nos)

    if mode_norm == "instance":
        if join_ids_norm and not pjt_nos_norm:
            return
        raise StrategyViolation(
            error_code="EXECUTOR_JOIN_KEY_INPUT_INVALID",
            reason="instance requires pjt_id and forbids pjt_no",
        )

    if mode_norm == "group":
        if pjt_nos_norm and not join_ids_norm:
            return
        raise StrategyViolation(
            error_code="EXECUTOR_JOIN_KEY_INPUT_INVALID",
            reason="group requires pjt_no and forbids pjt_id",
        )

    raise StrategyViolation(
        error_code="EXECUTOR_JOIN_KEY_MODE_INVALID",
        reason=f"unsupported join_key_mode: {mode}",
    )


def validate_join_filter_must_keys(*, mode: str, must_conditions: List[Any]) -> None:
    """실제로 조립된 JOIN must 조건이 mode에 맞는 키를 쓰는지 확인한다."""
    mode_norm = str(mode or "").strip().lower()
    for cond in (must_conditions or []):
        key = getattr(cond, "key", None)
        if not key:
            continue
        if mode_norm == "group" and not _is_pjt_no_key(key):
            raise ValueError(f"group join mode requires a pjt_no runtime key, got {key}")
        if mode_norm == "instance" and not _is_pjt_id_key(key):
            raise ValueError(f"instance join mode requires a pjt_id runtime key, got {key}")


def _dedupe_non_empty(values: List[str]) -> List[str]:
    """비어 있지 않은 문자열만 중복 없이 유지한다."""
    return list(dict.fromkeys(str(v).strip() for v in (values or []) if str(v).strip()))


def _build_perf_filter_for_keys(
        join_values: List[str],
        key_cands: List[str],
        query: str = "",
        *,
        apply_query_tag_inference: bool = False,
) -> "qmodels.Filter":
    """perf hop2에 대한 join key gate 필터를 만든다.
    
    여기서 만든 must 조건이 hop2 관계 의미를 잡고, query-time tag 필터는 그 위에만 얹혀야 한다."""
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

    # JOIN hop2 must filter should always contain the resolved join key gate.
    # Query-time perf tags may be appended, but they must not replace the join key gate.
    _ = (query, apply_query_tag_inference)

    return qmodels.Filter(must=must, must_not=[])


def build_perf_filter_by_pjt_id(
        pjt_ids: List[str],
        query: str = "",
        *,
        apply_query_tag_inference: bool = False,
) -> "qmodels.Filter":
    """pjt_id 런타임 키로 perf hop2 필터를 만든다."""
    _log_project_key_policy_once()
    return _build_perf_filter_for_keys(
        pjt_ids,
        _project_key_candidates("pjt_id"),
        query,
        apply_query_tag_inference=apply_query_tag_inference,
    )


def build_perf_filter_by_pjt_no(
        pjt_nos: List[str],
        query: str = "",
        *,
        apply_query_tag_inference: bool = False,
) -> "qmodels.Filter":
    """pjt_no 런타임 키로 perf hop2 필터를 만든다."""
    _log_project_key_policy_once()
    return _build_perf_filter_for_keys(
        pjt_nos,
        _project_key_candidates("pjt_no"),
        query,
        apply_query_tag_inference=apply_query_tag_inference,
    )


def build_perf_filter_group_resolved(
        pjt_nos: List[str],
        pjt_ids: List[str],
        strategy: str = "prefer_pjt_no",
        query: str = "",
        *,
        apply_query_tag_inference: bool = False,
) -> "qmodels.Filter":
    """group JOIN에서 perf hop2 필터를 pjt_no/pjt_id 전략에 따라 조립한다.
    
    prefer_pjt_no, prefer_pjt_id, or_both 전략을 지원하며 join key 게이트를 약화하지 않는다."""
    if qmodels is None:
        raise RuntimeError("qdrant_client is required for perf filter build")

    _log_project_key_policy_once()
    pjt_no_values = _dedupe_non_empty(pjt_nos)
    pjt_id_values = _dedupe_non_empty(pjt_ids)

    strategy_norm = str(strategy or "prefer_pjt_no").strip().lower()
    if strategy_norm not in {"prefer_pjt_no", "prefer_pjt_id", "or_both"}:
        raise ValueError(f"PERF_GROUP_STRATEGY_INVALID: unsupported perf group strategy={strategy}")

    should: List[Any] = []

    pjt_no_cond = None
    if pjt_no_values:
        pjt_no_should = [
            qmodels.FieldCondition(key=key, match=make_match_any(pjt_no_values))
            for key in _project_key_candidates("pjt_no")
        ]
        pjt_no_cond = _build_filter(
            must=None,
            should=pjt_no_should,
            must_not=None,
            min_should=1,
        )

    pjt_id_cond = None
    if pjt_id_values:
        pjt_id_should = [
            qmodels.FieldCondition(key=key, match=make_match_any(pjt_id_values))
            for key in _project_key_candidates("pjt_id")
        ]
        pjt_id_cond = _build_filter(
            must=None,
            should=pjt_id_should,
            must_not=None,
            min_should=1,
        )

    must: List[Any] = []
    if strategy_norm == "prefer_pjt_no":
        chosen = pjt_no_cond or pjt_id_cond
        if chosen is not None:
            must.append(chosen)
    elif strategy_norm == "prefer_pjt_id":
        chosen = pjt_id_cond or pjt_no_cond
        if chosen is not None:
            must.append(chosen)
    else:
        if pjt_no_cond:
            should.append(pjt_no_cond)
        if pjt_id_cond:
            should.append(pjt_id_cond)
        if should:
            must.append(_build_filter(must=None, should=should, must_not=None, min_should=1))

    # Group hop2 filters must always include resolved join keys before optional perf tags.
    # Query-time perf tags are additive and must not weaken join-key semantics.
    _ = (query, apply_query_tag_inference)

    return qmodels.Filter(must=must, must_not=[])


def build_perf_filter(
        spec: PerfFilterInput,
        *,
        apply_query_tag_inference: bool = False,
) -> "qmodels.Filter":
    """join_ids 모양을 보고 pjt_id 경로와 pjt_no 경로 중 하나로 perf 필터를 분기한다."""
    join_ids = _dedupe_non_empty(spec.join_ids)
    if not join_ids:
        return _build_perf_filter_for_keys(
            [],
            [],
            spec.query,
            apply_query_tag_inference=apply_query_tag_inference,
        )

    has_pjt_id = any(_PJT_ID_VALUE_RE.fullmatch(v or "") for v in join_ids)
    has_pjt_no = any(_PJT_NO_VALUE_RE.fullmatch(v or "") for v in join_ids)
    if has_pjt_id and has_pjt_no:
        raise ValueError("PerfFilterInput.join_ids requires either pjt_id or pjt_no, but not both")

    if has_pjt_id:
        return build_perf_filter_by_pjt_id(
            join_ids,
            spec.query,
            apply_query_tag_inference=apply_query_tag_inference,
        )
    return build_perf_filter_by_pjt_no(
        join_ids,
        spec.query,
        apply_query_tag_inference=apply_query_tag_inference,
    )
