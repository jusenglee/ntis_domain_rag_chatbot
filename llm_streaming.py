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
    chunks: List[str] = []
    emitted_chars = 0
    emitted_chunks = 0
    deadline_exceeded = False
    char_limited = False
    fallback_used = False

    try:
        async for chunk in llm.astream(list(messages), **stream_kwargs):
            text = getattr(chunk, "content", "") or getattr(getattr(chunk, "message", None), "content", "") or ""
            if not text:
                continue
            chunks.append(text)
            emitted_chars += len(text)
            emitted_chunks += 1

            if max_chars and emitted_chars >= max_chars:
                char_limited = True
                break

            if deadline_ms is not None and ((time.monotonic() - started) * 1000 >= deadline_ms):
                deadline_exceeded = True
                break
    except EmptyStreamContentError:
        if not fallback_policy.allow_empty_stream_fallback:
            raise
        fallback_used = True
    except Exception:
        logger.exception("run_llm_streaming failed: request_id=%s", request_id)
        raise

    if emitted_chunks == 0 and fallback_policy.allow_empty_stream_fallback:
        fallback_used = True
        response = await llm.ainvoke(list(messages), max_tokens_hint=max_tokens_hint, request_id=request_id)
        fallback_text = (getattr(response, "content", "") or "").strip()
        if fallback_text:
            chunks.append(fallback_text)
            emitted_chunks = 1
            emitted_chars = len(fallback_text)

    final_text = "".join(chunks).replace("<eos>", "").strip()
    elapsed_ms = (time.monotonic() - started) * 1000

    metrics: Dict[str, Any] = {
        "elapsed_ms": round(elapsed_ms, 1),
        "emitted_chunks": emitted_chunks,
        "emitted_chars": emitted_chars,
        "deadline_exceeded": deadline_exceeded,
        "char_limited": char_limited,
        "fallback_used": fallback_used,
        "fallback_emit_mode": fallback_policy.emit_mode,
    }

    if deadline_exceeded:
        final_text = (final_text + "\n\n(안내: 응답 시간을 제한하여 일부만 반환했습니다.)").strip()
    elif char_limited:
        final_text = (final_text + "\n\n(안내: 응답 길이 제한으로 일부만 반환했습니다.)").strip()

    return final_text, metrics
