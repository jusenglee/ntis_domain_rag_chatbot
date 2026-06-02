from __future__ import annotations

from dataclasses import replace
from typing import Any

from apps.api.streaming.contracts import StreamEvent


FRONTEND_MODEL_KEYS: tuple[str, str] = ("solar", "gemma")

# 프론트 패널 매핑 (templates/index.html: model_key==="solar"→패널 A, "gemma"→패널 B).
# 사용자 확정(2026-06-01): A = Solar(vLLM, **메인** 답변), B = Gemma(Triton, 비교 답변).
PRIMARY_FRONTEND_KEY: str = "solar"      # 패널 A — 메인(Solar) 답변, Critic·references·세션 기준
SECONDARY_FRONTEND_KEY: str = "gemma"    # 패널 B — 비교(Gemma) 답변, 검증 없이 원문 표시


def answer_chunk_model_keys_for_frontend(
    *, answer_kind: str | None = None, model_key: str | None = None
) -> tuple[str, str]:
    """Return the public frontend labels for an ``answer.chunk`` frame.

    Runtime model keys such as ``gemma_triton_0`` and deterministic branch
    labels such as ``no_result`` are internal diagnostics. The current browser
    compare UI only accepts ``solar`` and ``gemma``, so every answer chunk fans
    out to both public lanes.
    """

    return FRONTEND_MODEL_KEYS


def stream_events_for_frontend(event: StreamEvent) -> tuple[StreamEvent, ...]:
    """내부 StreamEvent를 프론트가 소비하는 공개 SSE 이벤트(들)로 변환.

    ``answer.chunk`` 라우팅 규칙:
        - model_key가 이미 프론트 레인(``solar``/``gemma``)으로 태깅돼 있으면 **해당
          레인 1개로만** 전달한다. → A=Solar(메인) 답변과 B=Gemma(비교) 답변을 각
          패널에 분리 스트리밍 (두 모델이 서로 다른 답변을 낼 때).
        - 그 외 내부/결정적 키(``gemma_triton_0``, ``solar_vllm_0``, ``no_result``,
          ``clarification``, ``agentic_direct_answer``, ``internal_error`` 등)는 **두
          레인 모두로 fan-out**한다. → 단일 메시지(즉답·거절·rollback 단일 답변)를 A·B
          양 패널에 동일하게 표시.
    """

    if event.kind != "answer.chunk":
        return (event,)
    lane = (event.model_key or "").strip().lower()
    if lane in FRONTEND_MODEL_KEYS:
        return (replace(event, model_key=lane),)
    return tuple(
        replace(event, model_key=model_key)
        for model_key in answer_chunk_model_keys_for_frontend(model_key=event.model_key)
    )


def sanitize_terminal_done_model_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Remove internal model routing labels from terminal ``done`` metadata."""

    clean = dict(meta or {})
    clean.pop("frontend_model_keys", None)
    clean.pop("model_key", None)
    return clean


def terminal_done_model_keys_for_frontend(meta: dict[str, Any] | None) -> tuple[str, str]:
    """Terminal frames complete both frontend lanes."""

    return FRONTEND_MODEL_KEYS
