import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.messages import BaseMessage

from openai_compat_llm import EmptyStreamContentError

logger = logging.getLogger(__name__)


def _parse_chunk_fields(chunk: Any) -> Tuple[str, Optional[str]]:
    msg = getattr(chunk, "message", None) or chunk
    text = getattr(msg, "content", "") or ""
    ak = getattr(msg, "additional_kwargs", {}) or {}
    stream_field = ak.get("stream_field")
    return text, stream_field


@dataclass(frozen=True)
class StreamFallbackPolicy:
    # Deprecated: non-stream fallback 정책은 2026-03 기준 비활성화되었으며,
    # 하위 호환을 위해 필드만 유지합니다(값은 run_llm_streaming에서 사용하지 않음).
    allow_empty_stream_fallback: Optional[bool] = None
    emit_mode: Optional[str] = None
    user_notice: Optional[str] = None


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
        # 빈 스트림은 상위 레이어에서 fail-fast 처리하도록 전파한다.
        raise
    except Exception:
        logger.exception("run_llm_streaming failed: request_id=%s", request_id)
        raise
    finally:
        aclose = getattr(stream_iter, "aclose", None)
        if callable(aclose):
            await aclose()

    stream_content_emitted_chunks = content_emitted_chunks

    # 생성 제한으로 끊긴 경우, 지나치게 짧은 출력은 별도 정책(재시도/폴백)으로 라우팅할 수 있는 분기 포인트
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
        # Deprecated metric: non-stream fallback 제거 이후 상수값으로 유지하고 점진 제거 예정.
        "fallback_used": False,
        "fallback_emit_mode": None,
        "short_output_guard_triggered": short_output_guard_triggered,
        "short_output_guard_min_chars": short_output_guard_min_chars,
    }

    is_partial_content_truncation = (deadline_exceeded or char_limited) and content_emitted_chunks > 0
    if deadline_exceeded and is_partial_content_truncation:
        final_text = (final_text + "\n\n(안내: 응답 시간을 제한하여 일부만 반환했습니다.)").strip()
    elif char_limited and is_partial_content_truncation:
        final_text = (final_text + "\n\n(안내: 응답 길이 제한으로 일부만 반환했습니다.)").strip()

    return final_text, metrics
