"""3계층 파이프라인의 LangGraph 상태 컨테이너.

기존 AgentState(70+ 필드)를 대체한다. 본 PipelineState는 단계별 산출물만 들고 가며,
routes.py가 final_state에서 읽는 호환 필드(answer_artifact, context, latencies 등)를
property로 노출한다.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from apps.api.streaming.contracts import AnswerArtifact
from apps.api.streaming.emitter import AsyncStreamEmitter
from apps.platform.langchain_compat import BaseMessage
from apps.platform.storage import KVStore
from apps.conversation.session_memory import SessionMemory
from apps.pipeline.contracts import (
    FinalAnswer,
    GeneratedAnswer,
    JudgmentDecision,
    SearchResult,
    SearchTask,
)


try:
    from langgraph.graph.message import add_messages
except ModuleNotFoundError:
    def add_messages(
        left: Optional[List[BaseMessage]],
        right: Optional[List[BaseMessage]],
    ) -> List[BaseMessage]:
        """langgraph 부재 시의 fallback merger."""
        return list(left or []) + list(right or [])


def _merge_latencies(existing: Dict[str, float], new: Dict[str, float]) -> Dict[str, float]:
    """노드별 latency dict 병합."""
    out = dict(existing)
    out.update(new or {})
    return out


class PipelineState(BaseModel):
    """LangGraph가 노드 간 전달하는 단일 상태 컨테이너.

    각 노드는 자기 책임 범위의 필드만 채운다.
    JudgmentAgent → judgment / search_task
    SearchAgent   → search_result
    LLMGenerator  → generated
    FinalGuard    → final_answer / answer_artifact
    """

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
    stream_emitter: Optional[AsyncStreamEmitter] = None

    # ── JudgmentAgent 산출물 ──────────────────────────────────────────────
    judgment: Optional[JudgmentDecision] = None
    search_task: Optional[SearchTask] = None       # judgment.search_task의 빠른 접근용 alias

    # ── SearchAgent 산출물 ───────────────────────────────────────────────
    search_result: Optional[SearchResult] = None

    # ── LLM 생성 산출물 ───────────────────────────────────────────────────
    generated: Optional[GeneratedAnswer] = None

    # ── FinalGuard 산출물 ────────────────────────────────────────────────
    final_answer: Optional[FinalAnswer] = None

    # ── 발행 산출물 (routes.py가 읽는 호환 필드) ────────────────────────
    answer_artifact: Optional[AnswerArtifact] = None
    final_answer_text: str = ""

    # ── 운영 메타 ─────────────────────────────────────────────────────────
    latencies: Annotated[Dict[str, float], _merge_latencies] = Field(default_factory=dict)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    # ── 재시도 가드 ──────────────────────────────────────────────────────
    refine_attempted: bool = False  # SearchResult=multiple일 때 1회만 refine 허용

    # ----------------------------------------------------------------------
    # routes.py 호환 read-only 뷰
    # ----------------------------------------------------------------------

    @property
    def context(self) -> List[Dict[str, Any]]:
        """기존 routes.py가 documents_used 카운트로 쓰던 필드 호환."""
        if self.search_result is None:
            return []
        return [
            {
                "rank": ev.snapshot_rank,
                "identity": ev.identity,
                "title": ev.title,
                "ids": dict(ev.ids),
                "source_type": ev.source_type,
            }
            for ev in self.search_result.evidences
        ]

    def total_ms(self) -> float:
        """요청 시작부터 현재까지의 경과 시간(ms)."""
        if self.request_started_at is None:
            return 0.0
        return (time.perf_counter() - self.request_started_at) * 1000.0
