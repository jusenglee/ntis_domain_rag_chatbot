from __future__ import annotations

from typing import Any, Iterable

from apps.conversation.agent_contracts import AgentDecision
from apps.conversation.agent_tools import AgentToolSpec


class AgentDecisionValidationError(ValueError):
    """Repairable validation failure for a model-produced AgentDecision."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "tool_schema_error",
        validation_errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.validation_errors = validation_errors or [{"msg": message, "type": error_type}]


def _is_empty_required_value(value: Any) -> bool:
    return value in (None, "", [], {})


def _matches_json_schema_type(value: Any, schema_type: Any) -> bool:
    if schema_type is None or value is None:
        return True
    allowed = schema_type if isinstance(schema_type, list) else [schema_type]
    if "null" in allowed and value is None:
        return True
    checks = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "array": lambda item: isinstance(item, list),
        "object": lambda item: isinstance(item, dict),
    }
    return any(checks[kind](value) for kind in allowed if kind in checks)


def _validate_tool_args(decision: AgentDecision, spec: AgentToolSpec) -> None:
    schema = dict(spec.input_schema or {})
    properties = dict(schema.get("properties") or {})
    required = list(schema.get("required") or [])
    args = dict(decision.tool_args or {})
    errors: list[dict[str, Any]] = []

    if schema.get("additionalProperties") is False:
        for key in sorted(set(args) - set(properties)):
            errors.append(
                {
                    "loc": ["tool_args", key],
                    "msg": "extra field is not permitted by the tool schema",
                    "type": "tool_schema_error.extra_field",
                }
            )

    for key in required:
        if _is_empty_required_value(args.get(key)):
            errors.append(
                {
                    "loc": ["tool_args", key],
                    "msg": "required tool argument is missing",
                    "type": "tool_schema_error.missing_required",
                }
            )

    for key, value in args.items():
        prop = dict(properties.get(key) or {})
        if not prop:
            continue
        if not _matches_json_schema_type(value, prop.get("type")):
            errors.append(
                {
                    "loc": ["tool_args", key],
                    "msg": f"expected JSON schema type {prop.get('type')}",
                    "type": "tool_schema_error.type",
                }
            )
        enum_values = prop.get("enum")
        if enum_values is not None and value not in (None, "") and value not in enum_values:
            errors.append(
                {
                    "loc": ["tool_args", key],
                    "msg": f"value must be one of {enum_values}",
                    "type": "tool_schema_error.enum",
                }
            )

    if errors:
        raise AgentDecisionValidationError(
            f"tool args failed schema validation for {spec.name}",
            error_type="tool_schema_error",
            validation_errors=errors,
        )


def validate_agent_decision(
    decision: AgentDecision,
    *,
    available_tools: Iterable[AgentToolSpec],
) -> AgentDecision:
    """Validate repairable tool-selection errors without changing the decision intent."""

    if decision.decision_type != "call_tool":
        return decision

    tool_map = {tool.name: tool for tool in available_tools}
    tool_name = str(decision.tool_name or "").strip()

    spec = tool_map.get(tool_name)
    if spec is None:
        raise AgentDecisionValidationError(
            f"unknown agent tool: {tool_name or 'empty'}",
            error_type="unknown_tool",
            validation_errors=[
                {
                    "loc": ["tool_name"],
                    "msg": f"unknown agent tool: {tool_name or 'empty'}",
                    "type": "unknown_tool",
                }
            ],
        )

    if not bool(spec.implemented):
        return decision

    _validate_tool_args(decision, spec)
    return decision
