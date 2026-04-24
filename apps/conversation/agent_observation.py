from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class AgentObservation(BaseModel):
    """Compact, safe observation returned by an Agent tool backend."""

    observation_type: Literal[
        "search_results",
        "lookup_result",
        "planned_intent",
        "clarification_required",
        "no_results",
        "contract_violation",
        "error",
    ]
    summary: str
    structured_refs: Dict[str, Any] = Field(default_factory=dict)
    evidence_count: int = 0
    warnings: List[str] = Field(default_factory=list)
    next_suggested_actions: List[str] = Field(default_factory=list)
    # ADR-0015 C1 Option B: 최소 결과 주석 훅 (merge_answers 대안).
    # Tool backend가 Agent에게 전달할 '짧은 프로즈 힌트'. 전략·스코어·구성 요소 금지.
    answer_note: Optional[str] = None


class AgentToolExecutionResult(BaseModel):
    """Agent tool execution result plus guarded planner outputs."""

    observation: AgentObservation
    intent_payload: Optional[Any] = None
    question_analysis: Optional[Any] = None
    next_current_context: Optional[Any] = None

    @property
    def observation_type(self) -> str:
        return self.observation.observation_type

    @property
    def summary(self) -> str:
        return self.observation.summary

    @property
    def structured_refs(self) -> Dict[str, Any]:
        return self.observation.structured_refs

    @property
    def warnings(self) -> List[str]:
        return self.observation.warnings
