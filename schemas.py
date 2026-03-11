# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from rag_parts.pipeline_steps import NormalizedIntent
from rag_parts.planner_contract import normalize_stats_policy_value


@dataclass(frozen=True)
class IntentPayloadV2:
    """RAG intent_payload.v2 계약: normalized_intent 단일 필드."""

    normalized_intent: NormalizedIntent


Stage1Relation = Literal["project_perf", "perf_project"]


class PlannerStage1Decision(BaseModel):
    action: Literal["topic", "list", "detail", "stats", "download"]
    head: Literal["project", "perf", "people", "org", "support"]
    relation_candidate: Optional[Stage1Relation] = None
    referential_followup: bool = False
    confidence: float = Field(ge=0.0, le=1.0)


class PlannerStage2Slots(BaseModel):
    ids_map: Dict[str, list[str]] = Field(default_factory=dict)
    filters: Dict[str, Any] = Field(default_factory=dict)
    retrieval_query: Optional[str] = None
    limit: int = Field(default=20, ge=1)
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class StrategySpec:
    mode: str
    action: str
    relation: Optional[Tuple[str, str]]
    # planner 출력/정규화 단계에서 값이 흔들릴 수 있으므로 Optional[str]로 완화
    join_key_mode: Optional[str] = None
    join_key_source: Optional[str] = None
    hop1_mode: Optional[str] = None
    hop2_key_strategy: Optional[str] = None
    join_keys_used_count: Optional[int] = None
    people_terms: Tuple[str, ...] = field(default_factory=tuple)
    target_collections: Tuple[str, ...] = field(default_factory=tuple)
    search_filter_enabled: bool = False
    lookup_filter_enabled: bool = False
    relation_lookup_enforce: bool = False
    lookup_filter_policy: Optional[str] = None
    lookup_filter_min_should: Optional[int] = None
    lookup_filter_gate: Optional[str] = None
    lookup_filter_promote_one_must: bool = False
    lookup_title_filter_policy: Optional[str] = None
    title_match_mode: Optional[str] = None
    search_filter_server_policy: Optional[str] = None


@dataclass(frozen=True)
class QueryPlan:
    mode: str  # "search" | "lookup" | "join"
    base_route: str
    action: str
    relation: Optional[Tuple[str, str]]
    join_key_mode: Optional[str]
    output_type: Optional[str]
    stats_metric: str
    window_years: int
    candidate_n: int
    top_k: int
    tie_break: str
    target_collections: Tuple[str, ...]
    # server-side filters by collection (optional)
    filters: Dict[str, Any]


@dataclass
class ExecutionContext:
    intent: Any
    base_route: str
    action: str
    mode: Optional[str]
    relation: Optional[Tuple[str, str]]
    join_key_mode: Optional[str]
    is_id_query: bool
    output_type: Optional[str]
    categories: list[str]
    planner_limit: Optional[int]
    retrieval_query: Optional[str]
    planner_confidence: Optional[float]
    years: list[str]
    year_from: Optional[str]
    year_to: Optional[str]
    people_terms: list[str]
    gender_terms: list[str]
    org_terms: list[str]
    org_role: Optional[str]
    lead_org_terms: list[str]
    participant_org_terms: list[str]
    people_affiliation_org_terms: list[str]
    perf_types: list[str]
    keywords: list[str]
    title: list[str]
    perf_tag_filters: list[str]
    project_tag_filters: list[str]
    tag_filters: list[str]
    ids_map: Dict[str, list[str]]
    ids_flat: list[str]
    remove_terms_for_head: list[str]
    people_terms_match_mode: Optional[str] = None
    people_terms_min_should: Optional[int] = None
    lookup_filter_policy_hint: Optional[str] = None
    stats_metric: Optional[str] = None
    window_years: Optional[int] = None
    candidate_n: Optional[int] = None
    top_k: Optional[int] = None
    tie_break: Optional[str] = None
    plan: Optional[QueryPlan] = None
    strategy: Optional[StrategySpec] = None
    target_collections: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        policy = normalize_stats_policy_value(
            stats_metric=self.stats_metric,
            window_years=self.window_years,
            candidate_n=self.candidate_n,
            top_k=self.top_k,
            tie_break=self.tie_break,
        )
        self.stats_metric = policy["stats_metric"]
        self.window_years = policy["window_years"]
        self.candidate_n = policy["candidate_n"]
        self.top_k = policy["top_k"]
        self.tie_break = policy["tie_break"]

    @classmethod
    def from_intent(cls, intent: Any) -> "ExecutionContext":
        return cls(
            intent=intent,
            base_route=intent.base_route,
            action=intent.action,
            mode=getattr(intent, "mode", None),
            relation=intent.relation,
            join_key_mode=getattr(intent, "join_key_mode", None),
            is_id_query=intent.is_id_query,
            output_type=intent.output_type,
            categories=list(intent.categories),
            planner_limit=intent.planner_limit,
            retrieval_query=intent.retrieval_query,
            planner_confidence=intent.planner_confidence,
            years=list(intent.years),
            year_from=intent.year_from,
            year_to=intent.year_to,
            people_terms=list(intent.people_terms),
            gender_terms=list(intent.gender_terms),
            org_terms=list(intent.org_terms),
            org_role=intent.org_role,
            lead_org_terms=list(getattr(intent, "lead_org_terms", []) or []),
            participant_org_terms=list(getattr(intent, "participant_org_terms", []) or []),
            people_affiliation_org_terms=list(getattr(intent, "people_affiliation_org_terms", []) or []),
            perf_types=list(intent.perf_types),
            keywords=list(intent.keywords),
            title=list(intent.title),
            perf_tag_filters=list(intent.perf_tag_filters),
            project_tag_filters=list(intent.project_tag_filters),
            tag_filters=list(intent.tag_filters),
            ids_map=dict(intent.ids_map),
            ids_flat=list(intent.ids_flat),
            remove_terms_for_head=list(intent.remove_terms_for_head),
            people_terms_match_mode=getattr(intent, "people_terms_match_mode", None),
            people_terms_min_should=getattr(intent, "people_terms_min_should", None),
            lookup_filter_policy_hint=getattr(intent, "lookup_filter_policy", None),
            stats_metric=getattr(intent, "stats_metric", None),
            window_years=getattr(intent, "window_years", None),
            candidate_n=getattr(intent, "candidate_n", None),
            top_k=getattr(intent, "top_k", None),
            tie_break=getattr(intent, "tie_break", None),
        )

    def intent_view(self) -> Any:
        from dataclasses import replace

        return replace(
            self.intent,
            base_route=self.base_route,
            action=self.action,
            mode=self.mode,
            relation=self.relation,
            join_key_mode=self.join_key_mode,
            is_id_query=self.is_id_query,
            output_type=self.output_type,
            categories=list(self.categories),
            planner_limit=self.planner_limit,
            retrieval_query=self.retrieval_query,
            planner_confidence=self.planner_confidence,
            years=list(self.years),
            year_from=self.year_from,
            year_to=self.year_to,
            people_terms=list(self.people_terms),
            gender_terms=list(self.gender_terms),
            org_terms=list(self.org_terms),
            org_role=self.org_role,
            lead_org_terms=list(self.lead_org_terms),
            participant_org_terms=list(self.participant_org_terms),
            people_affiliation_org_terms=list(self.people_affiliation_org_terms),
            perf_types=list(self.perf_types),
            keywords=list(self.keywords),
            title=list(self.title),
            perf_tag_filters=list(self.perf_tag_filters),
            project_tag_filters=list(self.project_tag_filters),
            tag_filters=list(self.tag_filters),
            ids_map=dict(self.ids_map),
            ids_flat=list(self.ids_flat),
            remove_terms_for_head=list(self.remove_terms_for_head),
            people_terms_match_mode=self.people_terms_match_mode,
            people_terms_min_should=self.people_terms_min_should,
            lookup_filter_policy=self.lookup_filter_policy_hint,
            stats_metric=self.stats_metric,
            window_years=self.window_years,
            candidate_n=self.candidate_n,
            top_k=self.top_k,
            tie_break=self.tie_break,
        )


def to_strategy_spec(raw: Any) -> StrategySpec:
    """dict/객체 형태의 strategy를 StrategySpec으로 정규화한다."""

    if isinstance(raw, StrategySpec):
        return raw
    data: Dict[str, Any]
    if isinstance(raw, dict):
        data = dict(raw)
    else:
        data = {
            "mode": getattr(raw, "mode", ""),
            "action": getattr(raw, "action", ""),
            "relation": getattr(raw, "relation", None),
            "join_key_mode": getattr(raw, "join_key_mode", None),
            "join_key_source": getattr(raw, "join_key_source", None),
            "hop1_mode": getattr(raw, "hop1_mode", None),
            "hop2_key_strategy": getattr(raw, "hop2_key_strategy", None),
            "join_keys_used_count": getattr(raw, "join_keys_used_count", None),
            "people_terms": getattr(raw, "people_terms", tuple()),
            "target_collections": getattr(raw, "target_collections", tuple()),
            "search_filter_enabled": getattr(raw, "search_filter_enabled", False),
            "lookup_filter_enabled": getattr(raw, "lookup_filter_enabled", False),
            "relation_lookup_enforce": getattr(raw, "relation_lookup_enforce", False),
            "lookup_filter_policy": getattr(raw, "lookup_filter_policy", None),
            "lookup_filter_min_should": getattr(raw, "lookup_filter_min_should", None),
            "lookup_filter_gate": getattr(raw, "lookup_filter_gate", None),
            "lookup_filter_promote_one_must": getattr(raw, "lookup_filter_promote_one_must", False),
            "lookup_title_filter_policy": getattr(raw, "lookup_title_filter_policy", None),
            "title_match_mode": getattr(raw, "title_match_mode", None),
            "search_filter_server_policy": getattr(raw, "search_filter_server_policy", None),
        }

    relation = data.get("relation")
    if isinstance(relation, list):
        relation = tuple(relation)

    return StrategySpec(
        mode=str(data.get("mode") or "").strip().lower(),
        action=str(data.get("action") or "").strip().lower(),
        relation=tuple(relation) if isinstance(relation, tuple) and len(relation) == 2 else None,
        join_key_mode=(str(data.get("join_key_mode")).strip().lower() or None) if data.get("join_key_mode") is not None else None,
        join_key_source=(str(data.get("join_key_source")).strip().lower() or None) if data.get("join_key_source") is not None else None,
        hop1_mode=(str(data.get("hop1_mode")).strip().lower() or None) if data.get("hop1_mode") is not None else None,
        hop2_key_strategy=(str(data.get("hop2_key_strategy")).strip().lower() or None) if data.get("hop2_key_strategy") is not None else None,
        join_keys_used_count=(int(data.get("join_keys_used_count")) if data.get("join_keys_used_count") is not None else None),
        people_terms=tuple(data.get("people_terms") or tuple()),
        target_collections=tuple(data.get("target_collections") or tuple()),
        search_filter_enabled=bool(data.get("search_filter_enabled", False)),
        lookup_filter_enabled=bool(data.get("lookup_filter_enabled", False)),
        relation_lookup_enforce=bool(data.get("relation_lookup_enforce", False)),
        lookup_filter_policy=data.get("lookup_filter_policy"),
        lookup_filter_min_should=data.get("lookup_filter_min_should"),
        lookup_filter_gate=data.get("lookup_filter_gate"),
        lookup_filter_promote_one_must=bool(data.get("lookup_filter_promote_one_must", False)),
        lookup_title_filter_policy=data.get("lookup_title_filter_policy"),
        title_match_mode=data.get("title_match_mode"),
        search_filter_server_policy=data.get("search_filter_server_policy"),
    )


def strategy_spec_to_response(strategy: Optional[StrategySpec]) -> Dict[str, Any]:
    if strategy is None:
        return {}
    spec = to_strategy_spec(strategy)
    return {
        "mode": spec.mode,
        "action": spec.action,
        "relation": list(spec.relation) if spec.relation else None,
        "join_key_mode": spec.join_key_mode,
        "join_key_source": spec.join_key_source,
        "hop1_mode": spec.hop1_mode,
        "hop2_key_strategy": spec.hop2_key_strategy,
        "join_keys_used_count": spec.join_keys_used_count,
        "people_terms": list(spec.people_terms),
        "target_collections": list(spec.target_collections),
        "search_filter_enabled": bool(spec.search_filter_enabled),
        "lookup_filter_enabled": bool(spec.lookup_filter_enabled),
        "relation_lookup_enforce": bool(spec.relation_lookup_enforce),
        "lookup_filter_policy": spec.lookup_filter_policy,
        "lookup_filter_min_should": spec.lookup_filter_min_should,
        "lookup_filter_gate": spec.lookup_filter_gate,
        "lookup_filter_promote_one_must": bool(spec.lookup_filter_promote_one_must),
        "lookup_title_filter_policy": spec.lookup_title_filter_policy,
        "title_match_mode": spec.title_match_mode,
        "search_filter_server_policy": spec.search_filter_server_policy,
    }
