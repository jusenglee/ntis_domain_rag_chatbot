from __future__ import annotations

import os


def env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    """Read an integer environment value with a bounded fallback."""

    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if min_value is not None:
        value = max(min_value, value)
    return value


def agentic_max_steps() -> int:
    """Maximum number of agent tool calls allowed per request."""

    return env_int("AGENTIC_MAX_STEPS", 3, min_value=1)


def agentic_decision_repair_max_attempts() -> int:
    """Maximum number of schema-only AgentDecision repair attempts."""

    return env_int("AGENTIC_DECISION_REPAIR_MAX_ATTEMPTS", 1, min_value=1)
