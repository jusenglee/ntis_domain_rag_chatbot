from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentToolSpec(BaseModel):
    """Tool contract exposed to the Dialogue Agent."""

    name: str
    description: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    implemented: bool = True


def _schema(properties: Dict[str, Any], *, required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required or []),
        "additionalProperties": False,
    }


def default_agent_tool_specs(*, implemented_only: bool = False) -> List[AgentToolSpec]:
    """Return the agent tool registry. Availability is not feature-flagged."""

    specs = [
        AgentToolSpec(
            name="search_ntis_domain",
            description=(
                "Start a new NTIS domain search for projects, performance, people, "
                "organizations, or support documents. Use this when the user names "
                "a new explicit target or asks a new generic NTIS data question. "
                "Do not use this for explicit people/org activity-history requests."
            ),
            input_schema=_schema(
                {
                    "query": {"type": "string", "description": "Natural-language search query"},
                    "domain_head": {
                        "type": "string",
                        "enum": ["project", "perf", "people", "org", "support", "auto"],
                        "description": "Target NTIS domain. Use auto when uncertain.",
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
            name="search_subject_activity",
            description=(
                "Lookup activity history for one explicit researcher or organization. "
                "Use this for named people/org activity records, participation history, "
                "projects, and performance history."
            ),
            input_schema=_schema(
                {
                    "subject_kind": {"type": "string", "enum": ["people", "org"]},
                    "subject_name": {"type": "string"},
                    "affiliation_org_name": {"type": ["string", "null"]},
                    "year_from": {"type": ["integer", "null"]},
                    "year_to": {"type": ["integer", "null"]},
                    "role": {"type": ["string", "null"], "enum": ["연구책임자", "참여연구원"]},
                    "target": {
                        "type": "string",
                        "enum": ["project", "perf", "both"],
                    },
                    "limit": {"type": ["integer", "null"]},
                    "query": {"type": ["string", "null"]},
                },
                required=["subject_kind", "subject_name"],
            ),
        ),
        AgentToolSpec(
            name="refine_current_subject",
            description=(
                "Refine the current conversation subject with additional filters "
                "such as period, role, performance type, target, ordering, or count."
            ),
            input_schema=_schema(
                {
                    "subject_ref": {"type": "string", "enum": ["current_subject"]},
                    "year_from": {"type": ["integer", "null"]},
                    "year_to": {"type": ["integer", "null"]},
                    "role": {"type": ["string", "null"]},
                    "perf_type": {"type": ["string", "null"]},
                    "target": {
                        "type": "string",
                        "enum": ["project", "perf", "activity_history", "both"],
                    },
                    "limit": {"type": ["integer", "null"]},
                },
                required=["subject_ref"],
            ),
        ),
        AgentToolSpec(
            name="lookup_specific_entity",
            description="Lookup one specific entity by an existing observation or memory reference.",
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
            description="Trace legal project-performance relationships through the contract backend.",
            implemented=False,
            input_schema=_schema(
                {
                    "direction": {"type": "string", "enum": ["project_to_perf", "perf_to_project"]},
                    "anchor_ref": {"type": "string"},
                    "perf_type": {"type": ["string", "null"]},
                },
                required=["direction", "anchor_ref"],
            ),
        ),
        AgentToolSpec(
            name="ask_user_for_clarification",
            description="Ask the user for missing information or disambiguation.",
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


def tool_spec_by_name() -> Dict[str, AgentToolSpec]:
    return {spec.name: spec for spec in default_agent_tool_specs()}
