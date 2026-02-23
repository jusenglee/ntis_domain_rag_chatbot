import logging
import time
from typing import Any, List, Optional, AsyncIterator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk
from openai import AsyncOpenAI


logger = logging.getLogger(__name__)


class OpenAICompatStreamError(RuntimeError):
    """OpenAI 호환 모델 스트리밍 계약 위반 계열 기본 예외"""


class EmptyStreamContentError(OpenAICompatStreamError):
    """스트림에서 실제 텍스트 청크를 수신하지 못했을 때 발생"""


class OpenAICompatChatModel(BaseChatModel):
    """OpenAI 호환 API(vLLM 등)용 LangChain 래퍼"""

    model_name: str = "/model"
    base_url: str = "http://triton_gpt_oss:8001/v1"
    api_key: str = "EMPTY"
    timeout: float = 120.0

    @staticmethod
    def _extract_structured_output_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
        """구조화 출력 관련 파라미터만 안전하게 추출한다."""
        passthrough: dict[str, Any] = {}
        for key in ("response_format", "tools", "tool_choice"):
            value = kwargs.get(key)
            if value is not None:
                passthrough[key] = value
        return passthrough

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
        structured_kwargs = self._extract_structured_output_kwargs(kwargs)
        request_id = kwargs.get("request_id")
        extra_headers = {"x-request-id": request_id} if request_id else None
        t0 = time.monotonic()
        response = await client.chat.completions.create(
            model=self.model_name,
            messages=self._to_openai_messages(messages),
            temperature=kwargs.get("temperature", 0.2),
            top_p=kwargs.get("top_p", 0.8),
            max_tokens=max_tokens_hint,
            stream=False,
            extra_headers=extra_headers,
            **structured_kwargs,
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
        structured_kwargs = self._extract_structured_output_kwargs(kwargs)
        request_id = kwargs.get("request_id")
        extra_headers = {"x-request-id": request_id} if request_id else None
        t0 = time.monotonic()
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
            extra_headers=extra_headers,
            **structured_kwargs,
        )

        emitted = False
        chunk_n = 0
        char_n = 0
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
                logger.warning(
                    "[openai_compat_llm] Stream ended without text chunks; fallback to non-stream: request_id=%s model=%s base_url=%s",
                    request_id,
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
                    extra_headers=extra_headers,
                    **structured_kwargs,
                )
                fallback_content = (response.choices[0].message.content if response.choices else "") or ""
                if fallback_content:
                    char_n += len(fallback_content)
                    yield ChatGenerationChunk(message=AIMessageChunk(content=fallback_content))
                else:
                    raise EmptyStreamContentError(
                        "No text content emitted in stream and fallback non-stream response was empty. "
                        f"model={self.model_name}, base_url={self.base_url}, request_params={request_params}"
                    )
        finally:
            dt_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "[openai_compat_llm] stream summary: request_id=%s dt_ms=%.1f chunk_n=%d char_n=%d finish_reason=%s model=%s base_url=%s",
                request_id,
                dt_ms,
                chunk_n,
                char_n,
                last_finish_reason,
                self.model_name,
                self.base_url,
            )
            await client.close()

    @property
    def _llm_type(self) -> str:
        return "openai_compat_chat"
