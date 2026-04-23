import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from apps.platform.langchain_compat import BaseMessage


from apps.api.streaming.contracts import AnswerArtifact, StreamEvent
from apps.api.streaming.emitter import AsyncStreamEmitter
try:
    from apps.platform.openai_compat_llm import EmptyStreamContentError
except ModuleNotFoundError:
    class EmptyStreamContentError(Exception):
        """Fallback error used when langchain-backed LLM adapters are unavailable at import time."""


# 스트리밍 계층은 "사용자에게 무엇을 내보낼 것인가"를 최종 결정한다.
# reasoning 토큰은 관측용으로만 집계하고, 실제 응답에는 content만 포함하는 것이
# ADR-0001과 CONTRACT 문서의 핵심 규칙이다.


def _parse_chunk_fields(chunk: Any) -> Tuple[str, Optional[str]]:
    """스트리밍 chunk에서 본문 텍스트와 stream_field 메타를 분리한다."""
    msg = getattr(chunk, "message", None) or chunk
    text = getattr(msg, "content", "") or ""
    ak = getattr(msg, "additional_kwargs", {}) or {}
    stream_field = ak.get("stream_field")
    return text, stream_field


@dataclass(frozen=True)
class StreamFallbackPolicy:
    """스트리밍 실패 시 fallback 응답을 어떻게 노출할지 정한 정책 객체다."""
    allow_empty_stream_fallback: bool = False
    emit_mode: str = "single_chunk"  # single_chunk | final_only
    user_notice: str = "스트리밍이 불안정하여 완성된 응답으로 대체했습니다."


async def run_llm_streaming(
    llm: Any,
    messages: Sequence[BaseMessage],
    *,
    emitter: Optional[AsyncStreamEmitter] = None,
    model_key: Optional[str] = None,
    max_tokens_hint: Optional[int] = None,
    request_id: Optional[str] = None,
    deadline_ms: Optional[int] = None,
    ttft_deadline_ms: Optional[int] = None,
    gen_deadline_ms: Optional[int] = None,
    max_chars: Optional[int] = None,
    fallback_policy: Optional[StreamFallbackPolicy] = None,
    astream_kwargs: Optional[Dict[str, Any]] = None,
) -> AnswerArtifact:
    """LLM astream 결과를 읽어 최종 텍스트와 스트리밍 메트릭을 계산한다.

    reasoning 토큰은 관측용으로만 세고 사용자 응답에는 content만 합쳐, 계약상 최종 답변과 내부 추론 스트림을 분리한다.
    """
    fallback_policy = fallback_policy or StreamFallbackPolicy()
    stream_kwargs: Dict[str, Any] = dict(astream_kwargs or {})
    if max_tokens_hint is not None:
        stream_kwargs["max_tokens_hint"] = max_tokens_hint
    if request_id:
        stream_kwargs["request_id"] = request_id

    started = time.monotonic()
    ttft_effective_ms = ttft_deadline_ms if ttft_deadline_ms is not None else deadline_ms
    ttft_deadline_s = (ttft_effective_ms / 1000) if ttft_effective_ms is not None else None
    first_chunk_at: Optional[float] = None
    first_content_at: Optional[float] = None
    gen_deadline_at: Optional[float] = None
    chunks: List[str] = []
    emitted_chars = 0
    emitted_chunks = 0
    reasoning_chars = 0
    content_chars = 0
    content_emitted_chunks = 0
    deadline_exceeded = False
    timeout_phase: Optional[str] = None
    ttft_deadline_exceeded = False
    gen_deadline_exceeded = False
    char_limited = False
    short_output_guard_triggered = False

    stream_iter = llm.astream(list(messages), **stream_kwargs)

    async def _next_chunk(stream_aiter: Any) -> Any:
        """TTFT와 generation deadline을 적용해 다음 스트림 chunk를 읽는다."""
        now = time.monotonic()
        if first_chunk_at is None:
            timeout = None
            if ttft_deadline_s is not None:
                timeout = ttft_deadline_s - (now - started)
            if timeout is not None and timeout <= 0:
                raise asyncio.TimeoutError
            if timeout is None:
                return await stream_aiter.__anext__()
            return await asyncio.wait_for(stream_aiter.__anext__(), timeout=timeout)

        if gen_deadline_at is None:
            return await stream_aiter.__anext__()
        timeout = gen_deadline_at - now
        if timeout <= 0:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(stream_aiter.__anext__(), timeout=timeout)

    try:
        stream_aiter = stream_iter.__aiter__()
        while True:
            try:
                chunk = await _next_chunk(stream_aiter)
            except StopAsyncIteration:
                break

            text, stream_field = _parse_chunk_fields(chunk)
            if not text:
                continue

            if first_chunk_at is None:
                first_chunk_at = time.monotonic()
                if gen_deadline_ms is not None:
                    gen_deadline_at = first_chunk_at + (gen_deadline_ms / 1000)

            # reasoning 델타는 사용자 최종 답변 문자열에 합치지 않는다.
            # 이유: 모델 내부 추론 노출 방지/정책 준수 + UI 최종 답변 오염 방지.
            # 대신 reasoning_chars 메트릭으로만 누적해 운영 관측 신호로 사용한다.
            if stream_field == "reasoning":
                reasoning_chars += len(text)
                continue

            if stream_field not in {"content", None}:
                continue

            if first_content_at is None:
                first_content_at = time.monotonic()

            chunks.append(text)
            content_chars += len(text)
            content_emitted_chunks += 1
            emitted_chars = content_chars
            emitted_chunks = content_emitted_chunks
            if emitter is not None and request_id:
                await emitter.publish(
                    StreamEvent(
                        kind="answer.chunk",
                        request_id=request_id,
                        model_key=model_key,
                        content=text,
                        meta={},
                    )
                )

            if max_chars and emitted_chars >= max_chars:
                char_limited = True
                break
    except asyncio.TimeoutError:
        deadline_exceeded = True
        if first_chunk_at is None:
            ttft_deadline_exceeded = True
            timeout_phase = "ttft"
        else:
            gen_deadline_exceeded = True
            timeout_phase = "gen"
    except EmptyStreamContentError:
        # 스트림 본문이 비어 있으면 fail-fast로 상위 호출자에 그대로 전달한다.
        raise
    except Exception:
        logger.exception("run_llm_streaming failed: request_id=%s", request_id)
        raise
    finally:
        aclose = getattr(stream_iter, "aclose", None)
        if callable(aclose):
            await aclose()

    stream_content_emitted_chunks = content_emitted_chunks

    # 생성 제한(시간/길이)으로 잘린 출력이 너무 짧으면, "부분 성공"이 아닌 "품질 위험"으로 분류한다.
    # short_output_guard_triggered=True는 운영 관점에서
    # "응답은 나갔지만 사용자가 쓸 수 없을 가능성이 높다"는 신호이며,
    # 상위 계층에서 재시도/논스트림 폴백/알림 정책을 태우는 트리거로 사용한다.
    short_output_guard_min_chars = 40
    if (gen_deadline_exceeded or char_limited) and 0 < emitted_chars < short_output_guard_min_chars:
        short_output_guard_triggered = True

    final_text = "".join(chunks).replace("<eos>", "").strip()
    elapsed_ms = (time.monotonic() - started) * 1000
    ttft_any_ms = ((first_chunk_at - started) * 1000) if first_chunk_at is not None else None
    ttft_content_ms = ((first_content_at - started) * 1000) if first_content_at is not None else None

    metrics: Dict[str, Any] = {
        "elapsed_ms": round(elapsed_ms, 1),
        "ttft_ms": round(ttft_content_ms, 1) if ttft_content_ms is not None else None,
        "ttft_any_ms": round(ttft_any_ms, 1) if ttft_any_ms is not None else None,
        "ttft_content_ms": round(ttft_content_ms, 1) if ttft_content_ms is not None else None,
        "emitted_chunks": emitted_chunks,
        "emitted_chars": emitted_chars,
        "content_emitted_chunks": content_emitted_chunks,
        "stream_content_emitted_chunks": stream_content_emitted_chunks,
        "content_chars": content_chars,
        "reasoning_chars": reasoning_chars,
        "deadline_exceeded": deadline_exceeded,
        "timeout_phase": timeout_phase,
        "ttft_deadline_exceeded": ttft_deadline_exceeded,
        "gen_deadline_exceeded": gen_deadline_exceeded,
        "char_limited": char_limited,
        "fallback_used": False,
        "fallback_emit_mode": "disabled",
        "short_output_guard_triggered": short_output_guard_triggered,
        "short_output_guard_min_chars": short_output_guard_min_chars,
    }

    is_partial_content_truncation = (deadline_exceeded or char_limited) and content_emitted_chunks > 0
    if deadline_exceeded and is_partial_content_truncation:
        final_text = (final_text + "\n\n(안내: 응답 시간을 제한하여 일부만 반환했습니다.)").strip()
    elif char_limited and is_partial_content_truncation:
        final_text = (final_text + "\n\n(안내: 응답 길이 제한으로 일부만 반환했습니다.)").strip()

    answer_kind = "llm_streamed" if content_emitted_chunks > 0 else "llm_collected"
    return AnswerArtifact(
        text=final_text,
        answer_kind=answer_kind,
        stream_metrics=metrics,
        user_visible_final_required=True,
        meta={"model_key": model_key},
    )
