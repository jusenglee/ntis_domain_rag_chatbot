from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResultItem:
    canonical: dict[str, Any]
    display: dict[str, Any]
    raw_hit: Any | None = None


@dataclass(frozen=True)
class ProjectedItem:
    projection_item_id: str
    projection_id: str
    request_id: str
    rank: int
    entity_kind: str
    display_title: str
    canonical_title: str
    ids_map: dict[str, list[str]]
    identity_key: str
    source_doc_id: str | None = None
    source_type: str | None = None
    publishable: bool = True


@dataclass(frozen=True)
class EvidenceProjectionBundle:
    projection_id: str
    request_id: str
    turn_id: str
    context_kind: str
    output_type: str
    display_source: str
    display_documents: list[dict[str, Any]] = field(default_factory=list)
    canonical_evidence: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    projected_items: list[ProjectedItem] = field(default_factory=list)
    answer_context_text: str = ""
    debug_answer_context_text: str = ""
    render_profile: dict[str, Any] = field(default_factory=dict)
    requested_count: int = 0
    selected_count: int = 0
    manifest_count: int = 0
    rendered_count: int = 0


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
    references: list[dict[str, Any]] = field(default_factory=list)
