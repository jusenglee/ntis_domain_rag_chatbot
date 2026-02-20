from typing import Any, List, Optional, AsyncIterator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk
from openai import AsyncOpenAI


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

        stream = await client.chat.completions.create(
            model=self.model_name,
            messages=self._to_openai_messages(messages),
            temperature=kwargs.get("temperature", 0.2),
            top_p=kwargs.get("top_p", 0.8),
            max_tokens=max_tokens_hint,
            stream=True,
        )

        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                text = getattr(delta, "content", None)
                if text:
                    yield ChatGenerationChunk(message=AIMessageChunk(content=text))
        finally:
            await client.close()

    @property
    def _llm_type(self) -> str:
        return "openai_compat_chat"
