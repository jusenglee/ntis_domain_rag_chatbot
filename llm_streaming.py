import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.messages import BaseMessage

from openai_compat_llm import EmptyStreamContentError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamFallbackPolicy:
    allow_empty_stream_fallback: bool = True
    emit_mode: str = "single_chunk"  # single_chunk | final_only
    user_notice: str = "스트리밍이 불안정하여 완성된 응답으로 대체했습니다."


async def run_llm_streaming(
    llm: Any,
    messages: Sequence[BaseMessage],
    *,
    max_tokens_hint: Optional[int] = None,
    request_id: Optional[str] = None,
    deadline_ms: Optional[int] = None,
    ttft_deadline_ms: Optional[int] = None,
    gen_deadline_ms: Optional[int] = None,
    max_chars: Optional[int] = None,
    fallback_policy: Optional[StreamFallbackPolicy] = None,
    astream_kwargs: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """공통 스트리밍 실행 유틸.

    반환:
      - final_text: 최종 텍스트
      - metrics: 실행 메트릭/정책 결과
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
    gen_deadline_at: Optional[float] = None
    chunks: List[str] = []
    emitted_chars = 0
    emitted_chunks = 0
    deadline_exceeded = False
    timeout_phase: Optional[str] = None
    ttft_deadline_exceeded = False
    gen_deadline_exceeded = False
    char_limited = False
    fallback_used = False
    short_output_guard_triggered = False

    stream_iter = llm.astream(list(messages), **stream_kwargs)

    async def _next_chunk(stream_aiter: Any) -> Any:
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

            text = getattr(chunk, "content", "") or getattr(getattr(chunk, "message", None), "content", "") or ""
            if not text:
                continue

            if first_chunk_at is None:
                first_chunk_at = time.monotonic()
                if gen_deadline_ms is not None:
                    gen_deadline_at = first_chunk_at + (gen_deadline_ms / 1000)

            chunks.append(text)
            emitted_chars += len(text)
            emitted_chunks += 1

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
        if not fallback_policy.allow_empty_stream_fallback:
            raise
        fallback_used = True
    except Exception:
        logger.exception("run_llm_streaming failed: request_id=%s", request_id)
        raise
    finally:
        aclose = getattr(stream_iter, "aclose", None)
        if callable(aclose):
            await aclose()

    if emitted_chunks == 0 and fallback_policy.allow_empty_stream_fallback and not deadline_exceeded:
        fallback_used = True
        response = await llm.ainvoke(list(messages), max_tokens_hint=max_tokens_hint, request_id=request_id)
        fallback_text = (getattr(response, "content", "") or "").strip()
        if fallback_text:
            chunks.append(fallback_text)
            emitted_chunks = 1
            emitted_chars = len(fallback_text)

    # 생성 제한으로 끊긴 경우, 지나치게 짧은 출력은 별도 정책(재시도/폴백)으로 라우팅할 수 있는 분기 포인트
    short_output_guard_min_chars = 40
    if (gen_deadline_exceeded or char_limited) and 0 < emitted_chars < short_output_guard_min_chars:
        short_output_guard_triggered = True

    final_text = "".join(chunks).replace("<eos>", "").strip()
    elapsed_ms = (time.monotonic() - started) * 1000
    ttft_ms = ((first_chunk_at - started) * 1000) if first_chunk_at is not None else None

    metrics: Dict[str, Any] = {
        "elapsed_ms": round(elapsed_ms, 1),
        "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
        "emitted_chunks": emitted_chunks,
        "emitted_chars": emitted_chars,
        "deadline_exceeded": deadline_exceeded,
        "timeout_phase": timeout_phase,
        "ttft_deadline_exceeded": ttft_deadline_exceeded,
        "gen_deadline_exceeded": gen_deadline_exceeded,
        "char_limited": char_limited,
        "fallback_used": fallback_used,
        "fallback_emit_mode": fallback_policy.emit_mode,
        "short_output_guard_triggered": short_output_guard_triggered,
        "short_output_guard_min_chars": short_output_guard_min_chars,
    }

    if deadline_exceeded:
        final_text = (final_text + "\n\n(안내: 응답 시간을 제한하여 일부만 반환했습니다.)").strip()
    elif char_limited:
        final_text = (final_text + "\n\n(안내: 응답 길이 제한으로 일부만 반환했습니다.)").strip()

    return final_text, metrics
