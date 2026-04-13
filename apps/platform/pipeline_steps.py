# -*- coding: utf-8 -*-
from __future__ import annotations
import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple
from apps.platform.rag_constants import (
    COL_PROJECT,
    COL_PERF,
    TAG_PJT_INFO,
    normalize_perf_types,
)
from apps.planner.planner_contract import normalize_stats_policy_value
from apps.planner.query_intent import (
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
    """두 스냅샷 사이에서 달라진 필드만 추출해 이력 dict로 만든다.
    planner merge나 runtime patch가 어떤 필드를 바꿈는지 바로 보여주는 로그 보조 헬퍼다.
    """
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
    """classify_query 시그니처 차이를 흡수하는 호환 레이어다.
    hint 인자를 받는 버전과 아닌 버전을 동적으로 구분해 호출자가 query classifier 시그니처 변화에 흔들리지 않게 한다.
    """
    sig = inspect.signature(_classify_query)
    if hint is not None and "hint" in sig.parameters:
        return _classify_query(q, kws, domain_hint=domain_hint, hint=hint)
    return _classify_query(q, kws, domain_hint=domain_hint)
def _normalize_terms(values: Any) -> List[str]:
    """scalar/list/set으로 들어온 후보를 중복 없는 문자열 리스트로 정리한다.
    intent normalization 전처리에서 필드별 shape를 일치시키기 위한 중앙 헬퍼다.
    """
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
    """ids_map 입력을 키별 문자열 리스트 형태로 정리한다.
    lookup/join 시드를 어떤 레이어에서 주드더라도 동일한 contract로 부호화하기 위한 보조 정규화다.
    """
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
    """ids_map의 값들을 중복 제거한 평탄 id 목록으로 만든다.
    id query 판단이나 단순 prompt summary에서 키 구분 없이 전체 id를 보고 싶을 때 쓴다.
    """
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
    """query_intent 결과를 planner/runtime 공용 형식으로 정규화한 중간 구조체다.
    action·route·relation·ids·filter hint·stats policy를 하나로 묶어 후속 planner contract와 retrieval runtime이 같은 truth를 읽게 한다.
    """
    action: str
    base_route: str
    relation: Optional[Tuple[str, str]]
    is_id_query: bool
    mode: Optional[str] = None
    output_type: Optional[str] = None
    reverse_trace_followup: bool = False
    followup_relation_hint: Optional[str] = None
    pattern_kind: Optional[str] = None
    bundle_kind: Optional[str] = None
    bundle_targets: List[str] = field(default_factory=list)
    guidance_required: bool = False
    join_key_mode: Optional[Literal["instance", "group", "deferred"]] = None
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
    candidate_keys: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    project_key_policy: Optional[str] = None
    join_resolution_policy: Optional[str] = None
    ids_flat: List[str] = field(default_factory=list)
    has_project_candidate_key: bool = False
    has_perf_candidate_key: bool = False
    is_exact_key_query: bool = False
    remove_terms_for_head: List[str] = field(default_factory=list)
    people_terms_match_mode: Optional[str] = None
    people_terms_min_should: Optional[int] = None
    lookup_filter_policy: Optional[str] = None
    lookup_filter_policy_hint: Optional[str] = None
    target_cols: List[str] = field(default_factory=list)
    context_owner_lock: Optional[str] = None
    context_owner_lock_reason: Optional[str] = None
    wants_rank: bool = False
    min_metric_count: Optional[int] = None
    stats_metric: str = "project_participation_count"
    window_years: int = 3
    candidate_n: int = 50
    top_k: int = 1
    tie_break: str = "performance_count_desc_name_asc"
@dataclass(frozen=True)
class FilterBundle:
    """runtime이 조립한 people/org/tag filter 묶음을 담는 구조체다.
    filter object와 원본 term list를 함께 보존해 디버그·로그·후속 재조립에서 같은 입력을 재사용한다.
    """
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
    """JOIN 질의에서 hop1/hop2 컬렉션과 tag filter 분담을 설명하는 계획이다.
    relation 방향에 따라 어떤 컬렉션을 먼저 치고 어떤 태그를 각 hop에 걸지 고정한다.
    """
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
        hint_lead_org_terms: Optional[List[str]] = None,
        hint_participant_org_terms: Optional[List[str]] = None,
        hint_people_affiliation_org_terms: Optional[List[str]] = None,
        hint_years: Optional[List[str]] = None,
        hint_perf_types: Optional[List[str]] = None,
        hint_title_terms: Optional[List[str]] = None,
) -> NormalizedIntent:
    """query classifier 결과와 explicit hint를 합쳐 NormalizedIntent로 정규화한다.
    action/base_route/relation, people/org/year/title/tag/id signal, stats policy를 함께 정리해 planner와 runtime이 공유할 진입 truth를 만든다.
    """
    ids_map = _normalize_ids_map(getattr(intent, "ids_map", None) or getattr(intent, "ids", None) or {})
    ids_flat = _normalize_terms(getattr(intent, "ids_flat", None) or [])
    if not ids_flat:
        ids_flat = _flatten_ids(ids_map)
    org_terms = normalize_org_terms(_normalize_terms(getattr(intent, "org_terms", None) or []))
    if hint_org_terms:
        org_terms = normalize_org_terms(list(hint_org_terms))
    people_terms = _normalize_terms(getattr(intent, "people_terms", None) or [])
    if hint_people_terms:
        # Use explicit hints as an override only when a hint is actually present.
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
    if action == "relation":
        action = "list"
    relation = getattr(intent, "relation", None)
    wants_count = bool(getattr(intent, "wants_count", False))
    wants_list = bool(getattr(intent, "wants_list", False))
    wants_detail = bool(getattr(intent, "wants_detail", False))
    wants_rank = bool(getattr(intent, "wants_rank", False))
    output_type = str(getattr(intent, "output_type", "") or "").strip().lower() or None
    if output_type == "rank":
        output_type = "stats"
    if output_type not in ("stats", "list", "detail", "relation", "summary", "comparison", "series"):
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
    }
    normalization_warnings: List[str] = []
    if base_route not in valid_routes:
        normalization_warnings.append(f"invalid_base_route:{base_route or 'empty'}")
    if action not in valid_actions:
        normalization_warnings.append(f"invalid_action:{action or 'empty'}")
    perf_types_raw = _normalize_terms(hint_perf_types or getattr(intent, "perf_types", None) or [])
    perf_type_norm = normalize_perf_types(perf_types_raw)
    perf_types = perf_type_norm["tags"] or perf_type_norm["unknown"]
    years = _normalize_terms(hint_years or getattr(intent, "years", None) or [])
    title_terms = _normalize_terms(hint_title_terms or getattr(intent, "title", None) or [])
    normalized_join_key_mode = normalize_join_key_mode(getattr(intent, "join_key_mode", None))
    candidate_keys = getattr(intent, "candidate_keys", None) or {}
    join_parsing_warnings = _normalize_terms(
        list(getattr(intent, "parsing_warnings", None) or []) + normalization_warnings
    )
    join_contract_violations = _normalize_terms(list(getattr(intent, "contract_violations", None) or []))
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
        is_id_query=bool(getattr(intent, "is_id_query", False) or getattr(intent, "is_exact_key_query", False)),
        output_type=output_type,
        categories=normalize_categories(getattr(intent, "categories", None)),
        planner_limit=getattr(intent, "planner_limit", None),
        retrieval_query=(str(getattr(intent, "retrieval_query", "") or "").strip() or None),
        planner_confidence=getattr(intent, "planner_confidence", None),
        years=years,
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
        title=title_terms,
        keywords=_normalize_terms(getattr(intent, "keywords", None) or keywords or []),
        perf_tag_filters=_normalize_terms(getattr(intent, "perf_tag_filters", None) or []),
        project_tag_filters=_normalize_terms(getattr(intent, "project_tag_filters", None) or []),
        tag_filters=_normalize_terms(getattr(intent, "tag_filters", None) or []),
        ids_map=ids_map,
        candidate_keys=dict(candidate_keys),
        project_key_policy=(str(getattr(intent, "project_key_policy", "") or "").strip().lower() or None),
        join_resolution_policy=(str(getattr(intent, "join_resolution_policy", "") or "").strip().lower() or None),
        ids_flat=ids_flat,
        has_project_candidate_key=bool(getattr(intent, "has_project_candidate_key", False)),
        has_perf_candidate_key=bool(getattr(intent, "has_perf_candidate_key", False)),
        is_exact_key_query=bool(getattr(intent, "is_exact_key_query", False) or ids_flat or (candidate_keys.get("project_key") or []) or (candidate_keys.get("perf_key") or [])),
        remove_terms_for_head=_normalize_terms(getattr(intent, "remove_terms_for_head", None) or []),
        people_terms_match_mode=str(getattr(intent, "people_terms_match_mode", "") or "").strip().lower() or None,
        people_terms_min_should=getattr(intent, "people_terms_min_should", None),
        lookup_filter_policy=str(getattr(intent, "lookup_filter_policy", "") or "").strip().lower() or None,
        lookup_filter_policy_hint=str(getattr(intent, "lookup_filter_policy", "") or "").strip().lower() or None,
        target_cols=_normalize_terms(getattr(intent, "target_cols", None) or []),
        context_owner_lock=(str(getattr(intent, "context_owner_lock", "") or "").strip().lower() or None),
        context_owner_lock_reason=(str(getattr(intent, "context_owner_lock_reason", "") or "").strip().lower() or None),
        wants_rank=wants_rank,
        reverse_trace_followup=bool(getattr(intent, "reverse_trace_followup", False)),
        followup_relation_hint=(str(getattr(intent, "followup_relation_hint", "") or "").strip().lower() or None),
        pattern_kind=(str(getattr(intent, "pattern_kind", "") or "").strip().lower() or None),
        bundle_kind=(str(getattr(intent, "bundle_kind", "") or "").strip().lower() or None),
        bundle_targets=_normalize_terms(getattr(intent, "bundle_targets", None) or []),
        guidance_required=bool(getattr(intent, "guidance_required", False)),
        min_metric_count=getattr(intent, "min_metric_count", None),
        stats_metric=stats_policy["stats_metric"],
        window_years=stats_policy["window_years"],
        candidate_n=stats_policy["candidate_n"],
        top_k=stats_policy["top_k"],
        tie_break=stats_policy["tie_break"],
    )
def resolve_join_hops(relation: Optional[Tuple[str, str]]) -> Optional[JoinHopPlan]:
    """relation 방향과 태그 필터 상태를 바탕으로 JOIN hop 계획을 계산한다.
    project->perf와 perf->project가 다른 태그 장착 전략을 가지므로, hop별 collection·kind·label을 중앙화한다.
    """
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
            hop2_label="연관 성과",
        )
    if relation == ("project", "people"):
        return JoinHopPlan(
            hop1_col=COL_PROJECT,
            hop2_col=COL_PROJECT,
            hop1_kind="project",
            hop2_kind="people",
            hop1_tag_filters=[TAG_PJT_INFO],
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="연관 연구자",
        )
    if relation == ("project", "org"):
        return JoinHopPlan(
            hop1_col=COL_PROJECT,
            hop2_col=COL_PROJECT,
            hop1_kind="project",
            hop2_kind="org",
            hop1_tag_filters=[TAG_PJT_INFO],
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="연관 기관",
        )
    if relation == ("perf", "project"):
        return JoinHopPlan(
            hop1_col=COL_PERF,
            hop2_col=COL_PROJECT,
            hop1_kind="perf",
            hop2_kind="project",
            hop1_tag_filters=None,
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="연관 과제",
        )
    return None
