"""Phase 2 — PlannerAgent ↔ ToolExecutor 사이의 agentic loop 계약.

PlannerAgent는 사용자 질문 + 현재까지 누적된 Observations를 보고 다음 step을 결정한다.
결정 옵션:
    - action="call_tool" : 특정 도구 호출 (tool/args 채움)
    - action="answer"    : 답변 생성 단계로 진입 (LLM 답변 텍스트 또는 AnswerAgent 위임)
    - action="clarify"   : 사용자에게 되묻기

PlanState는 한 turn 동안 누적되는 step·observation·decision 시퀀스. ToolExecutor 호출 후
새 Observation을 append, PlannerAgent 호출 후 PlannerStep을 append, 모두 frozen 모델이라
복사 갱신 패턴(`plan_state.with_observation(obs)`)을 사용한다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.pipeline.tools.contracts import Observation


# ============================================================================
# PlannerStep — 단일 결정
# ============================================================================

PlannerAction = Literal["call_tool", "answer", "clarify"]


class PlannerStep(BaseModel):
    """PlannerAgent가 매 step에서 발행하는 단일 결정.

    action 별 채워야 할 필드:
        call_tool : tool(필수), args(dict)
        answer    : answer_text(선택 — 비우면 후속 AnswerAgent가 생성)
        clarify   : clarification_question(필수)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: PlannerAction
    tool: Optional[str] = None
    args: Dict[str, Any] = Field(default_factory=dict)
    answer_text: Optional[str] = None
    clarification_question: Optional[str] = None
    reason: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_action_payload(self) -> "PlannerStep":
        if self.action == "call_tool" and not self.tool:
            raise ValueError("action=call_tool requires 'tool' field")
        if self.action == "clarify" and not self.clarification_question:
            raise ValueError("action=clarify requires 'clarification_question' field")
        return self


# ============================================================================
# PlanState — 누적 상태
# ============================================================================

TerminationReason = Literal["answer", "clarify", "max_steps", "error", "duplicate_call"]


class PlanState(BaseModel):
    """한 turn의 planner loop 누적 상태.

    각 step:
        1. PlannerAgent.decide_next(plan_state) → PlannerStep
        2. plan_state.with_decision(step)
        3. step.action == "call_tool"이면 ToolExecutor.execute(step.toolcall) → Observation
        4. plan_state.with_observation(obs)
        5. 종료 조건 검사:
            - answer/clarify → terminate
            - step_no >= max_steps → terminate(max_steps)
            - 동일 ToolCall 반복(loop guard) → terminate(duplicate_call)
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str
    step_no: int = Field(default=0, ge=0)
    decisions: List[PlannerStep] = Field(default_factory=list)
    observations: List[Observation] = Field(default_factory=list)
    terminated: bool = False
    termination_reason: Optional[TerminationReason] = None

    def with_decision(self, step: PlannerStep) -> "PlanState":
        """PlannerStep 추가한 새 PlanState 반환 (step_no += 1)."""
        return self.model_copy(update={
            "decisions": [*self.decisions, step],
            "step_no": self.step_no + 1,
        })

    def with_observation(self, obs: Observation) -> "PlanState":
        return self.model_copy(update={
            "observations": [*self.observations, obs],
        })

    def with_termination(self, reason: TerminationReason) -> "PlanState":
        return self.model_copy(update={
            "terminated": True,
            "termination_reason": reason,
        })

    def last_decision(self) -> Optional[PlannerStep]:
        return self.decisions[-1] if self.decisions else None

    def last_observation(self) -> Optional[Observation]:
        return self.observations[-1] if self.observations else None

    def signature_of(self, step: PlannerStep) -> str:
        """동일 도구·동일 args 호출 반복(loop) 감지용 정규화 키."""
        import json
        if step.action != "call_tool":
            return f"{step.action}::"
        try:
            args_key = json.dumps(step.args, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            args_key = str(step.args)
        return f"call_tool::{step.tool}::{args_key}"
