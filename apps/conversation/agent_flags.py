from __future__ import annotations

import os

def env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = str(os.getenv(name, str(default)) or "").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"{name} must be >= {min_value}") from None
    return value


def agent_tool_enabled(flag_name: str | None) -> bool:
    """Return whether an implemented agent tool can execute.

    Agentic dialogue is now the normal front-controller path, so implemented
    tools are not runtime-gated by rollout flags. Tools that are not ready must
    stay declared with ``implemented=False`` in the registry.
    """

    return True


def agentic_max_steps() -> int:
    """Return the reserved agent loop guard threshold for tool execution."""

    return env_int("AGENTIC_MAX_STEPS", 3, min_value=1)
