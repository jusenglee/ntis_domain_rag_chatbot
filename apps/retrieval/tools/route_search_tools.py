from __future__ import annotations

from time import perf_counter
from typing import Any

from apps.retrieval.tools.types import ToolResult, build_tool_result


def search_route_by_text(
    base_route: str,
    query: str,
    filters: dict[str, Any],
    limit: int,
    target_cols: list[str],
    collaborators: dict[str, Any],
) -> ToolResult:
    tool_name = "search_route_by_text"
    route = str(base_route or "").strip().lower()
    query_text = str(query or "").strip()
    if not route or not query_text:
        return ToolResult(tool_name=tool_name, status="empty")

    search_fn = collaborators.get("search_route_by_text")
    if not callable(search_fn):
        raise ValueError("ROUTE_SEARCH_TOOL_COLLABORATOR_MISSING:search_route_by_text")

    started_at = perf_counter()
    raw_result = search_fn(
        base_route=route,
        query=query_text,
        filters=dict(filters or {}),
        limit=max(1, int(limit or 1)),
        target_cols=list(target_cols or []),
    )
    latency_ms = (perf_counter() - started_at) * 1000.0
    return build_tool_result(
        tool_name=tool_name,
        raw_result=raw_result,
        latency_ms=latency_ms,
        default_render_profile={"context_kind": route, "name": "list"},
    )
