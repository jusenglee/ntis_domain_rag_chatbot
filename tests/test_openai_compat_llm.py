from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List

from langchain_core.messages import HumanMessage

from openai_compat_llm import OpenAICompatChatModel


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        self.usage = None


class _FakeStream:
    def __init__(self, chunks: List[Any]):
        self._chunks = chunks

    def __aiter__(self):
        async def _gen():
            for chunk in self._chunks:
                yield chunk

        return _gen()


class _FakeCompletions:
    def __init__(self):
        self.calls: List[dict[str, Any]] = []

    async def create(self, **kwargs: Any):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            chunk = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason=None,
                        delta=SimpleNamespace(content="스트림 응답"),
                    )
                ]
            )
            return _FakeStream([chunk])
        return _FakeResponse("비스트림 응답")


class _FakeClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeCompletions())
        self.closed = False

    async def close(self):
        self.closed = True


def test_agenerate_omits_none_max_tokens_and_applies_stop() -> None:
    async def _run() -> None:
        model = OpenAICompatChatModel(model_name="/model", base_url="http://localhost:8000/v1", api_key="EMPTY")
        fake_client = _FakeClient()
        model._client = fake_client

        await model._agenerate([HumanMessage(content="안녕")], stop=["END"], request_id="rid-1")

        call = fake_client.chat.completions.calls[0]
        assert call["stream"] is False
        assert call["stop"] == ["END"]
        assert call["extra_headers"] == {"x-request-id": "rid-1"}
        assert "max_tokens" not in call

    asyncio.run(_run())


def test_astream_uses_same_request_policy_with_stop_and_max_tokens() -> None:
    async def _run() -> None:
        model = OpenAICompatChatModel(model_name="/model", base_url="http://localhost:8000/v1", api_key="EMPTY")
        fake_client = _FakeClient()
        model._client = fake_client

        chunks = []
        async for chunk in model._astream(
            [HumanMessage(content="질문")],
            stop=["###"],
            max_tokens_hint=128,
            request_id="rid-2",
        ):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].message.content == "스트림 응답"

        call = fake_client.chat.completions.calls[0]
        assert call["stream"] is True
        assert call["stop"] == ["###"]
        assert call["max_tokens"] == 128
        assert call["extra_headers"] == {"x-request-id": "rid-2"}

    asyncio.run(_run())


def test_aclose_closes_cached_client() -> None:
    async def _run() -> None:
        model = OpenAICompatChatModel(model_name="/model", base_url="http://localhost:8000/v1", api_key="EMPTY")
        fake_client = _FakeClient()
        model._client = fake_client

        await model.aclose()

        assert fake_client.closed is True
        assert model._client is None

    asyncio.run(_run())
