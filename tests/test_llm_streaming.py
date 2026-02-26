from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from llm_streaming import run_llm_streaming, StreamFallbackPolicy
from openai_compat_llm import EmptyStreamContentError


class _Chunk:
    def __init__(self, content: str):
        self.message = SimpleNamespace(content=content)


class _FakeLLM:
    def __init__(self, chunks=None, raises_empty=False, fallback_text="대체 응답"):
        self._chunks = chunks or []
        self._raises_empty = raises_empty
        self._fallback_text = fallback_text

    async def astream(self, messages, **kwargs):
        if self._raises_empty:
            raise EmptyStreamContentError("empty")
        for c in self._chunks:
            yield _Chunk(c)

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


def test_run_llm_streaming_uses_fallback_on_empty_stream_error() -> None:
    async def _run() -> None:
        llm = _FakeLLM(raises_empty=True, fallback_text="완성 응답")
        text, metrics = await run_llm_streaming(
            llm,
            [HumanMessage(content="hi")],
            fallback_policy=StreamFallbackPolicy(allow_empty_stream_fallback=True),
        )
        assert text == "완성 응답"
        assert metrics["fallback_used"] is True

    asyncio.run(_run())
