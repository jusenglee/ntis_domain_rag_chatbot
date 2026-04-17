from __future__ import annotations

from time import perf_counter
from typing import Any

from apps.retrieval.tools.types import ToolResult, build_tool_result


def fetch_project_performance(
    pjt_ids: list[str],
    join_key_mode: str,
    collaborators: dict[str, Any],
) -> ToolResult:
    tool_name = "fetch_project_performance"
    normalized_ids = [str(value).strip() for value in (pjt_ids or []) if str(value).strip()]
    if not normalized_ids:
        return ToolResult(
            tool_name=tool_name,
            status="empty",
            diagnostics={"reason": "missing_project_ids"},
        )

    relation_fn = collaborators.get("fetch_project_performance")
    if not callable(relation_fn):
        raise ValueError("RELATION_TOOL_COLLABORATOR_MISSING:fetch_project_performance")

    started_at = perf_counter()
    raw_result = relation_fn(pjt_ids=normalized_ids, join_key_mode=str(join_key_mode or "").strip().lower())
    latency_ms = (perf_counter() - started_at) * 1000.0
    return build_tool_result(
        tool_name=tool_name,
        raw_result=raw_result,
        latency_ms=latency_ms,
        default_render_profile={"context_kind": "perf", "name": "relation"},
    )
