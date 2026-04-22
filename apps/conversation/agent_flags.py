from __future__ import annotations

import os
from typing import Any


_TRUTHY = {"1", "true", "yes", "y", "on"}


def env_flag(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() in _TRUTHY


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
    if not flag_name:
        return True
    return env_flag(flag_name, "0")


def agentic_dialogue_enabled(overrides: dict[str, Any] | None = None) -> bool:
    """Return whether the agentic dialogue workflow path is enabled."""

    if isinstance(overrides, dict):
        for key in ("AGENTIC_DIALOGUE_ENABLED", "agentic_dialogue_enabled"):
            if key in overrides:
                value = overrides.get(key)
                if isinstance(value, bool):
                    return value
                return str(value).strip().lower() in _TRUTHY
    return env_flag("AGENTIC_DIALOGUE_ENABLED", "0")
