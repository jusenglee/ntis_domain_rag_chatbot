"""7-agent 재설계 패키지 (NTIS RAG Agentic Redesign).

설계 원칙:
    - 각 에이전트는 단일 책임을 갖고 Pydantic frozen 계약만 주고받는다.
    - LLM은 의도 분류와 답변 생성에만 사용. 식별자 확정/manifest 해소는 deterministic.
    - SearchTask, CanonicalEvidence, FinalAnswer, ReferenceManifest는 기존 그대로 재사용.

흐름:
    load_session
        → DialogueAgent       — 사용자 의도 분류 (DialogueIntent)
        → EntityResolver      — 식별자 해소 (EntityResolution)
        → SearchPlanner       — 실행 계획 (SearchPlan = List[SearchTask])
        → RetrievalAgent      — Qdrant 호출 (List[CanonicalEvidence])
        → EvidenceCurator     — 답변용 근거 정리 (EvidenceBundle)
        → AnswerAgent         — LLM 답변 (AnswerDraft)
        → CriticAgent         — 검증 (GuardDecision: publish/clarify/repair/internal_error)
    → save_session
"""

from apps.pipeline.agents.contracts import (
    AnswerDraft,
    Citation,
    CompareTarget,
    DialogueIntent,
    DialogueKind,
    EntityResolution,
    EvidenceBundle,
    EvidenceGroup,
    EvidenceView,
    GuardDecision,
    GuardDecisionKind,
    IdentityCandidate,
    SearchPlan,
)
from apps.pipeline.agents.answer_agent import AnswerAgent
from apps.pipeline.agents.critic_agent import CriticAgent
from apps.pipeline.agents.dialogue_agent import DialogueAgent
from apps.pipeline.agents.entity_resolver import EntityResolverAgent
from apps.pipeline.agents.evidence_curator import EvidenceCuratorAgent
from apps.pipeline.agents.retrieval_agent import RetrievalAgent
from apps.pipeline.agents.search_planner import SearchPlannerAgent
from apps.pipeline.agents.session_state import (
    FocusedDetailSlot,
    ManifestSlot,
    SessionState,
    SessionStateAdapter,
    SubjectSlot,
)

__all__ = [
    "AnswerAgent",
    "AnswerDraft",
    "Citation",
    "CompareTarget",
    "CriticAgent",
    "DialogueAgent",
    "DialogueIntent",
    "DialogueKind",
    "EntityResolution",
    "EntityResolverAgent",
    "EvidenceBundle",
    "EvidenceCuratorAgent",
    "EvidenceGroup",
    "EvidenceView",
    "FocusedDetailSlot",
    "GuardDecision",
    "GuardDecisionKind",
    "IdentityCandidate",
    "ManifestSlot",
    "RetrievalAgent",
    "SearchPlan",
    "SearchPlannerAgent",
    "SessionState",
    "SessionStateAdapter",
    "SubjectSlot",
]
