"""SSE 스트림 계약.

레거시의 ClarificationRequest 의존을 제거하고 pipeline.contracts.Clarification에 직접 의존한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


@dataclass(frozen=True)
class ErrorArtifact:
    """답변 발행 시 사용자에게 노출되는 에러 메타."""

    error_code: str
    reason: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StreamEvent:
    """SSE에 publish되는 단일 이벤트."""

    kind: Literal[
        "conversation",
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
    """라우트가 최종 응답 payload로 노출하는 객체.

    pipeline.workflow에서 채워지며 routes.py가 SSE 마지막 frame과 /query JSON 응답에 사용한다.
    """

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
    source_refs: list = field(default_factory=list)
    visible_answer_manifest: Optional[dict[str, Any]] = None
    visible_answer_manifest_publication: Optional[dict[str, Any]] = None
    error: Optional[ErrorArtifact] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_meta_dict(self) -> dict[str, Any]:
        """SSE 마지막 frame meta payload로 변환."""
        data = dict(self.stream_metrics or {})
        data.update(self.meta or {})
        data["answer_kind"] = self.answer_kind
        data["user_visible_final_required"] = bool(self.user_visible_final_required)
        if isinstance(self.visible_answer_manifest, dict):
            data["visible_answer_manifest"] = dict(self.visible_answer_manifest)
        if isinstance(self.visible_answer_manifest_publication, dict):
            data["visible_answer_manifest_publication"] = dict(
                self.visible_answer_manifest_publication
            )
        return data
