from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List

from langchain_core.messages import HumanMessage

from openai_compat_llm import (
    STREAM_FIELD_KEY,
    EmptyStreamContentError,
    OpenAICompatChatModel,
)


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


class _FakeClosableStream(_FakeStream):
    def __init__(self, chunks: List[Any]):
        super().__init__(chunks)
        self.close_called = False

    async def close(self):
        self.close_called = True


class _FakeClosableCompletions(_FakeCompletions):
    def __init__(self):
        super().__init__()
        self.last_stream: _FakeClosableStream | None = None

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
            stream = _FakeClosableStream([chunk])
            self.last_stream = stream
            return stream
        return _FakeResponse("비스트림 응답")


class _FakeClosableClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_FakeClosableCompletions())
        self.closed = False

    async def close(self):
        self.closed = True


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
        assert call["max_completion_tokens"] == 128
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


def test_agenerate_uses_request_id_from_config_metadata() -> None:
    async def _run() -> None:
        model = OpenAICompatChatModel(model_name="/model", base_url="http://localhost:8000/v1", api_key="EMPTY")
        fake_client = _FakeClient()
        model._client = fake_client

        await model._agenerate(
            [HumanMessage(content="안녕")],
            config={"metadata": {"request_id": "rid-meta-1", "conversation_id": "cid-meta-1"}},
        )

        call = fake_client.chat.completions.calls[0]
        assert call["stream"] is False
        assert call["extra_headers"] == {"x-request-id": "rid-meta-1"}

    asyncio.run(_run())


def test_astream_reasoning_and_content_stream_field() -> None:
    async def _run() -> None:
        model = OpenAICompatChatModel(model_name="/model", base_url="http://localhost:8000/v1", api_key="EMPTY")

        class _ReasoningClient:
            def __init__(self):
                reasoning_chunk = SimpleNamespace(
                    choices=[SimpleNamespace(finish_reason=None, delta=SimpleNamespace(reasoning="생각"))]
                )
                content_chunk = SimpleNamespace(
                    choices=[SimpleNamespace(finish_reason=None, delta=SimpleNamespace(content="답변"))]
                )
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(
                        create=self._create,
                    )
                )
                self.stream = _FakeStream([reasoning_chunk, content_chunk])

            async def _create(self, **kwargs: Any):
                return self.stream

        fake_client = _ReasoningClient()
        model._client = fake_client

        chunks = []
        async for chunk in model._astream([HumanMessage(content="질문")]):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].message.content == "생각"
        assert chunks[0].message.additional_kwargs[STREAM_FIELD_KEY] == "reasoning"
        assert chunks[1].message.content == "답변"
        assert chunks[1].message.additional_kwargs[STREAM_FIELD_KEY] == "content"

    asyncio.run(_run())


def test_astream_reasoning_only_raises_empty_stream_content_error() -> None:
    async def _run() -> None:
        model = OpenAICompatChatModel(model_name="/model", base_url="http://localhost:8000/v1", api_key="EMPTY")

        reasoning_chunk = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=None, delta=SimpleNamespace(reasoning_content="생각만"))]
        )

        class _ReasoningOnlyClient:
            def __init__(self):
                self.chat = SimpleNamespace(
                    completions=SimpleNamespace(create=self._create),
                )

            async def _create(self, **kwargs: Any):
                return _FakeStream([reasoning_chunk])

        model._client = _ReasoningOnlyClient()

        try:
            async for _ in model._astream([HumanMessage(content="질문")]):
                pass
            assert False, "EmptyStreamContentError가 발생해야 합니다."
        except EmptyStreamContentError:
            pass

    asyncio.run(_run())


def test_astream_awaits_awaitable_close() -> None:
    async def _run() -> None:
        model = OpenAICompatChatModel(model_name="/model", base_url="http://localhost:8000/v1", api_key="EMPTY")
        fake_client = _FakeClosableClient()
        model._client = fake_client

        async for _ in model._astream([HumanMessage(content="질문")]):
            pass

        assert fake_client.chat.completions.last_stream is not None
        assert fake_client.chat.completions.last_stream.close_called is True

    asyncio.run(_run())
