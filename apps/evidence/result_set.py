from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResultItem:
    canonical: dict[str, Any]
    display: dict[str, Any]
    raw_hit: Any | None = None


@dataclass(frozen=True)
class RetrievalBundle:
    items: list[ResultItem] = field(default_factory=list)
    render_profile: dict[str, Any] = field(default_factory=dict)
    raw_count: int = 0
    context_kind: str = "project"
    no_result_message: str | None = None
    clarification: dict[str, Any] | None = None
    answer_context_text: str = ""
    debug_answer_context_text: str = ""
    context_source: str = "pipeline_context"
