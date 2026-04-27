from __future__ import annotations

from time import perf_counter
from typing import Any

from apps.retrieval.tools.types import ToolResult, build_tool_result


def fetch_project_detail(
    pjt_id: str,
    target_cols: list[str],
    collaborators: dict[str, Any],
) -> ToolResult:
    tool_name = "fetch_project_detail"
    project_id = str(pjt_id or "").strip()
    if not project_id:
        return ToolResult(tool_name=tool_name, status="empty")

    lookup_fn = collaborators.get("lookup_project_detail")
    if not callable(lookup_fn):
        raise ValueError("PROJECT_TOOL_COLLABORATOR_MISSING:lookup_project_detail")

    started_at = perf_counter()
    raw_result = lookup_fn(pjt_id=project_id, target_cols=list(target_cols or []))
    latency_ms = (perf_counter() - started_at) * 1000.0
    return build_tool_result(
        tool_name=tool_name,
        raw_result=raw_result,
        latency_ms=latency_ms,
        default_render_profile={"context_kind": "project", "name": "detail"},
    )


def search_projects_by_text(
    query: str,
    filters: dict[str, Any],
    limit: int,
    collaborators: dict[str, Any],
) -> ToolResult:
    tool_name = "search_projects_by_text"
    query_text = str(query or "").strip()
    if not query_text:
        return ToolResult(tool_name=tool_name, status="empty")

    search_fn = collaborators.get("search_projects_by_text")
    if not callable(search_fn):
        raise ValueError("PROJECT_TOOL_COLLABORATOR_MISSING:search_projects_by_text")

    started_at = perf_counter()
    raw_result = search_fn(
        query=query_text,
        filters=dict(filters or {}),
        limit=max(1, int(limit or 1)),
    )
    latency_ms = (perf_counter() - started_at) * 1000.0
    return build_tool_result(
        tool_name=tool_name,
        raw_result=raw_result,
        latency_ms=latency_ms,
        default_render_profile={"context_kind": "project", "name": "list"},
    )
