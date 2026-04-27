from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _normalize_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return records
    for item in value:
        if isinstance(item, dict):
            records.append(dict(item))
            continue
        if hasattr(item, "model_dump"):
            try:
                records.append(dict(item.model_dump()))
                continue
            except Exception:
                pass
        payload = dict(getattr(item, "__dict__", {}) or {})
        if payload:
            records.append(payload)
    return records


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    status: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    canonical_evidence: list[dict[str, Any]] = field(default_factory=list)
    render_profile: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0


def build_tool_result(
    *,
    tool_name: str,
    raw_result: Any,
    latency_ms: float,
    default_render_profile: dict[str, Any] | None = None,
) -> ToolResult:
    rows: list[dict[str, Any]] = []
    canonical_evidence: list[dict[str, Any]] = []
    render_profile: dict[str, Any] = dict(default_render_profile or {})
    diagnostics: dict[str, Any] = {}

    if isinstance(raw_result, dict):
        rows = _normalize_records(raw_result.get("documents") or raw_result.get("rows") or [])
        canonical_evidence = _normalize_records(
            raw_result.get("canonical_evidence") or raw_result.get("canonical") or []
        )
        if not canonical_evidence and rows:
            canonical_evidence = list(rows)
        render_profile = dict(raw_result.get("render_profile") or default_render_profile or {})
        for key in (
            "clarification",
            "no_result_message",
            "actual_retrieval_query",
            "answer_context_text",
            "debug_answer_context_text",
            "raw_result_count",
            "context_source",
        ):
            if key in raw_result:
                diagnostics[key] = raw_result.get(key)
        if isinstance(raw_result.get("diagnostics"), dict):
            diagnostics.update(dict(raw_result.get("diagnostics") or {}))
    else:
        rows = _normalize_records(raw_result)
        canonical_evidence = list(rows)

    status = "success"
    if diagnostics.get("clarification") and not rows and not canonical_evidence:
        status = "clarification"
    elif not rows and not canonical_evidence:
        status = "empty"

    return ToolResult(
        tool_name=tool_name,
        status=status,
        rows=rows,
        canonical_evidence=canonical_evidence,
        render_profile=render_profile,
        diagnostics=diagnostics,
        latency_ms=float(latency_ms),
    )
