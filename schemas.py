# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class StrategySpec:
    mode: str
    action: str
    relation: Optional[Tuple[str, str]]
    people_terms: Tuple[str, ...] = field(default_factory=tuple)
    target_collections: Tuple[str, ...] = field(default_factory=tuple)
    search_filter_enabled: bool = False
    lookup_filter_enabled: bool = False
    relation_lookup_enforce: bool = False
    lookup_filter_policy: Optional[str] = None
    lookup_title_filter_policy: Optional[str] = None
    search_filter_server_policy: Optional[str] = None


@dataclass(frozen=True)
class QueryPlan:
    mode: str  # "search" | "lookup" | "join"
    base_route: str
    action: str
    relation: Optional[Tuple[str, str]]
    output_type: Optional[str]
    target_collections: Tuple[str, ...]
    # server-side filters by collection (optional)
    filters: Dict[str, Any]


@dataclass
class ExecutionContext:
    intent: Any
    base_route: str
    action: str
    relation: Optional[Tuple[str, str]]
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
    plan: Optional[QueryPlan] = None
    strategy: Optional[StrategySpec] = None
    target_collections: list[str] = field(default_factory=list)

    @classmethod
    def from_intent(cls, intent: Any) -> "ExecutionContext":
        return cls(
            intent=intent,
            base_route=intent.base_route,
            action=intent.action,
            relation=intent.relation,
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
        )

    def intent_view(self) -> Any:
        from dataclasses import replace

        return replace(
            self.intent,
            base_route=self.base_route,
            action=self.action,
            relation=self.relation,
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
        )
