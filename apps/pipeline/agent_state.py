"""Phase 10: 7-agent 재설계의 LangGraph 상태 컨테이너.

기존 PipelineState는 JudgmentDecision / SearchTask / FinalAnswer를 기준으로 만들어졌다.
7-agent flow는 그 자리에 DialogueIntent / EntityResolution / SearchPlan / EvidenceBundle /
AnswerDraft / GuardDecision을 둔다.

각 노드는 자기 책임 범위의 필드만 채운다:
    DialogueAgent      → dialogue_intent
    EntityResolver     → entity_resolution
    SearchPlanner      → search_plan
    RetrievalAgent     → search_result
    EvidenceCurator    → evidence_bundle
    AnswerAgent        → answer_draft
    CriticAgent        → guard_decision (publish 시 reference_manifest)
    emit_*             → answer_artifact / final_answer_text
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from apps.api.streaming.contracts import AnswerArtifact
from apps.api.streaming.emitter import AsyncStreamEmitter
from apps.conversation.session_memory import SessionMemory
from apps.platform.langchain_compat import BaseMessage
from apps.platform.storage import KVStore
from apps.pipeline.agents.contracts import (
    AnswerDraft,
    DialogueIntent,
    EntityResolution,
    EvidenceBundle,
    GuardDecision,
    SearchPlan,
)
from apps.pipeline.agents.planner_contracts import PlanState
from apps.pipeline.agents.session_state import SessionState
from apps.pipeline.contracts import SearchResult


try:
    from langgraph.graph.message import add_messages
except ModuleNotFoundError:
    def add_messages(
        left: Optional[List[BaseMessage]],
        right: Optional[List[BaseMessage]],
    ) -> List[BaseMessage]:
        return list(left or []) + list(right or [])


def _merge_latencies(existing: Dict[str, float], new: Dict[str, float]) -> Dict[str, float]:
    out = dict(existing)
    out.update(new or {})
    return out


class AgentPipelineState(BaseModel):
    """7-agent flow가 노드 간 전달하는 단일 상태 컨테이너."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ── 입력 ───────────────────────────────────────────────────────────────
    messages: Annotated[List[BaseMessage], add_messages] = Field(default_factory=list)
    question: str = ""
    conversation_id: str = ""
    request_id: str = ""
    turn_id: str = ""
    request_started_at: Optional[float] = None
    request_overrides: Dict[str, Any] = Field(default_factory=dict)

    # ── 환경 ───────────────────────────────────────────────────────────────
    kv_store: Optional[KVStore] = None
    session_memory: SessionMemory = Field(default_factory=SessionMemory)
    session_state: SessionState = Field(default_factory=SessionState)
    stream_emitter: Optional[AsyncStreamEmitter] = None

    # ── 단계별 산출물 ─────────────────────────────────────────────────────
    dialogue_intent: Optional[DialogueIntent] = None
    entity_resolution: Optional[EntityResolution] = None
    search_plan: Optional[SearchPlan] = None
    search_result: Optional[SearchResult] = None
    evidence_bundle: Optional[EvidenceBundle] = None
    answer_draft: Optional[AnswerDraft] = None
    # 비교 답변 초안 (이중 모델 출력). 메인=Solar(answer_draft), 보조=Gemma(secondary_answer_draft).
    # 보조는 프론트 패널 B로만 스트리밍되며 Critic 검증·세션·references 발행 대상이 아니다 (원문 비교용).
    secondary_answer_draft: Optional[AnswerDraft] = None
    guard_decision: Optional[GuardDecision] = None

    # ── 발행 산출물 ──────────────────────────────────────────────────────
    answer_artifact: Optional[AnswerArtifact] = None
    final_answer_text: str = ""

    # ── 운영 메타 ────────────────────────────────────────────────────────
    latencies: Annotated[Dict[str, float], _merge_latencies] = Field(default_factory=dict)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    # ── 재시도 가드 ──────────────────────────────────────────────────────
    repair_attempted: bool = False   # CriticAgent의 repair_answer는 1회만 허용

    # ── Phase 3 agentic loop 상태 ────────────────────────────────────────
    # RAG_AGENTIC_MODE=true일 때만 사용. PlannerAgent + ToolExecutor가 누적 갱신.
    plan_state: Optional[PlanState] = None

    def total_ms(self) -> float:
        if self.request_started_at is None:
            return 0.0
        return (time.perf_counter() - self.request_started_at) * 1000.0
