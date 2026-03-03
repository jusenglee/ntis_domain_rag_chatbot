from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from llm_streaming import run_llm_streaming
from openai_compat_llm import EmptyStreamContentError


class _Chunk:
    def __init__(self, content: str, stream_field: str | None = None):
        additional_kwargs = {} if stream_field is None else {"stream_field": stream_field}
        self.message = SimpleNamespace(content=content, additional_kwargs=additional_kwargs)


class _FakeLLM:
    def __init__(self, chunks=None, raises_empty=False, fallback_text="대체 응답", sleep_before_chunks=None):
        self._chunks = chunks or []
        self._raises_empty = raises_empty
        self._fallback_text = fallback_text
        self._sleep_before_chunks = sleep_before_chunks or []

    async def astream(self, messages, **kwargs):
        if self._raises_empty:
            raise EmptyStreamContentError("empty")
        for idx, chunk in enumerate(self._chunks):
            if idx < len(self._sleep_before_chunks):
                await asyncio.sleep(self._sleep_before_chunks[idx])
            if isinstance(chunk, tuple):
                content, stream_field = chunk
                yield _Chunk(content, stream_field=stream_field)
            else:
                yield _Chunk(chunk)

    async def ainvoke(self, messages, **kwargs):
        return SimpleNamespace(content=self._fallback_text)


def test_run_llm_streaming_collects_chunks() -> None:
    async def _run() -> None:
        llm = _FakeLLM(chunks=["안", "녕"])
        text, metrics = await run_llm_streaming(llm, [HumanMessage(content="hi")])
        assert text == "안녕"
        assert metrics["fallback_used"] is False
        assert metrics["emitted_chunks"] == 2

    asyncio.run(_run())


def test_run_llm_streaming_empty_stream_error_propagates() -> None:
    async def _run() -> None:
        llm = _FakeLLM(raises_empty=True, fallback_text="완성 응답")
        try:
            await run_llm_streaming(llm, [HumanMessage(content="hi")])
            raise AssertionError("EmptyStreamContentError should propagate")
        except EmptyStreamContentError:
            pass

    asyncio.run(_run())


def test_run_llm_streaming_ttft_timeout_phase() -> None:
    async def _run() -> None:
        llm = _FakeLLM(chunks=["늦게도착"], sleep_before_chunks=[0.03])
        text, metrics = await run_llm_streaming(
            llm,
            [HumanMessage(content="hi")],
            ttft_deadline_ms=5,
            gen_deadline_ms=50,
        )
        assert metrics["deadline_exceeded"] is True
        assert metrics["ttft_deadline_exceeded"] is True
        assert metrics["timeout_phase"] == "ttft"
        assert "응답 시간을 제한" in text

    asyncio.run(_run())


def test_run_llm_streaming_gen_timeout_phase_and_short_output_guard() -> None:
    async def _run() -> None:
        llm = _FakeLLM(chunks=["짧다", "뒤늦게"], sleep_before_chunks=[0.0, 0.03])
        text, metrics = await run_llm_streaming(
            llm,
            [HumanMessage(content="hi")],
            ttft_deadline_ms=100,
            gen_deadline_ms=5,
        )
        assert metrics["deadline_exceeded"] is True
        assert metrics["gen_deadline_exceeded"] is True
        assert metrics["timeout_phase"] == "gen"
        assert metrics["emitted_chars"] == len("짧다")
        assert metrics["short_output_guard_triggered"] is True
        assert "응답 시간을 제한" in text

    asyncio.run(_run())


def test_run_llm_streaming_reasoning_filtered_and_metrics() -> None:
    async def _run() -> None:
        llm = _FakeLLM(chunks=[("생각중", "reasoning"), ("정답", "content")])
        text, metrics = await run_llm_streaming(llm, [HumanMessage(content="hi")])
        assert text == "정답"
        assert metrics["reasoning_chars"] == len("생각중")
        assert metrics["content_chars"] == len("정답")
        assert metrics["content_emitted_chunks"] == 1
        assert metrics["ttft_any_ms"] is not None
        assert metrics["ttft_content_ms"] is not None

    asyncio.run(_run())


def test_run_llm_streaming_deadline_with_no_content_returns_empty_text() -> None:
    async def _run() -> None:
        llm = _FakeLLM(chunks=[("사고", "reasoning")], fallback_text="완성형")
        text, metrics = await run_llm_streaming(
            llm,
            [HumanMessage(content="hi")],
            ttft_deadline_ms=100,
            gen_deadline_ms=1,
        )
        assert metrics["deadline_exceeded"] is True
        assert metrics["fallback_used"] is False
        assert metrics["fallback_emit_mode"] is None
        assert metrics["content_emitted_chunks"] == 0
        assert metrics["stream_content_emitted_chunks"] == 0
        assert text == ""

    asyncio.run(_run())
