# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .constants import (
    COL_PROJECT,
    COL_PERF,
    TAG_PJT_INFO,
)
from .filters import (
    OrgFilterInput,
    PeopleFilterInput,
    build_org_filter,
    build_people_filter,
    build_prtcp_org_nested_filter,
    build_tag_only_filter,
)
from .query_intent import QueryIntent, classify_query as _classify_query


def classify_query_compat(
    q: str,
    kws: List[str],
    *,
    domain_hint: Optional[str],
    hint: Optional[Dict[str, Any]] = None,
) -> QueryIntent:
    """query_intent.classify_query signature 호환 래퍼."""
    sig = inspect.signature(_classify_query)
    if hint is not None and "hint" in sig.parameters:
        return _classify_query(q, kws, domain_hint=domain_hint, hint=hint)
    return _classify_query(q, kws, domain_hint=domain_hint)


def _normalize_terms(values: Optional[List[Any]]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for v in values or []:
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _normalize_ids_map(raw: Any) -> Dict[str, List[str]]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for key, values in raw.items():
        if values is None:
            continue
        if not isinstance(values, list):
            values = [values]
        norm = _normalize_terms(values)
        if norm:
            out[str(key)] = norm
    return out


def _flatten_ids(ids_map: Dict[str, List[str]]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for _, values in ids_map.items():
        for v in values or []:
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
    return out


@dataclass
class NormalizedIntent:
    action: str
    base_route: str
    relation: Optional[Tuple[str, str]]
    is_id_query: bool
    years: List[str] = field(default_factory=list)
    people_terms: List[str] = field(default_factory=list)
    gender_terms: List[str] = field(default_factory=list)
    org_terms: List[str] = field(default_factory=list)
    org_role: Optional[str] = None
    perf_tag_filters: List[str] = field(default_factory=list)
    project_tag_filters: List[str] = field(default_factory=list)
    tag_filters: List[str] = field(default_factory=list)
    ids_map: Dict[str, List[str]] = field(default_factory=dict)
    ids_flat: List[str] = field(default_factory=list)
    remove_terms_for_head: List[str] = field(default_factory=list)


@dataclass
class FilterBundle:
    org_terms: List[str]
    org_role: Optional[str]
    people_terms: List[str]
    people_ids: List[str]
    gender_terms: List[str]
    people_org_terms: List[str]
    org_filter: Any
    participant_org_filter: Any
    people_filter: Any
    perf_tag_filter: Any


@dataclass
class JoinHopPlan:
    hop1_col: str
    hop2_col: str
    hop1_kind: str
    hop2_kind: str
    hop1_tag_filters: Optional[List[str]]
    hop2_tag_filters: Optional[List[str]]
    hop2_label: str


def normalize_intent(
    intent: QueryIntent,
    *,
    query: str,
    keywords: List[str],
    hint_people_terms: Optional[List[str]] = None,
    hint_org_terms: Optional[List[str]] = None,
    hint_org_role: Optional[str] = None,
) -> NormalizedIntent:
    ids_map = _normalize_ids_map(getattr(intent, "ids_map", None) or getattr(intent, "ids", None) or {})
    ids_flat = _normalize_terms(getattr(intent, "ids_flat", None) or [])
    if not ids_flat:
        ids_flat = _flatten_ids(ids_map)

    org_terms = _normalize_terms(getattr(intent, "org_terms", None) or [])
    if hint_org_terms:
        org_terms = _normalize_terms(list(hint_org_terms))

    people_terms = _normalize_terms(getattr(intent, "people_terms", None) or [])

    if hint_people_terms:
        # ✅ hint가 있으면 hint만 사용(override)
        people_terms = _normalize_terms(list(hint_people_terms))

    gender_terms = _normalize_terms(getattr(intent, "gender_terms", None) or [])

    org_role = (hint_org_role or getattr(intent, "org_role", None) or "").strip().lower() or None

    return NormalizedIntent(
        action=str(getattr(intent, "action", "") or "").strip().lower(),
        base_route=str(getattr(intent, "base_route", "") or "").strip().lower(),
        relation=getattr(intent, "relation", None),
        is_id_query=bool(getattr(intent, "is_id_query", False)),
        years=_normalize_terms(getattr(intent, "years", None) or []),
        people_terms=people_terms,
        gender_terms=gender_terms,
        org_terms=org_terms,
        org_role=org_role,
        perf_tag_filters=_normalize_terms(getattr(intent, "perf_tag_filters", None) or []),
        project_tag_filters=_normalize_terms(getattr(intent, "project_tag_filters", None) or []),
        tag_filters=_normalize_terms(getattr(intent, "tag_filters", None) or []),
        ids_map=ids_map,
        ids_flat=ids_flat,
        remove_terms_for_head=_normalize_terms(getattr(intent, "remove_terms_for_head", None) or []),
    )


def build_filter_bundle(intent: NormalizedIntent) -> FilterBundle:
    org_terms = list(intent.org_terms)
    org_role = intent.org_role
    people_terms = list(intent.people_terms)
    gender_terms = list(intent.gender_terms)
    people_ids = list(intent.ids_map.get("person_no") or [])

    people_org_terms: List[str] = []
    if org_role == "affiliation" and org_terms:
        people_org_terms = list(org_terms)

    org_filter = build_org_filter(OrgFilterInput(org_terms)) if org_terms else None
    participant_org_filter = build_prtcp_org_nested_filter(OrgFilterInput(org_terms)) if org_terms else None

    people_filter = (
        build_people_filter(
            PeopleFilterInput(
                people_terms=people_terms,
                person_ids=people_ids,
                gender_terms=gender_terms,
                org_terms=people_org_terms,
            )
        )
        if (people_terms or people_ids or gender_terms or people_org_terms)
        else None
    )

    perf_tag_filter = build_tag_only_filter(intent.perf_tag_filters) if intent.perf_tag_filters else None

    return FilterBundle(
        org_terms=org_terms,
        org_role=org_role,
        people_terms=people_terms,
        people_ids=people_ids,
        gender_terms=gender_terms,
        people_org_terms=people_org_terms,
        org_filter=org_filter,
        participant_org_filter=participant_org_filter,
        people_filter=people_filter,
        perf_tag_filter=perf_tag_filter,
    )


def resolve_join_hops(relation: Optional[Tuple[str, str]]) -> Optional[JoinHopPlan]:
    if not relation:
        return None

    if relation == ("project", "perf"):
        return JoinHopPlan(
            hop1_col=COL_PROJECT,
            hop2_col=COL_PERF,
            hop1_kind="project",
            hop2_kind="perf",
            hop1_tag_filters=[TAG_PJT_INFO],
            hop2_tag_filters=None,
            hop2_label="성과(논문/특허/보고서 등) 목록",
        )
    if relation == ("project", "people"):
        return JoinHopPlan(
            hop1_col=COL_PROJECT,
            hop2_col=COL_PROJECT,
            hop1_kind="project",
            hop2_kind="people",
            hop1_tag_filters=[TAG_PJT_INFO],
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="참여인력 목록",
        )
    if relation == ("project", "org"):
        return JoinHopPlan(
            hop1_col=COL_PROJECT,
            hop2_col=COL_PROJECT,
            hop1_kind="project",
            hop2_kind="org",
            hop1_tag_filters=[TAG_PJT_INFO],
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="참여기관 목록",
        )
    if relation == ("people", "perf"):
        return JoinHopPlan(
            hop1_col=COL_PROJECT,
            hop2_col=COL_PERF,
            hop1_kind="people",
            hop2_kind="perf",
            hop1_tag_filters=[TAG_PJT_INFO],
            hop2_tag_filters=[],
            hop2_label="연관 성과(논문/특허/보고서 등) 목록",
        )
    if relation == ("org", "perf"):
        return JoinHopPlan(
            hop1_col=COL_PROJECT,
            hop2_col=COL_PERF,
            hop1_kind="org",
            hop2_kind="perf",
            hop1_tag_filters=[TAG_PJT_INFO],
            hop2_tag_filters=[],
            hop2_label="연관 성과(논문/특허/보고서 등) 목록",
        )
    if relation == ("perf", "project"):
        return JoinHopPlan(
            hop1_col=COL_PERF,
            hop2_col=COL_PROJECT,
            hop1_kind="perf",
            hop2_kind="project",
            hop1_tag_filters=None,
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="연관 과제(프로젝트) 정보",
        )
    if relation == ("perf", "people"):
        return JoinHopPlan(
            hop1_col=COL_PERF,
            hop2_col=COL_PROJECT,
            hop1_kind="perf",
            hop2_kind="people",
            hop1_tag_filters=None,
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="연관 과제의 참여인력 목록",
        )
    if relation == ("perf", "org"):
        return JoinHopPlan(
            hop1_col=COL_PERF,
            hop2_col=COL_PROJECT,
            hop1_kind="perf",
            hop2_kind="org",
            hop1_tag_filters=None,
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="연관 과제의 참여기관 목록",
        )
    if relation == ("people", "project"):
        return JoinHopPlan(
            hop1_col=COL_PROJECT,
            hop2_col=COL_PROJECT,
            hop1_kind="people",
            hop2_kind="project",
            hop1_tag_filters=[TAG_PJT_INFO],
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="참여 과제(프로젝트) 목록",
        )
    if relation == ("org", "project"):
        return JoinHopPlan(
            hop1_col=COL_PROJECT,
            hop2_col=COL_PROJECT,
            hop1_kind="org",
            hop2_kind="project",
            hop1_tag_filters=[TAG_PJT_INFO],
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="참여 과제(프로젝트) 목록",
        )
    return None
