# -*- coding: utf-8 -*-
from __future__ import annotations

"""Core planner and runtime schema definitions.\n\n- `IntentPayloadV2`: app entry payload handed to `rag_pipeline`.\n- `PlannerStage1Decision` / `PlannerStage2Slots`: staged planner outputs.\n- `QueryPlan` / `ExecutionContext` / `StrategySpec`: executor-facing runtime contracts.\n"""

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from apps.core.pipeline_steps import NormalizedIntent
from apps.core.planner_contract import normalize_stats_policy_value
from apps.core.query_intent import relation_target_collections
from apps.core.rag_constants import COL_PERF, COL_PROJECT, COL_SUPPORT
from apps.core.settings import RAG_COLLECTION_ALLOWLIST


@dataclass(frozen=True)
class IntentPayloadV2:
    """앱 진입점에서 정규화된 intent를 하나로 묶어 runtime에 넘기는 최상위 payload다.
    raw request를 다시 해석하지 않고, 이 구조체를 통해 staged planner와 rag pipeline이 같은 normalized intent를 공유한다.
    """

    normalized_intent: NormalizedIntent


Stage1Relation = Literal["project_perf", "perf_project"]


class PlannerStage1Decision(BaseModel):
    """stage 1 planner가 고정해야 할 의도 축을 담는 스키마다.
    stage 1은 action·head·relation_candidate만 결정하고, ids/filter 세부 슬롯은 다음 단계로 미룬다.
    """

    model_config = ConfigDict(extra="forbid")

    action: Literal["topic", "list", "detail", "stats", "download"]
    head: Literal["project", "perf", "people", "org", "support"]
    relation_candidate: Optional[Stage1Relation] = None
    referential_followup: bool = False
    confidence: float = Field(ge=0.0, le=1.0)


class PlannerStage2Slots(BaseModel):
    """stage 2 planner가 채울 수 있는 가변 슬롯만 분리해 담는다.
    locked strategy가 잡아둔 mode·routing은 건드리지 않고, ids_map·filters·retrieval_query·limit만 후속에 합성하게 한다.
    """

    model_config = ConfigDict(extra="forbid")

    ids_map: Dict[str, list[str]] = Field(default_factory=dict)
    filters: Dict[str, Any] = Field(default_factory=dict)
    retrieval_query: Optional[str] = None
    limit: int = Field(default=20, ge=1)
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class StrategySpec:
    """planner 결과와 runtime policy를 합친 executor 전용 전략 계약이다.
    JOIN key source, follow-up 선택, lookup filter policy, target collection 같은 실행 truth를 응답 메타와 로그까지 일관되게 전파한다.
    """

    mode: str
    action: str
    relation: Optional[Tuple[str, str]]
    # Planner output crosses service boundaries, so optional string fields stay normalized.
    join_key_mode: Optional[str] = None
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


@dataclass(frozen=True)
class QueryPlan:
    """retrieval과 rerank 런타임이 직접 소비하는 구체 실행 계획이다.
    mode, output_type, stats policy, target collections, filters를 하나로 고정해 후속 레이어가 다시 정책을 재판단하지 않게 한다.
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


@dataclass
class ExecutionContext:
    """한 요청이 runtime을 통과하는 동안 동행하는 가변 컨텍스트다.
    normalized intent에서 받은 기본 의미를 보존하면서 plan·strategy·target collections같은 runtime 산출물을 차례로 채운다.
    """

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

        """stats 관련 정책값을 정규화해 ExecutionContext 상태를 안정화한다.
        planner나 route에서 들어온 여러 설정 조합을 하나의 canonical stats policy로 맞춘 다음 후속 plan 생성에 쓴다.
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

        """normalized intent를 ExecutionContext로 복사해 runtime에서 직접 쓸 상태로 만든다.
        list·dict 필드를 복사해 후속 레이어가 intent 원본을 의도치 않게 변형하지 않게 한다.
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
        """현재 ExecutionContext 상태를 intent 형태로 다시 노출한다.
        runtime에서 조정된 mode·relation·target signal을 intent 호환 뷰로 바꿔 로그나 후속 함수가 읽게 한다.
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

    """ExecutionContext에서 executor가 쓸 StrategySpec를 조립한다.
    lookup/search/join 정책, target collection, title/rerank filter 설정, join runtime meta를 현재 context truth로 고정하는 단계다.
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
    )


def strategy_spec_to_response(strategy: Optional[StrategySpec]) -> Dict[str, Any]:
    """StrategySpec를 API 응답과 debug 메타에 실을 수 있는 dict로 변환한다.
    tuple·optional 필드를 직렬화 친화적으로 펼어 execution truth을 외부에 반영한다.
    """
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
    }


def default_target_collections() -> list[str]:
    """base route와 relation에 맞는 기본 collection 조합을 고른다.
    JOIN이면 hop 순서에 맞게 project/perf 조합을 고정하고, 아니면 base route의 기본 allowlist로 돌아간다.
    """
    allow_list = list(RAG_COLLECTION_ALLOWLIST)
    if allow_list:
        return allow_list
    return [COL_PROJECT]


def default_target_collections_for_route(base_route: str, intent: Optional[NormalizedIntent] = None) -> list[str]:
    """route 만 알 때 적용할 기본 target collection 목록을 돌려준다.
    route-level 기본값을 중앙화해 planner·runtime·route code가 같은 allowlist를 공유하게 한다.
    """
    base_route_norm = str(base_route or "").strip().lower()
    route_defaults = {
        "project": [COL_PROJECT],
        "perf": [COL_PERF],
        "support": [COL_SUPPORT],
        # people/org 정보는 project와 perf 양쪽에 존재하므로 기본 탐색도 둘 다 본다.
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
    """의도 signal과 식별자 여부를 기반으로 SEARCH/LOOKUP/JOIN 모드를 결정한다.
    strict JOIN seed가 있는지, name lookup이 우선인지, topic search로 남길지를 순차적으로 판단하는 중앙 정책이다.
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
    """ExecutionContext에 고정된 실행 정책을 QueryPlan 형태로 압축한다.
    stats policy, filters, target collections, tie-break 같은 retrieval·aggregation 인자를 후속 런타임이 그대로 소비할 수 있게 만든다.
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
    ), mode_reason


def _has_relation_join_ids(intent: NormalizedIntent) -> bool:
    """relation에 맞는 JOIN seed id가 실제로 있는지 검사한다.
    project_perf와 perf_project가 서로 다른 seed 조건을 요구하므로, relation별 필수 식별자 존재 여부를 분리한다.
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
    """ids_map에 의미 있는 식별자가 하나라도 들어 있는지 확인한다.
    LOOKUP/JOIN 선택 정책에서 광범위 시드 존재 여부를 보는 가볍운 gate로 쓴다.
    """
    ids_map = getattr(intent, "ids_map", {}) or {}
    for values in ids_map.values():
        normalized = [str(value).strip() for value in (values or []) if str(value).strip()]
        if normalized:
            return True
    return False
