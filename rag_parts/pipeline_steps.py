# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from .constants import (
    COL_PROJECT,
    COL_PERF,
    TAG_PJT_INFO,
    normalize_perf_types,
)
from .planner_contract import normalize_stats_policy_value
from .query_intent import (
    QueryIntent,
    classify_query as _classify_query,
    normalize_categories,
    normalize_join_key_mode,
    normalize_org_terms,
)


def build_changed_fields(
        before: Dict[str, Any],
        after: Dict[str, Any],
        fields: Tuple[str, ...],
        *,
        changed_by: str,
) -> Dict[str, Dict[str, Any]]:
    """지정 필드 diff를 `changed_by` 메타와 함께 구성한다."""
    changed: Dict[str, Dict[str, Any]] = {}
    for field in fields:
        before_val = before.get(field)
        after_val = after.get(field)
        if before_val == after_val:
            continue
        changed[field] = {
            "before": before_val,
            "after": after_val,
            "changed_by": changed_by,
        }
    return changed


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


def _normalize_terms(values: Any) -> List[str]:
    if values is None:
        iterable: List[Any] = []
    elif isinstance(values, str):
        iterable = [values]
    elif isinstance(values, (list, tuple, set)):
        iterable = list(values)
    else:
        iterable = [values]

    out: List[str] = []
    seen: set[str] = set()
    for v in iterable:
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


@dataclass(frozen=True)
class NormalizedIntent:
    action: str
    base_route: str
    relation: Optional[Tuple[str, str]]
    is_id_query: bool
    mode: Optional[str] = None
    output_type: Optional[str] = None
    join_key_mode: Optional[Literal["instance", "group"]] = None
    parsing_warnings: List[str] = field(default_factory=list)
    contract_violations: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    planner_limit: Optional[int] = None
    retrieval_query: Optional[str] = None
    planner_confidence: Optional[float] = None
    years: List[str] = field(default_factory=list)
    year_from: Optional[str] = None
    year_to: Optional[str] = None
    people_terms: List[str] = field(default_factory=list)
    gender_terms: List[str] = field(default_factory=list)
    org_terms: List[str] = field(default_factory=list)
    org_role: Optional[str] = None
    lead_org_terms: List[str] = field(default_factory=list)
    participant_org_terms: List[str] = field(default_factory=list)
    people_affiliation_org_terms: List[str] = field(default_factory=list)
    perf_types: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    title: List[str] = field(default_factory=list)
    perf_tag_filters: List[str] = field(default_factory=list)
    project_tag_filters: List[str] = field(default_factory=list)
    tag_filters: List[str] = field(default_factory=list)
    ids_map: Dict[str, List[str]] = field(default_factory=dict)
    ids_flat: List[str] = field(default_factory=list)
    remove_terms_for_head: List[str] = field(default_factory=list)
    people_terms_match_mode: Optional[str] = None
    people_terms_min_should: Optional[int] = None
    lookup_filter_policy: Optional[str] = None
    lookup_filter_policy_hint: Optional[str] = None
    target_cols: List[str] = field(default_factory=list)
    wants_rank: bool = False
    stats_metric: str = "project_participation_count"
    window_years: int = 3
    candidate_n: int = 50
    top_k: int = 1
    tie_break: str = "performance_count_desc_name_asc"

@dataclass(frozen=True)
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


@dataclass(frozen=True)
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
        allow_strategy_fallback: bool = False,
        hint_people_terms: Optional[List[str]] = None,
        hint_org_terms: Optional[List[str]] = None,
        hint_org_role: Optional[str] = None,
        hint_lead_org_terms: Optional[List[str]] = None,
        hint_participant_org_terms: Optional[List[str]] = None,
        hint_people_affiliation_org_terms: Optional[List[str]] = None,
) -> NormalizedIntent:
    # allow_strategy_fallback 운영 기본값을 False로 두는 이유:
    # - planner가 이미 mode/action/relation 계약을 만든 뒤에 재분류를 허용하면
    #   실행 전략이 조용히 바뀌어 관측 가능한 원인-결과 연결이 끊긴다.
    # - 즉, 계약 위반은 fail-close/명시 오류로 드러내고, 자동 우회는 제한적으로만 사용한다.
    ids_map = _normalize_ids_map(getattr(intent, "ids_map", None) or getattr(intent, "ids", None) or {})
    ids_flat = _normalize_terms(getattr(intent, "ids_flat", None) or [])
    if not ids_flat:
        ids_flat = _flatten_ids(ids_map)

    org_terms = normalize_org_terms(_normalize_terms(getattr(intent, "org_terms", None) or []))
    if hint_org_terms:
        org_terms = normalize_org_terms(list(hint_org_terms))

    people_terms = _normalize_terms(getattr(intent, "people_terms", None) or [])

    if hint_people_terms:
        # ✅ hint가 있으면 hint만 사용(override)
        people_terms = _normalize_terms(list(hint_people_terms))

    gender_terms = _normalize_terms(getattr(intent, "gender_terms", None) or [])

    org_role = (hint_org_role or getattr(intent, "org_role", None) or "").strip().lower() or None

    lead_org_terms = normalize_org_terms(_normalize_terms(hint_lead_org_terms or getattr(intent, "lead_org_terms", None) or []))
    participant_org_terms = normalize_org_terms(_normalize_terms(
        hint_participant_org_terms or getattr(intent, "participant_org_terms", None) or []
    ))
    people_affiliation_org_terms = normalize_org_terms(_normalize_terms(
        hint_people_affiliation_org_terms or getattr(intent, "people_affiliation_org_terms", None) or []
    ))

    if not org_terms and (lead_org_terms or participant_org_terms or people_affiliation_org_terms):
        org_terms = normalize_org_terms([*lead_org_terms, *participant_org_terms, *people_affiliation_org_terms])
    if org_role in ("lead", "performer", "performing") and not lead_org_terms and org_terms:
        lead_org_terms = list(org_terms)
    if org_role == "participant" and not participant_org_terms and org_terms:
        participant_org_terms = list(org_terms)
    if org_role == "affiliation" and not people_affiliation_org_terms and org_terms:
        people_affiliation_org_terms = list(org_terms)

    base_route = str(getattr(intent, "base_route", "") or "").strip().lower()
    action = str(getattr(intent, "action", "") or "").strip().lower()
    if action == "rank":
        action = "stats"
    relation = getattr(intent, "relation", None)
    wants_count = bool(getattr(intent, "wants_count", False))
    wants_list = bool(getattr(intent, "wants_list", False))
    wants_detail = bool(getattr(intent, "wants_detail", False))
    wants_rank = bool(getattr(intent, "wants_rank", False))
    output_type = str(getattr(intent, "output_type", "") or "").strip().lower() or None
    if output_type == "rank":
        output_type = "stats"
    if output_type not in ("stats", "list", "detail", "relation", "summary"):
        if wants_count or wants_rank:
            output_type = "stats"
        elif wants_list:
            output_type = "list"
        elif wants_detail:
            output_type = "detail"
        elif relation is not None:
            output_type = "relation"
        else:
            output_type = "summary"
    valid_routes = {"support", "project", "perf", "people", "org"}
    valid_actions = {
        "support",
        "id_exact",
        "id_fuzzy",
        "list",
        "stats",
        "topic",
        "detail",
        "content",
        "relation",
    }
    # 입력값이 유효 route/action 집합을 벗어났을 때만 제한적으로 fallback 허용.
    # (운영 기본은 False: 계약 기반 실행 일관성 우선)
    if allow_strategy_fallback and (base_route not in valid_routes or action not in valid_actions):
        fallback = classify_query_compat(query, keywords, domain_hint=base_route or None, hint=None)
        if base_route not in valid_routes:
            base_route = fallback.base_route
        if action not in valid_actions:
            action = fallback.action
        if relation is None:
            relation = fallback.relation

    perf_types_raw = _normalize_terms(getattr(intent, "perf_types", None) or [])
    perf_type_norm = normalize_perf_types(perf_types_raw)
    perf_types = perf_type_norm["tags"] or perf_type_norm["unknown"]

    normalized_join_key_mode, join_parsing_warnings, join_contract_violations = normalize_join_key_mode(
        getattr(intent, "join_key_mode", None),
        ids_map,
    )
    join_parsing_warnings = _normalize_terms(
        list(getattr(intent, "parsing_warnings", None) or []) + join_parsing_warnings
    )
    join_contract_violations = _normalize_terms(
        list(getattr(intent, "contract_violations", None) or []) + join_contract_violations
    )

    stats_policy = normalize_stats_policy_value(
        stats_metric=getattr(intent, "stats_metric", None),
        window_years=getattr(intent, "window_years", None),
        candidate_n=getattr(intent, "candidate_n", None),
        top_k=getattr(intent, "top_k", None),
        tie_break=getattr(intent, "tie_break", None),
    )

    return NormalizedIntent(
        mode=str(getattr(intent, "mode", "") or "").strip().lower() or None,
        action=action,
        base_route=base_route,
        relation=relation,
        join_key_mode=normalized_join_key_mode,
        parsing_warnings=join_parsing_warnings,
        contract_violations=join_contract_violations,
        is_id_query=bool(getattr(intent, "is_id_query", False)),
        output_type=output_type,
        categories=normalize_categories(getattr(intent, "categories", None)),
        planner_limit=getattr(intent, "planner_limit", None),
        retrieval_query=(str(getattr(intent, "retrieval_query", "") or "").strip() or None),
        planner_confidence=getattr(intent, "planner_confidence", None),
        years=_normalize_terms(getattr(intent, "years", None) or []),
        year_from=(str(getattr(intent, "year_from", "") or "").strip() or None),
        year_to=(str(getattr(intent, "year_to", "") or "").strip() or None),
        people_terms=people_terms,
        gender_terms=gender_terms,
        org_terms=org_terms,
        org_role=org_role,
        lead_org_terms=lead_org_terms,
        participant_org_terms=participant_org_terms,
        people_affiliation_org_terms=people_affiliation_org_terms,
        perf_types=perf_types,
        keywords=_normalize_terms(getattr(intent, "keywords", None) or keywords or []),
        perf_tag_filters=_normalize_terms(getattr(intent, "perf_tag_filters", None) or []),
        project_tag_filters=_normalize_terms(getattr(intent, "project_tag_filters", None) or []),
        tag_filters=_normalize_terms(getattr(intent, "tag_filters", None) or []),
        ids_map=ids_map,
        ids_flat=ids_flat,
        remove_terms_for_head=_normalize_terms(getattr(intent, "remove_terms_for_head", None) or []),
        people_terms_match_mode=str(getattr(intent, "people_terms_match_mode", "") or "").strip().lower() or None,
        people_terms_min_should=getattr(intent, "people_terms_min_should", None),
        lookup_filter_policy=str(getattr(intent, "lookup_filter_policy", "") or "").strip().lower() or None,
        lookup_filter_policy_hint=str(getattr(intent, "lookup_filter_policy", "") or "").strip().lower() or None,
        target_cols=_normalize_terms(getattr(intent, "target_cols", None) or []),
        wants_rank=wants_rank,
        stats_metric=stats_policy["stats_metric"],
        window_years=stats_policy["window_years"],
        candidate_n=stats_policy["candidate_n"],
        top_k=stats_policy["top_k"],
        tie_break=stats_policy["tie_break"],
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
    return None
