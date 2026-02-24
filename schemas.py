# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Tuple

from rag_parts.pipeline_steps import NormalizedIntent
from rag_parts.planner_contract import normalize_stats_policy_value


@dataclass(frozen=True)
class IntentPayloadV2:
    """RAG intent_payload.v2 계약: normalized_intent 단일 필드."""

    normalized_intent: NormalizedIntent


@dataclass(frozen=True)
class StrategySpec:
    mode: str
    action: str
    relation: Optional[Tuple[str, str]]
    # planner 출력/정규화 단계에서 값이 흔들릴 수 있으므로 Optional[str]로 완화
    join_key_mode: Optional[str] = None
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
