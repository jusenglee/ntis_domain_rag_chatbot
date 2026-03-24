# -*- coding: utf-8 -*-
from __future__ import annotations

"""Core planner and runtime schema definitions.

- `IntentPayloadV3`: app entry payload handed to `rag_pipeline`.
- `PlannerStage1Decision` / `PlannerStage2Slots`: staged planner outputs.
- `QueryPlan` / `ExecutionContext` / `StrategySpec`: executor-facing runtime contracts.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from apps.core.pipeline_steps import NormalizedIntent
from apps.core.planner_contract import normalize_stats_policy_value
from apps.core.query_intent import relation_target_collections
from apps.core.rag_constants import COL_PERF, COL_PROJECT, COL_SUPPORT
from apps.core.settings import RAG_COLLECTION_ALLOWLIST


@dataclass(frozen=True)
class IntentPayloadV3:
    """Transport payload shared across planner, retrieval, and runtime."""

    normalized_intent: NormalizedIntent
    intent_payload_version: Literal["v3"] = "v3"
    question_analysis: Any = None
    strategy_meta: Dict[str, Any] = field(default_factory=dict)


Stage1Relation = Literal["project_perf", "perf_project"]


class PlannerStage1Decision(BaseModel):
    """stage 1 planner? ???? ? ?? ?? ?? ????.
    stage 1? action, head, relation_candidate? ???? ids/filter ??? ?? ??? ???.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["topic", "list", "detail", "stats", "download"]
    head: Literal["project", "perf", "people", "org", "support"]
    relation_candidate: Optional[Stage1Relation] = None
    referential_followup: bool = False
    confidence: float = Field(ge=0.0, le=1.0)


class PlannerStage2Slots(BaseModel):
    """stage 2 planner? ?? ?? ??? ??? ???.
    locked strategy? ??? mode? routing? ???? ??, ids_map, filters, retrieval_query, limit? ???? ???? ??.
    """

    model_config = ConfigDict(extra="forbid")

    ids_map: Dict[str, list[str]] = Field(default_factory=dict)
    candidate_keys: Dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    project_key_policy: Optional[str] = None
    join_resolution_policy: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    retrieval_query: Optional[str] = None
    limit: int = Field(default=20, ge=1)
    display_limit: int = Field(default=20, ge=1)
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class StrategySpec:
    """Executor-facing strategy contract composed from planner truth and runtime policy.
    Join metadata, filter policy, target collections, and query-graph summaries stay together so
    response metadata and operational logs can report the same execution truth.
    """

    mode: str
    action: str
    relation: Optional[Tuple[str, str]]
    # Planner output crosses service boundaries, so optional string fields stay normalized.
    join_key_mode: Optional[str] = None
    project_key_policy: Optional[str] = None
    join_resolution_policy: Optional[str] = None
    join_key_source: Optional[str] = None
    hop1_mode: Optional[str] = None
    join_compile_selection: Optional[str] = None
    hop2_key_strategy: Optional[str] = None
    resolved_runtime_key_kind: Optional[str] = None
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
    query_graph_kind: Optional[str] = None
    anchor_summary: Dict[str, Any] = field(default_factory=dict)
    anchor_resolution_status: Optional[str] = None
    ambiguity_codes: Tuple[str, ...] = field(default_factory=tuple)
    resolved_researcher_count: Optional[int] = None
    resolved_org_count: Optional[int] = None
    aggregation_kind: Optional[str] = None
    series_kind: Optional[str] = None
    pattern_kind: Optional[str] = None
    bundle_kind: Optional[str] = None
    bundle_targets: Tuple[str, ...] = field(default_factory=tuple)
    guidance_required: bool = False
    reverse_trace_enabled: bool = False
    reverse_trace_hop_count: Optional[int] = None
    followup_relation_hint: Optional[str] = None


@dataclass(frozen=True)
class ResolvedAnchorSet:
    """Resolved identifier and name anchors that can seed traversal planning.

    This structure stays intentionally deterministic: it summarizes anchors already present in
    `NormalizedIntent` without inventing new identifiers or silently changing role semantics.
    """

    researcher_names: Tuple[str, ...] = field(default_factory=tuple)
    org_terms_by_role: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    project_instance_ids: Tuple[str, ...] = field(default_factory=tuple)
    project_group_ids: Tuple[str, ...] = field(default_factory=tuple)
    perf_ids: Tuple[str, ...] = field(default_factory=tuple)
    ambiguities: Tuple[str, ...] = field(default_factory=tuple)
    resolution_status: str = "none"


@dataclass(frozen=True)
class AggregationPlan:
    """Aggregation intent that retrieval runtime can execute after retrieval completes."""

    metric: str
    group_by: str
    comparison_mode: str
    threshold: Optional[int] = None
    top_k: Optional[int] = None


@dataclass(frozen=True)
class TemporalConstraint:
    """Normalized time constraint that can be reused across runtime stages."""

    year_from: Optional[str] = None
    year_to: Optional[str] = None
    window_years: Optional[int] = None


@dataclass(frozen=True)
class ProjectSeriesPlan:
    """Minimal series metadata for project-group or yearly flow questions."""

    series_key_kind: str
    relation_hint: Optional[str] = None


@dataclass(frozen=True)
class PatternAnalysisPlan:
    """Pattern-analysis metadata that runtime can execute after retrieval."""

    kind: str


@dataclass(frozen=True)
class MultiHopBundlePlan:
    """Planner-first metadata for bundling multiple downstream targets from a project set."""

    kind: str
    targets: Tuple[str, ...] = field(default_factory=tuple)
    representative_only: bool = False
    guidance_required: bool = False


@dataclass(frozen=True)
class PlanStep:
    """Single step inside a retrieval graph plan."""

    kind: str
    head: str
    relation: Optional[Tuple[str, str]] = None


@dataclass(frozen=True)
class QueryGraphPlan:
    """High-level retrieval graph summary that is more specific than mode alone."""

    kind: str
    steps: Tuple[PlanStep, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class QueryPlan:
    """Concrete execution plan consumed by retrieval and rerank runtime.
    Query-graph, aggregation, and series metadata travel with the usual mode/output_type/filter
    fields so planner and runtime observability can share the same plan vocabulary.
    """

    mode: str
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
    filters: Dict[str, Any]
    project_key_policy: Optional[str] = None
    join_resolution_policy: Optional[str] = None
    query_graph: Optional[QueryGraphPlan] = None
    aggregation_plan: Optional[AggregationPlan] = None
    temporal_constraint: Optional[TemporalConstraint] = None
    project_series_plan: Optional[ProjectSeriesPlan] = None
    pattern_analysis_plan: Optional[PatternAnalysisPlan] = None
    multi_hop_bundle_plan: Optional[MultiHopBundlePlan] = None
    reverse_trace_followup: bool = False
    followup_relation_hint: Optional[str] = None
    pattern_kind: Optional[str] = None
    bundle_kind: Optional[str] = None
    bundle_targets: Tuple[str, ...] = field(default_factory=tuple)
    guidance_required: bool = False


@dataclass
class ExecutionContext:
    """??? runtime? ???? ?? ???? ?? ?????.
    normalized intent?? ?? ?? ?? ????? plan, strategy, target collections ?? runtime ???? ???.
    """

    intent: Any
    base_route: str
    action: str
    mode: Optional[str]
    relation: Optional[Tuple[str, str]]
    join_key_mode: Optional[str]
    is_id_query: bool
    output_type: Optional[str]
    reverse_trace_followup: bool
    followup_relation_hint: Optional[str]
    pattern_kind: Optional[str]
    bundle_kind: Optional[str]
    bundle_targets: Tuple[str, ...]
    guidance_required: bool
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
    candidate_keys: Dict[str, list[dict[str, Any]]]
    project_key_policy: Optional[str]
    join_resolution_policy: Optional[str]
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

        """stats ?? ???? ???? ExecutionContext ??? ????.
        planner? route?? ??? ?? ?? ??? canonical stats policy? ?? ?? ?? plan ??? ???.
        """
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

        """normalized intent? ExecutionContext? ??? runtime?? ?? ?? ??? ???.
        list, dict ??? ???? ?? ??? intent ??? ???? ?? ????.
        """
        return cls(
            intent=intent,
            base_route=intent.base_route,
            action=intent.action,
            mode=getattr(intent, "mode", None),
            relation=intent.relation,
            join_key_mode=getattr(intent, "join_key_mode", None),
            is_id_query=intent.is_id_query,
            output_type=intent.output_type,
            reverse_trace_followup=bool(getattr(intent, "reverse_trace_followup", False)),
            followup_relation_hint=getattr(intent, "followup_relation_hint", None),
            pattern_kind=getattr(intent, "pattern_kind", None),
            bundle_kind=getattr(intent, "bundle_kind", None),
            bundle_targets=tuple(getattr(intent, "bundle_targets", ()) or ()),
            guidance_required=bool(getattr(intent, "guidance_required", False)),
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
            candidate_keys=dict(getattr(intent, "candidate_keys", {}) or {}),
            project_key_policy=getattr(intent, "project_key_policy", None),
            join_resolution_policy=getattr(intent, "join_resolution_policy", None),
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
        """?? ExecutionContext ??? intent ??? ?? ????.
        runtime?? ??? mode, relation, target signal? intent ?? ?? ?? ??? ?? ??? ?? ??.
        """
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
            reverse_trace_followup=bool(self.reverse_trace_followup),
            followup_relation_hint=self.followup_relation_hint,
            pattern_kind=self.pattern_kind,
            bundle_kind=self.bundle_kind,
            bundle_targets=list(self.bundle_targets),
            guidance_required=bool(self.guidance_required),
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
            candidate_keys=dict(self.candidate_keys),
            project_key_policy=self.project_key_policy,
            join_resolution_policy=self.join_resolution_policy,
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


def derive_resolved_anchor_set(intent: NormalizedIntent) -> ResolvedAnchorSet:
    """Build deterministic anchor metadata from `NormalizedIntent`.

    The resolver only normalizes anchors that already survived planner and contract handling.
    It preserves `pjt_id` versus `pjt_no`, keeps organization roles separate, and records common
    ambiguity states so logs can distinguish weak name-only anchors from fully resolved seeds.
    """
    ids_map = getattr(intent, "ids_map", {}) or {}
    perf_ids: list[str] = []
    for key in ("doi", "paper_id", "rst_id", "patent_no"):
        perf_ids.extend(str(value).strip() for value in (ids_map.get(key) or []) if str(value).strip())

    researcher_names = tuple(
        str(value).strip() for value in (getattr(intent, "people_terms", []) or []) if str(value).strip()
    )
    lead_terms = tuple(
        str(value).strip() for value in (getattr(intent, "lead_org_terms", []) or []) if str(value).strip()
    )
    participant_terms = tuple(
        str(value).strip() for value in (getattr(intent, "participant_org_terms", []) or []) if str(value).strip()
    )
    affiliation_terms = tuple(
        str(value).strip() for value in (getattr(intent, "people_affiliation_org_terms", []) or []) if str(value).strip()
    )
    generic_org_terms = tuple(
        str(value).strip() for value in (getattr(intent, "org_terms", []) or []) if str(value).strip()
    )

    role_seen = {*(lead_terms or ()), *(participant_terms or ()), *(affiliation_terms or ())}
    generic_only_terms = tuple(term for term in generic_org_terms if term not in role_seen)
    org_terms_by_role = {
        "generic": generic_only_terms,
        "lead": lead_terms,
        "participant": participant_terms,
        "affiliation": affiliation_terms,
    }

    ambiguities: list[str] = []
    if researcher_names and not any(org_terms_by_role.values()) and not (ids_map.get("person_no") or []):
        ambiguities.append("researcher_name_only")
    if generic_only_terms and not str(getattr(intent, "org_role", "") or "").strip():
        ambiguities.append("org_role_unspecified")
    if researcher_names and generic_only_terms:
        ambiguities.append("researcher_org_pair_unresolved")

    has_any_anchor = bool(researcher_names or any(org_terms_by_role.values()) or ids_map)
    if not has_any_anchor:
        resolution_status = "none"
    elif ambiguities and not (ids_map.get("pjt_id") or ids_map.get("pjt_no") or perf_ids):
        resolution_status = "ambiguous"
    elif ambiguities:
        resolution_status = "partial"
    else:
        resolution_status = "resolved"

    return ResolvedAnchorSet(
        researcher_names=researcher_names,
        org_terms_by_role={key: value for key, value in org_terms_by_role.items() if value},
        project_instance_ids=tuple(str(value).strip() for value in (ids_map.get("pjt_id") or []) if str(value).strip()),
        project_group_ids=tuple(str(value).strip() for value in (ids_map.get("pjt_no") or []) if str(value).strip()),
        perf_ids=tuple(perf_ids),
        ambiguities=tuple(ambiguities),
        resolution_status=resolution_status,
    )


def summarize_anchor_set(anchor_set: ResolvedAnchorSet) -> Dict[str, Any]:
    """Create a compact anchor summary for logs and strategy responses."""
    all_org_terms = {term for values in anchor_set.org_terms_by_role.values() for term in values}
    return {
        "researcher_count": len(anchor_set.researcher_names),
        "org_role_counts": {key: len(values) for key, values in anchor_set.org_terms_by_role.items()},
        "generic_org_count": len(anchor_set.org_terms_by_role.get("generic", ())),
        "project_instance_seed_count": len(anchor_set.project_instance_ids),
        "project_group_seed_count": len(anchor_set.project_group_ids),
        "perf_seed_count": len(anchor_set.perf_ids),
        "ambiguity_count": len(anchor_set.ambiguities),
        "ambiguities": list(anchor_set.ambiguities),
        "anchor_resolution_status": anchor_set.resolution_status,
        "resolved_researcher_count": len(anchor_set.researcher_names),
        "resolved_org_count": len(all_org_terms),
    }


def derive_temporal_constraint(intent: NormalizedIntent) -> Optional[TemporalConstraint]:
    """Summarize explicit year ranges or relative windows as temporal metadata."""
    year_from = str(getattr(intent, "year_from", "") or "").strip() or None
    year_to = str(getattr(intent, "year_to", "") or "").strip() or None
    years = [str(value).strip() for value in (getattr(intent, "years", []) or []) if str(value).strip()]
    if not year_from and years:
        year_from = years[0]
    if not year_to and years:
        year_to = years[-1]
    window_years = getattr(intent, "window_years", None)
    if year_from or year_to or window_years:
        return TemporalConstraint(year_from=year_from, year_to=year_to, window_years=window_years)
    return None


def derive_aggregation_plan(intent: NormalizedIntent, output_type: Optional[str]) -> Optional[AggregationPlan]:
    """Detect whether the current intent already implies aggregation or comparison."""
    output_type_norm = str(output_type or "").strip().lower() or None
    action = str(getattr(intent, "action", "") or "").strip().lower()
    wants_rank = bool(getattr(intent, "wants_rank", False))
    if action != "stats" and output_type_norm != "comparison" and not wants_rank:
        return None
    comparison_mode = "top_k" if wants_rank else ("comparison" if output_type_norm == "comparison" else "stats")
    group_by = "project_group" if bool(getattr(intent, "ids_map", {}).get("pjt_no")) else "project"
    return AggregationPlan(
        metric=str(getattr(intent, "stats_metric", "project_participation_count") or "project_participation_count"),
        group_by=group_by,
        comparison_mode=comparison_mode,
        threshold=getattr(intent, "min_metric_count", None),
        top_k=int(getattr(intent, "top_k", 1) or 1),
    )


def derive_project_series_plan(intent: NormalizedIntent, output_type: Optional[str]) -> Optional[ProjectSeriesPlan]:
    """Create minimal project-series metadata when the query implies yearly or group flow."""
    output_type_norm = str(output_type or "").strip().lower() or None
    ids_map = getattr(intent, "ids_map", {}) or {}
    if output_type_norm != "series" and not ids_map.get("pjt_no"):
        return None
    return ProjectSeriesPlan(
        series_key_kind="pjt_no" if ids_map.get("pjt_no") else "year_window",
        relation_hint="_".join(getattr(intent, "relation", ()) or ()) or None,
    )


def derive_pattern_analysis_plan(intent: NormalizedIntent) -> Optional[PatternAnalysisPlan]:
    """Return planner-first pattern-analysis metadata when stage2 fixed a pattern kind."""
    pattern_kind = str(getattr(intent, "pattern_kind", "") or "").strip().lower() or None
    if not pattern_kind:
        return None
    return PatternAnalysisPlan(kind=pattern_kind)


def derive_multi_hop_bundle_plan(intent: NormalizedIntent) -> Optional[MultiHopBundlePlan]:
    """Return planner-first bundle metadata for project-output bundle questions."""
    bundle_targets = tuple(str(value).strip().lower() for value in (getattr(intent, "bundle_targets", None) or []) if str(value).strip())
    if not bundle_targets:
        return None
    bundle_kind = str(getattr(intent, "bundle_kind", "") or "").strip().lower() or "project_outputs"
    return MultiHopBundlePlan(
        kind=bundle_kind,
        targets=bundle_targets,
        representative_only=bool(getattr(intent, "representative_only", False)),
        guidance_required=bool(getattr(intent, "guidance_required", False)),
    )


def derive_query_graph_plan(
    intent: NormalizedIntent,
    *,
    mode: str,
    relation: Optional[Tuple[str, str]],
    output_type: Optional[str],
    aggregation_plan: Optional[AggregationPlan],
    project_series_plan: Optional[ProjectSeriesPlan],
    pattern_analysis_plan: Optional[PatternAnalysisPlan],
    multi_hop_bundle_plan: Optional[MultiHopBundlePlan],
) -> QueryGraphPlan:
    """Derive a retrieval-graph skeleton without changing existing mode/relation policy."""
    base_route = str(getattr(intent, "base_route", "") or "").strip().lower() or "project"
    output_type_norm = str(output_type or "").strip().lower() or "summary"
    if multi_hop_bundle_plan is not None:
        return QueryGraphPlan(
            kind="multi_hop_bundle",
            steps=(
                PlanStep(kind="resolve_anchor", head=base_route),
                PlanStep(kind="lookup_projects", head="project", relation=relation),
                PlanStep(kind="bundle_project_outputs", head=multi_hop_bundle_plan.kind, relation=relation),
            ),
        )
    if pattern_analysis_plan is not None:
        steps = [PlanStep(kind="lookup_anchor", head=base_route)]
        if pattern_analysis_plan.kind == "series_member_change" and project_series_plan is not None:
            steps.append(PlanStep(kind="expand_series", head="project", relation=relation))
        elif relation in {("project", "perf"), ("perf", "project")}:
            steps.append(PlanStep(kind="expand_relation", head=relation[1], relation=relation))
        steps.append(PlanStep(kind="analyze_pattern", head=pattern_analysis_plan.kind, relation=relation))
        return QueryGraphPlan(kind="pattern_analysis", steps=tuple(steps))
    if project_series_plan is not None:
        return QueryGraphPlan(
            kind="project_series",
            steps=(
                PlanStep(kind="lookup_projects", head=base_route),
                PlanStep(kind="series", head="project", relation=relation),
            ),
        )
    if aggregation_plan is not None:
        return QueryGraphPlan(
            kind="aggregate_comparison",
            steps=(
                PlanStep(kind="lookup_projects" if base_route != "perf" else "lookup_perf", head=base_route),
                PlanStep(kind="aggregate", head=aggregation_plan.group_by, relation=relation),
            ),
        )
    if relation == ("project", "perf"):
        return QueryGraphPlan(
            kind="project_to_perf",
            steps=(
                PlanStep(kind="lookup_projects", head="project"),
                PlanStep(kind="join_project_to_perf", head="perf", relation=relation),
            ),
        )
    if relation == ("perf", "project"):
        if bool(getattr(intent, "reverse_trace_followup", False)):
            return QueryGraphPlan(
                kind="perf_to_project_to_perf",
                steps=(
                    PlanStep(kind="lookup_perf", head="perf"),
                    PlanStep(kind="join_perf_to_project", head="project", relation=relation),
                    PlanStep(kind="followup_project_to_perf", head="perf", relation=("project", "perf")),
                ),
            )
        return QueryGraphPlan(
            kind="perf_to_project",
            steps=(
                PlanStep(kind="lookup_perf", head="perf"),
                PlanStep(kind="join_perf_to_project", head="project", relation=relation),
            ),
        )
    if output_type_norm == "comparison":
        return QueryGraphPlan(kind="aggregate_comparison", steps=(PlanStep(kind="aggregate", head=base_route),))
    if output_type_norm == "series":
        return QueryGraphPlan(kind="project_series", steps=(PlanStep(kind="series", head=base_route),))
    return QueryGraphPlan(kind=f"{mode}_single_route", steps=(PlanStep(kind=("lookup" if mode == "lookup" else mode), head=base_route),))


def to_strategy_spec(raw: Any) -> StrategySpec:

    """ExecutionContext?? executor? ? StrategySpec? ????.
    lookup/search/join ??, target collection, title/rerank filter ??, join runtime meta? ?? context truth? ???? ???.
    """
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
            "project_key_policy": getattr(raw, "project_key_policy", None),
            "join_resolution_policy": getattr(raw, "join_resolution_policy", None),
            "join_key_source": getattr(raw, "join_key_source", None),
            "hop1_mode": getattr(raw, "hop1_mode", None),
            "join_compile_selection": getattr(raw, "join_compile_selection", None),
            "hop2_key_strategy": getattr(raw, "hop2_key_strategy", None),
            "resolved_runtime_key_kind": getattr(raw, "resolved_runtime_key_kind", None),
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
            "query_graph_kind": getattr(raw, "query_graph_kind", None),
            "anchor_summary": getattr(raw, "anchor_summary", None),
            "anchor_resolution_status": getattr(raw, "anchor_resolution_status", None),
            "ambiguity_codes": getattr(raw, "ambiguity_codes", None),
            "resolved_researcher_count": getattr(raw, "resolved_researcher_count", None),
            "resolved_org_count": getattr(raw, "resolved_org_count", None),
            "aggregation_kind": getattr(raw, "aggregation_kind", None),
            "series_kind": getattr(raw, "series_kind", None),
            "pattern_kind": getattr(raw, "pattern_kind", None),
            "bundle_kind": getattr(raw, "bundle_kind", None),
            "bundle_targets": getattr(raw, "bundle_targets", None),
            "guidance_required": getattr(raw, "guidance_required", None),
            "reverse_trace_enabled": getattr(raw, "reverse_trace_enabled", False),
            "reverse_trace_hop_count": getattr(raw, "reverse_trace_hop_count", None),
            "followup_relation_hint": getattr(raw, "followup_relation_hint", None),
        }

    relation = data.get("relation")
    if isinstance(relation, list):
        relation = tuple(relation)

    return StrategySpec(
        mode=str(data.get("mode") or "").strip().lower(),
        action=str(data.get("action") or "").strip().lower(),
        relation=tuple(relation) if isinstance(relation, tuple) and len(relation) == 2 else None,
        join_key_mode=(str(data.get("join_key_mode")).strip().lower() or None) if data.get("join_key_mode") is not None else None,
        project_key_policy=(str(data.get("project_key_policy")).strip().lower() or None) if data.get("project_key_policy") is not None else None,
        join_resolution_policy=(str(data.get("join_resolution_policy")).strip().lower() or None) if data.get("join_resolution_policy") is not None else None,
        join_key_source=(str(data.get("join_key_source")).strip().lower() or None) if data.get("join_key_source") is not None else None,
        hop1_mode=(str(data.get("hop1_mode")).strip().lower() or None) if data.get("hop1_mode") is not None else None,
        join_compile_selection=(str(data.get("join_compile_selection")).strip().lower() or None) if data.get("join_compile_selection") is not None else None,
        hop2_key_strategy=(str(data.get("hop2_key_strategy")).strip().lower() or None) if data.get("hop2_key_strategy") is not None else None,
        resolved_runtime_key_kind=(str(data.get("resolved_runtime_key_kind")).strip().lower() or None) if data.get("resolved_runtime_key_kind") is not None else None,
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
        query_graph_kind=(str(data.get("query_graph_kind")).strip().lower() or None) if data.get("query_graph_kind") is not None else None,
        anchor_summary=dict(data.get("anchor_summary") or {}),
        anchor_resolution_status=(str(data.get("anchor_resolution_status")).strip().lower() or None) if data.get("anchor_resolution_status") is not None else None,
        ambiguity_codes=tuple(data.get("ambiguity_codes") or tuple()),
        resolved_researcher_count=(int(data.get("resolved_researcher_count")) if data.get("resolved_researcher_count") is not None else None),
        resolved_org_count=(int(data.get("resolved_org_count")) if data.get("resolved_org_count") is not None else None),
        aggregation_kind=(str(data.get("aggregation_kind")).strip().lower() or None) if data.get("aggregation_kind") is not None else None,
        series_kind=(str(data.get("series_kind")).strip().lower() or None) if data.get("series_kind") is not None else None,
        pattern_kind=(str(data.get("pattern_kind")).strip().lower() or None) if data.get("pattern_kind") is not None else None,
        bundle_kind=(str(data.get("bundle_kind")).strip().lower() or None) if data.get("bundle_kind") is not None else None,
        bundle_targets=tuple(str(value).strip().lower() for value in (data.get("bundle_targets") or tuple()) if str(value).strip()),
        guidance_required=bool(data.get("guidance_required", False)),
        reverse_trace_enabled=bool(data.get("reverse_trace_enabled", False)),
        reverse_trace_hop_count=(int(data.get("reverse_trace_hop_count")) if data.get("reverse_trace_hop_count") is not None else None),
        followup_relation_hint=(str(data.get("followup_relation_hint")).strip().lower() or None) if data.get("followup_relation_hint") is not None else None,
    )


def strategy_spec_to_response(strategy: Optional[StrategySpec]) -> Dict[str, Any]:
    """StrategySpec? API ??? debug ??? ?? ?? dict? ????.
    tuple? optional ??? ??? ????? ?? execution truth? ??? ????.
    """
    if strategy is None:
        return {}
    spec = to_strategy_spec(strategy)
    return {
        "mode": spec.mode,
        "action": spec.action,
        "relation": list(spec.relation) if spec.relation else None,
        "join_key_mode": spec.join_key_mode,
        "project_key_policy": spec.project_key_policy,
        "join_resolution_policy": spec.join_resolution_policy,
        "join_key_source": spec.join_key_source,
        "hop1_mode": spec.hop1_mode,
        "join_compile_selection": spec.join_compile_selection,
        "hop2_key_strategy": spec.hop2_key_strategy,
        "resolved_runtime_key_kind": spec.resolved_runtime_key_kind,
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
        "query_graph_kind": spec.query_graph_kind,
        "anchor_summary": dict(spec.anchor_summary or {}),
        "anchor_resolution_status": spec.anchor_resolution_status,
        "ambiguity_codes": list(spec.ambiguity_codes),
        "resolved_researcher_count": spec.resolved_researcher_count,
        "resolved_org_count": spec.resolved_org_count,
        "aggregation_kind": spec.aggregation_kind,
        "series_kind": spec.series_kind,
        "pattern_kind": spec.pattern_kind,
        "bundle_kind": spec.bundle_kind,
        "bundle_targets": list(spec.bundle_targets),
        "guidance_required": bool(spec.guidance_required),
        "reverse_trace_enabled": bool(spec.reverse_trace_enabled),
        "reverse_trace_hop_count": spec.reverse_trace_hop_count,
        "followup_relation_hint": spec.followup_relation_hint,
    }


def default_target_collections() -> list[str]:
    """base route? relation? ?? ?? collection ??? ???.
    JOIN?? hop ??? ?? project/perf ??? ????, ??? base route? ?? allowlist? ????.
    """
    allow_list = list(RAG_COLLECTION_ALLOWLIST)
    if allow_list:
        return allow_list
    return [COL_PROJECT]


def default_target_collections_for_route(base_route: str, intent: Optional[NormalizedIntent] = None) -> list[str]:
    """route ???? ??? ?? target collection ??? ????.
    route-level ???? ???? planner, runtime, route code? ?? allowlist? ???? ??.
    """
    base_route_norm = str(base_route or "").strip().lower()
    route_defaults = {
        "project": [COL_PROJECT],
        "perf": [COL_PERF],
        "support": [COL_SUPPORT],
        # people/org ??? project? perf ??? ??? ?? ? ?? ?? ??? ??.
        "people": [COL_PROJECT, COL_PERF],
        "org": [COL_PROJECT, COL_PERF],
    }
    desired = list(route_defaults.get(base_route_norm, [COL_PROJECT]))

    allow_list = list(RAG_COLLECTION_ALLOWLIST)
    if not allow_list:
        return desired

    allow_set = set(allow_list)
    selected = [c for c in desired if c in allow_set]
    if selected:
        return selected
    return allow_list


def select_mode_policy(intent: NormalizedIntent) -> Tuple[str, str]:
    """??? ??? ?? ??? ???? SEARCH/LOOKUP/JOIN ??? ????.
    strict JOIN seed? ???, name lookup? ????, topic search? ??? ????? ???? ?? ????.
    """
    action = intent.action
    relation = intent.relation
    if action == "relation" and relation:
        return "join", "relation_action"
    if relation == ("people", "project") and list(getattr(intent, "people_terms", []) or []):
        return "lookup", "people_project_lookup"
    if relation and _has_relation_join_ids(intent):
        return "join", "relation_ids"

    people_terms = [str(t).strip() for t in (getattr(intent, "people_terms", None) or []) if str(t).strip()]
    lead_org_terms = [str(t).strip() for t in (getattr(intent, "lead_org_terms", None) or []) if str(t).strip()]
    participant_org_terms = [str(t).strip() for t in (getattr(intent, "participant_org_terms", None) or []) if str(t).strip()]
    affiliation_org_terms = [str(t).strip() for t in (getattr(intent, "people_affiliation_org_terms", None) or []) if str(t).strip()]
    org_terms = [str(t).strip() for t in (getattr(intent, "org_terms", None) or []) if str(t).strip()]

    has_name_lookup_signal = bool(
        people_terms
        or lead_org_terms
        or participant_org_terms
        or affiliation_org_terms
        or org_terms
        or (str(getattr(intent, "org_role", "") or "").strip().lower() in ("lead", "performer", "performing", "participant", "affiliation"))
    )
    if has_name_lookup_signal and action not in ("support",):
        return "lookup", "people_org_name_lookup"

    if bool(intent.is_id_query) or _has_any_ids(intent) or action in ("id_exact", "id_fuzzy"):
        return "lookup", "id_or_exact"
    if action in ("list", "stats", "download"):
        return "lookup", "list_like"
    if action in ("topic", "search"):
        return "search", "topic_search"
    return "search", "default"


def build_query_plan(
    intent: NormalizedIntent,
    *,
    preferred_mode: Optional[str] = None,
    preferred_mode_source: Optional[str] = None,
) -> Tuple[QueryPlan, str]:
    """ExecutionContext?? ??? ?? ??? QueryPlan ??? ????.
    stats policy, filters, target collections, tie-break ?? retrieval?aggregation ??? ?? ???? ??? ?? ???.
    """
    action = intent.action
    base_route = intent.base_route
    relation = intent.relation
    output_type = getattr(intent, "output_type", None)
    stats_policy = normalize_stats_policy_value(
        stats_metric=getattr(intent, "stats_metric", None),
        window_years=getattr(intent, "window_years", None),
        candidate_n=getattr(intent, "candidate_n", None),
        top_k=getattr(intent, "top_k", None),
        tie_break=getattr(intent, "tie_break", None),
    )

    if preferred_mode:
        mode = preferred_mode
        mode_reason = f"planner:{preferred_mode_source or 'mode'}"
    else:
        mode, mode_reason = select_mode_policy(intent)

    if relation:
        target_cols = list(relation_target_collections(relation) or default_target_collections())
    else:
        target_cols = default_target_collections_for_route(base_route, intent)

    aggregation_plan = derive_aggregation_plan(intent, output_type)
    project_series_plan = derive_project_series_plan(intent, output_type)
    pattern_analysis_plan = derive_pattern_analysis_plan(intent)
    multi_hop_bundle_plan = derive_multi_hop_bundle_plan(intent)
    temporal_constraint = derive_temporal_constraint(intent)
    query_graph = derive_query_graph_plan(
        intent,
        mode=mode,
        relation=relation,
        output_type=output_type,
        aggregation_plan=aggregation_plan,
        project_series_plan=project_series_plan,
        pattern_analysis_plan=pattern_analysis_plan,
        multi_hop_bundle_plan=multi_hop_bundle_plan,
    )

    return QueryPlan(
        mode=mode,
        base_route=base_route,
        action=action,
        relation=relation,
        join_key_mode=getattr(intent, "join_key_mode", None),
        output_type=output_type,
        stats_metric=stats_policy["stats_metric"],
        window_years=stats_policy["window_years"],
        candidate_n=stats_policy["candidate_n"],
        top_k=stats_policy["top_k"],
        tie_break=stats_policy["tie_break"],
        target_collections=tuple(target_cols),
        filters={},
        query_graph=query_graph,
        aggregation_plan=aggregation_plan,
        temporal_constraint=temporal_constraint,
        project_series_plan=project_series_plan,
        pattern_analysis_plan=pattern_analysis_plan,
        multi_hop_bundle_plan=multi_hop_bundle_plan,
        reverse_trace_followup=bool(getattr(intent, "reverse_trace_followup", False)),
        followup_relation_hint=(str(getattr(intent, "followup_relation_hint", "") or "").strip().lower() or None),
        bundle_kind=(getattr(multi_hop_bundle_plan, "kind", None) if multi_hop_bundle_plan is not None else None),
        bundle_targets=tuple(getattr(multi_hop_bundle_plan, "targets", tuple()) or tuple()),
        guidance_required=bool(getattr(multi_hop_bundle_plan, "guidance_required", False)) if multi_hop_bundle_plan is not None else bool(getattr(intent, "guidance_required", False)),
    ), mode_reason


def _has_relation_join_ids(intent: NormalizedIntent) -> bool:
    """relation? ?? JOIN seed id? ??? ??? ????.
    project_perf? perf_project? ?? ?? seed ??? ????? relation? ?? ??? ?? ??? ????.
    """
    ids_map = getattr(intent, "ids_map", {}) or {}
    relation_keys = ("pjt_id", "pjt_no", "doi", "patent_no", "rst_id", "paper_id")
    for key in relation_keys:
        values = ids_map.get(key)
        if not values:
            continue
        normalized = [str(value).strip() for value in values if str(value).strip()]
        if normalized:
            return True
    return False


def _has_any_ids(intent: NormalizedIntent) -> bool:
    """ids_map ?? ??? ??? ?? ???? ?? ??? ????.
    LOOKUP/JOIN ?? ???? ???? ?? ?? ??? ?? ??? gate?.
    """
    ids_map = getattr(intent, "ids_map", {}) or {}
    for values in ids_map.values():
        normalized = [str(value).strip() for value in (values or []) if str(value).strip()]
        if normalized:
            return True
    return False


