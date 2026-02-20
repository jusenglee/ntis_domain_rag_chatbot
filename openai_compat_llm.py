import logging
from typing import Any, List, Optional, AsyncIterator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk
from openai import AsyncOpenAI


logger = logging.getLogger(__name__)


class EmptyStreamContentError(RuntimeError):
    """스트림에서 실제 텍스트 청크를 수신하지 못했을 때 발생"""


class OpenAICompatChatModel(BaseChatModel):
    """OpenAI 호환 API(vLLM 등)용 LangChain 래퍼"""

    model_name: str = "/model"
    base_url: str = "http://vllm_solar:8001/v1"
    api_key: str = "EMPTY"
    timeout: float = 120.0

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

    async def _agenerate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))
        response = await client.chat.completions.create(
            model=self.model_name,
            messages=self._to_openai_messages(messages),
            temperature=kwargs.get("temperature", 0.2),
            top_p=kwargs.get("top_p", 0.8),
            max_tokens=max_tokens_hint,
            stream=False,
        )
        content = (response.choices[0].message.content if response.choices else "") or ""
        await client.close()
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
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))
        request_params: dict[str, Any] = {
            "temperature": kwargs.get("temperature", 0.2),
            "top_p": kwargs.get("top_p", 0.8),
            "max_tokens": max_tokens_hint,
            "stop": stop,
        }

        stream = await client.chat.completions.create(
            model=self.model_name,
            messages=self._to_openai_messages(messages),
            temperature=request_params["temperature"],
            top_p=request_params["top_p"],
            max_tokens=request_params["max_tokens"],
            stop=request_params["stop"],
            stream=True,
        )

        emitted = False
        try:
            async for chunk in stream:
                if not chunk.choices:
                    logger.debug("[openai_compat_llm] Empty choices chunk received: model=%s", self.model_name)
                    continue
                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None)
                if text:
                    emitted = True
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
                logger.debug(
                    "[openai_compat_llm] Stream ended without text chunks; fallback to non-stream: model=%s base_url=%s",
                    self.model_name,
                    self.base_url,
                )
                response = await client.chat.completions.create(
                    model=self.model_name,
                    messages=self._to_openai_messages(messages),
                    temperature=request_params["temperature"],
                    top_p=request_params["top_p"],
                    max_tokens=request_params["max_tokens"],
                    stop=request_params["stop"],
                    stream=False,
                )
                fallback_content = (response.choices[0].message.content if response.choices else "") or ""
                if fallback_content:
                    yield ChatGenerationChunk(message=AIMessageChunk(content=fallback_content))
                else:
                    raise EmptyStreamContentError(
                        "No text content emitted in stream and fallback non-stream response was empty. "
                        f"model={self.model_name}, base_url={self.base_url}, request_params={request_params}"
                    )
        finally:
            await client.close()

    @property
    def _llm_type(self) -> str:
        return "openai_compat_chat"
