from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentToolSpec(BaseModel):
    """Tool description exposed to the dialogue agent."""

    name: str
    description: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    implemented: bool = True
    feature_flag: Optional[str] = None


def _schema(properties: Dict[str, Any], *, required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required or []),
        "additionalProperties": False,
    }


def default_agent_tool_specs(*, implemented_only: bool = False) -> List[AgentToolSpec]:
    """Return the agent tool registry declared by ADR-0001."""

    specs = [
        AgentToolSpec(
            name="search_ntis_domain",
            description=(
                "Start a new NTIS domain search. The tool adapter must pass the request through "
                "planner and contract validation before retrieval."
            ),
            feature_flag="AGENTIC_TOOL_SEARCH_ENABLED",
            input_schema=_schema(
                {
                    "query": {"type": "string"},
                    "domain_head": {
                        "type": "string",
                        "enum": ["project", "perf", "people", "org", "support", "auto"],
                    },
                    "year_from": {"type": ["integer", "null"]},
                    "year_to": {"type": ["integer", "null"]},
                    "people_name": {"type": ["string", "null"]},
                    "org_name": {"type": ["string", "null"]},
                    "perf_type": {"type": ["string", "null"]},
                    "limit": {"type": ["integer", "null"]},
                },
                required=["query"],
            ),
        ),
        AgentToolSpec(
            name="refine_current_subject",
            description=(
                "Reuse SessionMemory.current_context subject and add constraints such as year, role, "
                "performance type, target, or limit."
            ),
            feature_flag="AGENTIC_TOOL_REFINE_SUBJECT_ENABLED",
            input_schema=_schema(
                {
                    "subject_ref": {"type": "string", "enum": ["current_subject"]},
                    "year_from": {"type": ["integer", "null"]},
                    "year_to": {"type": ["integer", "null"]},
                    "role": {"type": ["string", "null"]},
                    "perf_type": {"type": ["string", "null"]},
                    "target": {
                        "type": "string",
                        "enum": ["project", "perf", "activity_history", "auto"],
                    },
                    "limit": {"type": ["integer", "null"]},
                },
                required=["subject_ref"],
            ),
        ),
        AgentToolSpec(
            name="lookup_specific_entity",
            description="Lookup a specific entity by manifest reference or explicit supported ID.",
            feature_flag="AGENTIC_TOOL_LOOKUP_ENABLED",
            implemented=False,
            input_schema=_schema(
                {
                    "entity_ref": {"type": "string"},
                    "entity_kind": {"type": "string", "enum": ["project", "perf", "people", "org", "auto"]},
                    "detail_level": {"type": "string", "enum": ["summary", "detail"]},
                },
                required=["entity_ref"],
            ),
        ),
        AgentToolSpec(
            name="join_project_perf",
            description="Run a contract-guarded project/performance JOIN.",
            feature_flag="AGENTIC_TOOL_JOIN_ENABLED",
            implemented=False,
            input_schema=_schema(
                {
                    "direction": {"type": "string", "enum": ["project_to_perf", "perf_to_project"]},
                    "anchor_ref": {"type": "string"},
                    "perf_type": {"type": ["string", "null"]},
                    "year_from": {"type": ["integer", "null"]},
                    "year_to": {"type": ["integer", "null"]},
                },
                required=["direction", "anchor_ref"],
            ),
        ),
        AgentToolSpec(
            name="ask_user_for_clarification",
            description="Ask a natural clarification only when target identity or contract axis is unsafe.",
            feature_flag="AGENTIC_CLARIFICATION_ENABLED",
            input_schema=_schema(
                {
                    "reason": {"type": "string"},
                    "question_to_user": {"type": "string"},
                    "suggested_options": {"type": "array", "items": {"type": "string"}},
                },
                required=["reason", "question_to_user"],
            ),
        ),
    ]
    if implemented_only:
        return [spec for spec in specs if spec.implemented]
    return specs


def tool_spec_by_name(*, implemented_only: bool = False) -> Dict[str, AgentToolSpec]:
    return {spec.name: spec for spec in default_agent_tool_specs(implemented_only=implemented_only)}
