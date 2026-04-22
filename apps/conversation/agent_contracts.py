from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class AgentDecision(BaseModel):
    """Dialogue Agent decision before tool execution."""

    decision_type: Literal["direct_answer", "call_tool", "ask_clarification", "agent_internal_error"]
    tool_name: Optional[str] = None
    tool_args: Dict[str, Any] = Field(default_factory=dict)
    response_text: Optional[str] = None
    clarification_question: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str

    @model_validator(mode="after")
    def _validate_shape(self) -> "AgentDecision":
        if self.decision_type == "call_tool" and not self.tool_name:
            raise ValueError("tool_name is required for call_tool")
        if self.decision_type == "direct_answer" and not str(self.response_text or "").strip():
            raise ValueError("response_text is required for direct_answer")
        if self.decision_type == "ask_clarification" and not str(self.clarification_question or "").strip():
            raise ValueError("clarification_question is required for ask_clarification")
        return self
