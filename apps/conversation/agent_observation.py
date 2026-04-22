from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentObservation(BaseModel):
    """Standard observation returned from agent tool execution."""

    observation_type: Literal[
        "planned_intent",
        "search_results",
        "lookup_result",
        "join_result",
        "clarification_required",
        "no_results",
        "contract_violation",
        "error",
    ]
    summary: str
    structured_refs: Dict[str, Any] = Field(default_factory=dict)
    evidence_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    next_suggested_actions: list[str] = Field(default_factory=list)


class AgentToolExecutionResult(BaseModel):
    """Tool execution wrapper carrying guarded planner outputs when available."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    observation: AgentObservation
    intent_payload: Optional[Any] = None
    question_analysis: Optional[Any] = None

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
    def evidence_count(self) -> int:
        return self.observation.evidence_count

    @property
    def warnings(self) -> list[str]:
        return self.observation.warnings

    @property
    def next_suggested_actions(self) -> list[str]:
        return self.observation.next_suggested_actions
