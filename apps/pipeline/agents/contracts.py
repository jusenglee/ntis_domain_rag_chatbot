"""7-agent 재설계의 계약 모델 (Phase 1).

각 에이전트 경계에서 오가는 모든 데이터 구조를 frozen Pydantic 모델로 정의한다.
기존 SearchTask, CanonicalEvidence, FinalAnswer, ReferenceManifest는 변경 없이 재사용한다.

흐름 요약:

    user question
        → DialogueIntent       (DialogueAgent)
        → EntityResolution     (EntityResolverAgent)
        → SearchPlan           (SearchPlannerAgent)         — contains 1+ SearchTask
        → List[CanonicalEvidence] (RetrievalAgent)          — 기존 계약 재사용
        → EvidenceBundle       (EvidenceCuratorAgent)
        → AnswerDraft          (AnswerAgent)
        → GuardDecision        (CriticAgent)                — publish/clarify/repair/error
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.pipeline.contracts import (
    AggregateBy,
    Axis,
    CanonicalEvidence,
    FilterBundle,
    IdentifierBundle,
    ReferenceManifest,
    SearchTask,
    SubjectAnchor,
    Target,
)


# ============================================================================
# 1. DialogueAgent — 사용자 의도 분류
# ============================================================================

DialogueKind = Literal[
    "ask_search",         # 일반 검색 (사람/기관/주제)
    "ask_detail",         # 단일 대상 상세
    "ask_meta",           # 직전 항목의 메타 분류 질문 (과제/성과 여부, 종류, 식별자 형식) — 검색 없이 manifest로 즉답
    "ask_children",       # 직전 항목의 자식 엔티티 목록 (참여연구자/참여기관) — 검색 없이 focused_detail 캐시로 즉답
    "ask_similar",        # 직전 항목과 유사한 항목 — focused_detail.title을 query로 사용한 hybrid_search
    "refine_previous",    # 직전 결과를 조건 추가로 좁힘
    "compare",            # 둘 이상 비교
    "stats",              # 통계/집계
    "direct_answer",      # 인사/잡담
    "clarification",      # 정보 부족 → 사용자 되묻기
]


class CompareTarget(BaseModel):
    """compare kind일 때 비교 대상 1개."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    kind: Literal["people", "org", "project", "perf"]


class DialogueIntent(BaseModel):
    """DialogueAgent의 단일 출력.

    DialogueAgent는 "사용자가 무엇을 원하는가"만 결정한다. 검색 전략·collection·strategy·target
    최종 확정은 일체 하지 않는다. LLM이 채울 수 있는 모든 힌트는 ``*_hint`` 접미사로 받는다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"

    kind: DialogueKind

    # 사용자가 묻는 도메인 힌트 (project/perf/people/org/support). 최종 결정은 EntityResolver/Planner.
    target_hint: Optional[Target] = None

    # action 힌트 (list/detail/stats/topic/download). 최종 결정은 Planner.
    action_hint: Optional[Literal["list", "detail", "stats", "topic", "download"]] = None

    # 사람/기관 anchor 힌트 (이름/소속). 최종 person_no/org_id 해소는 EntityResolver.
    subject_name: Optional[str] = None
    subject_kind: Optional[Literal["people", "org"]] = None
    subject_affiliation_hint: Optional[str] = None

    # 공동 참여자 힌트 — subject 외의 추가 인명들 (AND 결합). "A와 B가 같이 참여한" 패턴.
    coparticipants: List[str] = Field(default_factory=list)

    # 제외(negative) 필터 — "X 제외", "특허 빼고" 같은 follow-up 패턴.
    exclude_org_name: List[str] = Field(default_factory=list)
    exclude_perf_type: List[str] = Field(default_factory=list)
    exclude_person_name: List[str] = Field(default_factory=list)

    # 식별자 힌트 (LLM이 텍스트에서 추출). 검증은 EntityResolver.
    identifier_hints: Dict[str, List[str]] = Field(default_factory=dict)
    # 예: {"pjt_id": ["1345214806"], "pjt_no": ["K-20-..."], "rst_id": ["REP-2010-..."]}

    # 직전 turn의 published manifest 인용. EntityResolver가 결정적으로 해소.
    manifest_rank: Optional[int] = Field(default=None, ge=1)

    # 필터 힌트.
    year_from: Optional[int] = Field(default=None, ge=1900, le=2100)
    year_to: Optional[int] = Field(default=None, ge=1900, le=2100)
    perf_type_hint: List[str] = Field(default_factory=list)

    # 정렬 힌트 (P0-β). 사용자가 "최근순"/"오래된 순" 명시 시 채움. 기본 "relevance"(score).
    sort_by: Literal["relevance", "recent_desc", "recent_asc"] = "relevance"

    # 답변 길이 힌트. 사용자가 "간단히/한 줄/자세히/더 자세히" 명시 시 채움.
    length_hint: Literal["brief", "default", "detailed"] = "default"

    # stats kind에서 집계 축 힌트. LLM이 "참여기관별/참여자별/기관별/분야별" 같은
    # 사용자 의도를 분류해 채움. 비어 있으면 Planner는 "year" fallback.
    aggregate_hint: Optional[AggregateBy] = None

    # compare kind일 때만 사용.
    compare_targets: List[CompareTarget] = Field(default_factory=list)

    # 검색용 자연어 질의 (사용자 입력 핵심 표현 보존).
    query: str = ""

    # direct_answer kind일 때만 사용.
    direct_text: Optional[str] = None

    # clarification kind일 때만 사용.
    clarification_question: Optional[str] = None
    clarification_options: List[str] = Field(default_factory=list)

    # 운영용
    reason: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_kind_payload(self) -> "DialogueIntent":
        if self.kind == "direct_answer" and not self.direct_text:
            raise ValueError("kind=direct_answer requires direct_text")
        if self.kind == "clarification" and not self.clarification_question:
            raise ValueError("kind=clarification requires clarification_question")
        if self.kind == "compare" and len(self.compare_targets) < 2:
            raise ValueError("kind=compare requires at least 2 compare_targets")
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("year_from must be <= year_to")
        return self


# ============================================================================
# 1b. DialogueAgent 2-pass 내부 contracts (2026-05-26 근본 원인 1·2 해결)
# ============================================================================
# - Pass 1 (IntentClassification): kind + anaphora만. 짧은 prompt로 분류 신뢰성 우선.
# - Pass 2 (SlotExtraction): kind에 필요한 슬롯만. kind-specific 짧은 prompt로 NER 안정.
# 둘은 외부에 노출되는 DialogueIntent로 merge되어 downstream(EntityResolver/SearchPlanner)
# 호환을 깨지 않는다.


class IntentClassification(BaseModel):
    """Pass 1 결과 — 의도 분류 + manifest_rank 인용 + direct/clarification 즉답.

    이 단의 책임은 **"사용자가 무엇을 원하는가"** 와 **"직전 turn의 어느 항목을 가리키는가"** 만.
    슬롯(subject_name·year·perf_type 등)은 Pass 2가 채운다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: DialogueKind
    target_hint: Optional[Target] = None
    action_hint: Optional[Literal["list", "detail", "stats", "topic", "download"]] = None
    manifest_rank: Optional[int] = Field(default=None, ge=1)

    # direct_answer / clarification는 슬롯 추출 불필요 — 여기서 즉시 종료.
    direct_text: Optional[str] = None
    clarification_question: Optional[str] = None
    clarification_options: List[str] = Field(default_factory=list)

    reason: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SlotExtraction(BaseModel):
    """Pass 2 결과 — kind에 맞는 슬롯만 추출.

    Pass 1의 kind에 따라 prompt를 분기하여 호출하므로 모든 필드를 채울 필요 없음.
    예: kind=ask_search → subject/year/perf_type/sort_by/length_hint/aggregate_hint 등.
       kind=stats        → subject + aggregate_hint.
       kind=compare      → compare_targets.
       kind=refine_previous → subject_name(검증) + year/exclude_* 등.
       kind=ask_children    → subject_name(검증 대상) 또는 비움.
       kind=ask_meta        → 비움 (필요 시 subject_name만).
       kind=ask_detail      → identifier_hints 또는 비움(manifest_rank가 Pass 1에서 채워짐).
       kind=ask_similar     → 비움.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_name: Optional[str] = None
    subject_kind: Optional[Literal["people", "org"]] = None
    subject_affiliation_hint: Optional[str] = None
    coparticipants: List[str] = Field(default_factory=list)
    exclude_org_name: List[str] = Field(default_factory=list)
    exclude_perf_type: List[str] = Field(default_factory=list)
    exclude_person_name: List[str] = Field(default_factory=list)
    identifier_hints: Dict[str, List[str]] = Field(default_factory=dict)
    year_from: Optional[int] = Field(default=None, ge=1900, le=2100)
    year_to: Optional[int] = Field(default=None, ge=1900, le=2100)
    perf_type_hint: List[str] = Field(default_factory=list)
    sort_by: Literal["relevance", "recent_desc", "recent_asc"] = "relevance"
    length_hint: Literal["brief", "default", "detailed"] = "default"
    aggregate_hint: Optional[AggregateBy] = None
    compare_targets: List[CompareTarget] = Field(default_factory=list)


# ============================================================================
# 2. EntityResolverAgent — 식별자/주체 해소
# ============================================================================

class IdentityCandidate(BaseModel):
    """동명이인/유사 기관 등 모호한 경우의 후보 1개."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["people", "org"]
    display_name: str
    person_no: Optional[str] = None
    org_id: Optional[str] = None
    affiliation_org_name: Optional[str] = None
    score: float = 0.0  # 유사도/신뢰도
    source: str = ""    # "manifest" / "fresh_lookup" / "session_memory"


class EntityResolution(BaseModel):
    """EntityResolverAgent 출력.

    DialogueIntent의 힌트를 받아 실제 식별자/주체로 해소한다. 핵심 규칙:
        - manifest_rank는 LLM 판단보다 항상 우선.
        - rst_id가 있으면 target은 강제 'perf'.
        - 모호하면 ambiguity_candidates 채우고 clarification_needed=True.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"

    # 최종 해소된 식별자/주체
    subject: Optional[SubjectAnchor] = None
    identifiers: IdentifierBundle = Field(default_factory=IdentifierBundle)
    filters: FilterBundle = Field(default_factory=FilterBundle)

    # manifest 해소 결과 (있을 때)
    manifest_rank: Optional[int] = Field(default=None, ge=1)
    manifest_resolved_target: Optional[Target] = None     # manifest item.entity_kind 우선
    manifest_resolved_axis: Optional[Axis] = None

    # 강제 target. 'rst_id가 있으면 perf' 같은 규칙이 적용된 후 최종값.
    forced_target: Optional[Target] = None

    # 모호성
    ambiguity_candidates: List[IdentityCandidate] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification_reason: Optional[str] = None

    # 운영용
    resolution_source: str = ""        # "manifest"/"identifier_literal"/"session_subject"/"llm_hint"
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# 3. SearchPlannerAgent — 검색 실행 계획 (SearchTask N개)
# ============================================================================

class SearchPlan(BaseModel):
    """SearchPlannerAgent 출력.

    1개 이상의 SearchTask와 그 결과를 합치는 정책을 포함한다.

    예 (사람 활동내역):
        tasks: [
            SearchTask(target=project, strategy=subject_anchor, ...),
            SearchTask(target=perf, strategy=subject_anchor, ...),
        ]
        merge_strategy: "by_score_dedup"

    예 (상세):
        tasks: [SearchTask(action=detail, strategy=exact_lookup, ...)]
        merge_strategy: "single"
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"

    tasks: List[SearchTask] = Field(..., min_length=1)
    merge_strategy: Literal["single", "by_score_dedup", "by_axis_groupby"] = "single"
    max_results: int = Field(default=10, ge=1, le=50)

    plan_reason: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "SearchPlan":
        if not self.tasks:
            raise ValueError("SearchPlan must have at least one task")
        return self


# ============================================================================
# 4. EvidenceCuratorAgent — 답변용 근거 묶음
# ============================================================================

EvidenceView = Literal[
    "single_detail",        # 1건 상세 — 모든 fact 노출
    "subject_activity",     # 사람/기관 활동 — 프로젝트/성과/역할별 그룹
    "list_compact",         # 일반 리스트 — 제목 + 요약
    "stats_summary",        # 통계 — 집계 결과
    "comparison_table",     # 비교 — 두 대상 매트릭스
    "empty",                # 결과 없음
]


class EvidenceGroup(BaseModel):
    """EvidenceBundle 내 의미 그룹.

    예시:
        - 사람 활동내역: pjt_no 기준 그룹핑된 같은 사업의 다년차
        - 비교: 대상별 묶음
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    group_key: str            # pjt_no 또는 compare target 이름 등
    group_label: str          # 사용자에게 보일 라벨
    role: Optional[str] = None  # "lead_org" / "participant_researcher" 등
    member_ranks: List[int] = Field(default_factory=list)  # bundle.items에서의 display_rank들


class EvidenceBundle(BaseModel):
    """EvidenceCuratorAgent 출력.

    AnswerAgent가 prompt를 만드는 최종 진실원. 여기서 결정된 display_rank가 곧
    답변 본문의 [N] 번호이자 ReferenceManifest의 published_rank가 된다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"

    view: EvidenceView
    items: List[CanonicalEvidence] = Field(default_factory=list)
    # items[i].snapshot_rank == display_rank (curator가 재부여)

    groups: List[EvidenceGroup] = Field(default_factory=list)
    total_unique: int = 0
    total_before_curation: int = 0
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "EvidenceBundle":
        if self.view == "empty" and self.items:
            raise ValueError("view=empty requires no items")
        if self.view != "empty" and not self.items:
            raise ValueError(f"view={self.view} requires non-empty items")
        return self


# ============================================================================
# 5. AnswerAgent — 답변 초안
# ============================================================================

class Citation(BaseModel):
    """답변에 등장한 [N] 인용 메타.

    AnswerAgent가 LLM 출력을 파싱한 결과를 그대로 보존한다. CriticAgent가 검증한다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(..., ge=1)
    span_start: int = 0      # 답변 본문 내 위치 (선택)
    span_end: int = 0


class AnswerDraft(BaseModel):
    """AnswerAgent 출력.

    아직 사용자에게 publish하지 않은 초안. CriticAgent의 GuardDecision이 결정한다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"

    text: str
    citations: List[Citation] = Field(default_factory=list)
    template: Literal["list", "detail", "stats", "compare", "no_result"] = "list"
    model_key: str = ""
    latency_ms: float = 0.0
    truncated: bool = False
    stream_metrics: Dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# 6. CriticAgent — 최종 검증
# ============================================================================

GuardDecisionKind = Literal[
    "publish",          # 답변 발행
    "repair_answer",    # AnswerAgent에게 1회만 되돌림
    "clarify",          # 사용자에게 재질문
    "internal_error",   # 시스템 오류 종료
]


class GuardDecision(BaseModel):
    """CriticAgent 출력.

    decision에 따라 워크플로우 다음 노드가 결정된다.
    repair_answer는 정확히 1회만 허용 (state.repair_attempted 플래그).
    재검색이 필요하면 critic이 직접 retrieval을 호출하지 않고, SearchPlanner로 re-plan 이벤트.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"

    decision: GuardDecisionKind

    # publish 시 채워짐
    text: Optional[str] = None
    reference_manifest: Optional[ReferenceManifest] = None

    # repair_answer 시 채워짐 (AnswerAgent에 전달할 보수 힌트)
    repair_hint: Optional[str] = None

    # clarify 시 채워짐
    clarification_question: Optional[str] = None
    clarification_options: List[str] = Field(default_factory=list)

    # internal_error 시 채워짐
    error_code: Optional[str] = None
    error_reason: Optional[str] = None

    reasoning: str = ""
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "GuardDecision":
        if self.decision == "publish":
            if not self.text:
                raise ValueError("decision=publish requires text")
            if self.reference_manifest is None:
                raise ValueError("decision=publish requires reference_manifest")
        if self.decision == "clarify" and not self.clarification_question:
            raise ValueError("decision=clarify requires clarification_question")
        if self.decision == "internal_error" and not self.error_code:
            raise ValueError("decision=internal_error requires error_code")
        if self.decision == "repair_answer" and not self.repair_hint:
            raise ValueError("decision=repair_answer requires repair_hint")
        return self
