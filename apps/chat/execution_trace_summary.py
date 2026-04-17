from __future__ import annotations

from typing import Any, Optional


def summarize_execution_trace(execution_trace: Any) -> dict[str, Any]:
    if not isinstance(execution_trace, list) or not execution_trace:
        return {}

    recovery_steps: list[dict[str, Any]] = []
    recovery_policy: Optional[str] = None
    recovery_user_notice: Optional[str] = None

    for entry in execution_trace:
        if not isinstance(entry, dict):
            continue

        phase = str(entry.get("phase") or "").strip().lower()
        if phase != "secondary":
            continue

        status = str(entry.get("status") or "").strip().lower()
        if status != "success":
            continue

        recovery_policy = recovery_policy or (str(entry.get("policy") or "").strip() or None)
        recovery_user_notice = recovery_user_notice or (str(entry.get("user_notice") or "").strip() or None)
        recovery_steps.append(
            {
                "step": entry.get("step"),
                "tool_name": entry.get("tool_name"),
                "status": entry.get("status"),
                "row_count": entry.get("row_count"),
            }
        )

    if not recovery_steps:
        return {}

    return {
        "recovery_applied": True,
        "recovery_policy": recovery_policy,
        "recovery_steps": recovery_steps,
        "recovery_user_notice": recovery_user_notice,
    }
