from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from apps.conversation.entity_reference import ClarificationRequest


@dataclass(frozen=True)
class ErrorArtifact:
    error_code: str
    reason: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StreamEvent:
    kind: Literal[
        "conversation",
        "status",
        "answer.chunk",
        "reference.set",
        "done",
    ]
    request_id: str
    seq: int = 0
    model_key: Optional[str] = None
    content: Optional[str] = None
    references: Optional[list[dict[str, Any]]] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AnswerArtifact:
    text: str
    answer_kind: Literal[
        "llm_streamed",
        "llm_collected",
        "detail_cache",
        "detail_profile",
        "clarification",
        "no_result",
        "direct_answer",
        "error",
    ]
    stream_metrics: dict[str, Any] = field(default_factory=dict)
    user_visible_final_required: bool = True
    references: list[dict[str, Any]] = field(default_factory=list)
    # SSOT 출처. Stage 4 도입. references는 deprecate, source_refs로 교체 예정.
    source_refs: list = field(default_factory=list)
    visible_answer_manifest: Optional[dict[str, Any]] = None
    visible_answer_manifest_publication: Optional[dict[str, Any]] = None
    clarification: Optional[ClarificationRequest] = None
    error: Optional[ErrorArtifact] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_meta_dict(self) -> dict[str, Any]:
        data = dict(self.stream_metrics or {})
        data.update(self.meta or {})
        data["answer_kind"] = self.answer_kind
        data["user_visible_final_required"] = bool(self.user_visible_final_required)
        if isinstance(self.visible_answer_manifest, dict):
            data["visible_answer_manifest"] = dict(self.visible_answer_manifest)
        if isinstance(self.visible_answer_manifest_publication, dict):
            data["visible_answer_manifest_publication"] = dict(self.visible_answer_manifest_publication)
        return data
