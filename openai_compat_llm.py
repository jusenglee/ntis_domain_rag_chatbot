import logging
import time
from typing import Any, List, Optional, AsyncIterator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk
from openai import AsyncOpenAI
from pydantic import PrivateAttr


logger = logging.getLogger(__name__)


class OpenAICompatStreamError(RuntimeError):
    """OpenAI 호환 모델 스트리밍 계약 위반 계열 기본 예외"""


class EmptyStreamContentError(OpenAICompatStreamError):
    """스트림에서 실제 텍스트 청크를 수신하지 못했을 때 발생"""


class OpenAICompatChatModel(BaseChatModel):
    """OpenAI 호환 API(vLLM 등)용 LangChain 래퍼"""

    model_name: str = "/model"
    base_url: str = "http://vllm_solar:8010/v1"
    api_key: str = "EMPTY"
    timeout: float = 120.0
    _client: Optional[AsyncOpenAI] = PrivateAttr(default=None)

    def _generate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        raise NotImplementedError("Use ainvoke/astream")

    def _to_openai_messages(self, messages: List[BaseMessage]) -> List[dict[str, str]]:
        converted: List[dict[str, str]] = []
        for m in messages:
            role = "user"
            if isinstance(m, SystemMessage):
                role = "system"
            elif isinstance(m, AIMessage):
                role = "assistant"
            elif isinstance(m, HumanMessage):
                role = "user"
            converted.append({"role": role, "content": str(m.content)})
        return converted

    def _build_openai_request_kwargs(
        self,
        *,
        request_id: Optional[str] = None,
        stop: Optional[List[str]] = None,
        kwargs: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        kwargs = kwargs or {}
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))
        request_kwargs: dict[str, Any] = {
            "temperature": kwargs.get("temperature", 0.2),
            "top_p": kwargs.get("top_p", 0.8),
        }
        if max_tokens_hint is not None:
            request_kwargs["max_tokens"] = max_tokens_hint
        if stop:
            request_kwargs["stop"] = stop
        if request_id:
            request_kwargs["extra_headers"] = {"x-request-id": request_id}
        return request_kwargs

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def _agenerate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        client = self._get_client()
        request_id = kwargs.get("request_id")
        request_kwargs = self._build_openai_request_kwargs(request_id=request_id, stop=kwargs.get("stop"), kwargs=kwargs)
        t0 = time.monotonic()
        response = await client.chat.completions.create(
            model=self.model_name,
            messages=self._to_openai_messages(messages),
            stream=False,
            **request_kwargs,
        )
        content = (response.choices[0].message.content if response.choices else "") or ""
        dt_ms = (time.monotonic() - t0) * 1000
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        logger.info(
            "[openai_compat_llm] non-stream summary: request_id=%s model=%s dt_ms=%.1f message_n=%d char_n=%d usage=%s base_url=%s",
            request_id,
            self.model_name,
            dt_ms,
            len(messages),
            len(content),
            usage,
            self.base_url,
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


    async def ainvoke_non_stream(self, messages: List[BaseMessage], **kwargs: Any) -> AIMessage:
        """스트림 경로 예외 시 강제 non-stream 호출용 API."""
        result = await self._agenerate(messages, **kwargs)
        if result.generations:
            return result.generations[0].message
        return AIMessage(content="")

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        client = self._get_client()
        request_id = kwargs.get("request_id")
        request_kwargs = self._build_openai_request_kwargs(request_id=request_id, stop=stop, kwargs=kwargs)
        t0 = time.monotonic()

        stream = await client.chat.completions.create(
            model=self.model_name,
            messages=self._to_openai_messages(messages),
            stream=True,
            **request_kwargs,
        )

        emitted = False
        fallback_used = False
        chunk_n = 0
        emitted_chunks = 0
        char_n = 0
        ttft_ms = None
        last_finish_reason = None
        try:
            async for chunk in stream:
                chunk_n += 1
                if not chunk.choices:
                    logger.debug("[openai_compat_llm] Empty choices chunk received: model=%s", self.model_name)
                    continue
                last_finish_reason = getattr(chunk.choices[0], "finish_reason", None) or last_finish_reason
                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None)
                if text:
                    emitted = True
                    emitted_chunks += 1
                    if ttft_ms is None:
                        ttft_ms = (time.monotonic() - t0) * 1000
                    char_n += len(text)
                    yield ChatGenerationChunk(message=AIMessageChunk(content=text))
                else:
                    logger.debug(
                        "[openai_compat_llm] Non-text stream chunk received: model=%s base_url=%s finish_reason=%s delta=%s",
                        self.model_name,
                        self.base_url,
                        getattr(chunk.choices[0], "finish_reason", None),
                        delta,
                    )

            if not emitted:
                raise EmptyStreamContentError(
                    "No text content emitted in stream. "
                    f"model={self.model_name}, base_url={self.base_url}, request_kwargs={request_kwargs}"
                )
        finally:
            dt_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "[openai_compat_llm] stream summary: request_id=%s dt_ms=%.1f ttft_ms=%s chunk_n=%d emitted_chunks=%d char_n=%d finish_reason=%s fallback=%s model=%s base_url=%s",
                request_id,
                dt_ms,
                f"{ttft_ms:.1f}" if ttft_ms is not None else "none",
                chunk_n,
                emitted_chunks,
                char_n,
                last_finish_reason,
                fallback_used,
                self.model_name,
                self.base_url,
            )

    @property
    def _llm_type(self) -> str:
        return "openai_compat_chat"
