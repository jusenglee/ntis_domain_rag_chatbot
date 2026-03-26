from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


@dataclass(frozen=True)
class ResolvedEntityRef:
    entity_kind: Literal["project", "perf", "people", "org"]
    seed_map: dict[str, list[str]]
    source: Literal[
        "display_snapshot",
        "detail_lookup",
        "reference_context_ordinal",
        "reference_context_deictic",
        "explicit_id",
    ]
    display_view_id: Optional[str] = None
    display_rank: Optional[int] = None
    anchor_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClarificationRequest:
    clarification_type: str
    message: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    resume_token: dict[str, Any] = field(default_factory=dict)
