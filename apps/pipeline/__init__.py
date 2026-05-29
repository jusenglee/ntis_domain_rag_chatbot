"""7-agent 권한분리 파이프라인 패키지.

ADR-0018의 후속 재설계(2026-05-19). 외부에서 사용하는 진입점만 노출한다.

핵심 흐름:
    UserQuestion
        -> DialogueAgent.decide()         -> DialogueIntent
        -> EntityResolverAgent.resolve()  -> EntityResolution
        -> SearchPlannerAgent.plan()      -> SearchPlan (N SearchTask)
        -> RetrievalAgent.execute()       -> SearchResult (List[CanonicalEvidence])
        -> EvidenceCuratorAgent.curate()  -> EvidenceBundle
        -> AnswerAgent.generate()         -> AnswerDraft
        -> CriticAgent.critique()         -> GuardDecision (publish | repair | clarify | error)
"""

from apps.pipeline.agent_state import AgentPipelineState
from apps.pipeline.agent_workflow import (
    AgentPipelineDeps,
    build_agent_pipeline_graph,
)
from apps.pipeline.agents import (
    AnswerAgent,
    AnswerDraft,
    CriticAgent,
    DialogueAgent,
    DialogueIntent,
    EntityResolution,
    EntityResolverAgent,
    EvidenceBundle,
    EvidenceCuratorAgent,
    GuardDecision,
    RetrievalAgent,
    SearchPlan,
    SearchPlannerAgent,
    SessionState,
    SessionStateAdapter,
)
from apps.pipeline.contracts import (
    CanonicalEvidence,
    FilterBundle,
    IdentifierBundle,
    ReferenceItem,
    ReferenceManifest,
    SearchResult,
    SearchResultStatus,
    SearchStrategy,
    SearchTask,
    SubjectAnchor,
)

__all__ = [
    # 7-agent 워크플로우
    "AgentPipelineDeps",
    "AgentPipelineState",
    "build_agent_pipeline_graph",
    # 에이전트
    "AnswerAgent",
    "CriticAgent",
    "DialogueAgent",
    "EntityResolverAgent",
    "EvidenceCuratorAgent",
    "RetrievalAgent",
    "SearchPlannerAgent",
    # 계약 모델
    "AnswerDraft",
    "CanonicalEvidence",
    "DialogueIntent",
    "EntityResolution",
    "EvidenceBundle",
    "FilterBundle",
    "GuardDecision",
    "IdentifierBundle",
    "ReferenceItem",
    "ReferenceManifest",
    "SearchPlan",
    "SearchResult",
    "SearchResultStatus",
    "SearchStrategy",
    "SearchTask",
    "SessionState",
    "SessionStateAdapter",
    "SubjectAnchor",
]
