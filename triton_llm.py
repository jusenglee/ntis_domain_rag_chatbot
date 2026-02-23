from typing import Any, List, Optional, AsyncIterator, Generator, cast
import logging
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk

# 기존 코드의 함수 임포트 (경로는 환경에 맞게 조정하세요)
from triton_client import triton_infer, get_tokenizer_for_model

logger = logging.getLogger(__name__)

TRITON_STRUCTURED_OUTPUT_CAPABLE_MODELS = frozenset()


def supports_triton_structured_output(model_name: str) -> bool:
    """Triton 백엔드가 response_format 기반 구조화 출력을 지원하는지 반환한다."""
    return model_name in TRITON_STRUCTURED_OUTPUT_CAPABLE_MODELS



def _extract_triton_passthrough_kwargs(model_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """triton_infer가 받을 수 있는 확장 파라미터만 선별 전달한다."""
    passthrough: dict[str, Any] = {}
    for key in ("temperature", "top_p", "timeout_first", "timeout_idle"):
        value = kwargs.get(key)
        if value is not None:
            passthrough[key] = value

    structured_keys = ("response_format", "tools", "tool_choice")
    if supports_triton_structured_output(model_name):
        for key in structured_keys:
            value = kwargs.get(key)
            if value is not None:
                passthrough[key] = value
    else:
        unsupported_structured = [key for key in structured_keys if kwargs.get(key) is not None]
        if unsupported_structured:
            logger.info(
                "[triton_llm] model=%s parser fallback mode: ignoring unsupported kwargs=%s",
                model_name,
                ", ".join(unsupported_structured),
            )

    return passthrough

class TritonChatModel(BaseChatModel):
    """LangChain 호환 Triton 래퍼"""
    model_name: str = "gpt_oss_triton_0"

    def _generate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        # 동기 호출은 구현 생략 (필요 시 추가)
        raise NotImplementedError("Use astream for streaming")

    async def _agenerate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        # 비동기 호출 (스트리밍 없이 결과만 반환)
        prompt = self._format_messages(messages)
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))

        passthrough_kwargs = _extract_triton_passthrough_kwargs(self.model_name, kwargs)

        # triton_infer(..., stream=False) 계약: str 반환
        response_text = cast(str, triton_infer(
            self.model_name,
            prompt,
            stream=False,
            max_tokens=max_tokens_hint,
            **passthrough_kwargs,
        ))

        full_text = response_text or ""

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=full_text))])

    async def ainvoke_non_stream(self, messages: List[BaseMessage], **kwargs: Any) -> AIMessage:
        """스트림 경로 예외 시 강제 non-stream 호출용 API."""
        result = await self._agenerate(messages, **kwargs)
        if result.generations:
            return result.generations[0].message
        return AIMessage(content="")

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        """스트리밍 지원"""
        prompt = self._format_messages(messages)
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))

        # Triton generator를 비동기 루프에서 실행 (block 방지)
        import asyncio
        loop = asyncio.get_running_loop()

        passthrough_kwargs = _extract_triton_passthrough_kwargs(self.model_name, kwargs)

        # stream=True
        # triton_infer(..., stream=True) 계약: generator 반환
        gen = cast(Generator[str, None, None], triton_infer(
            self.model_name,
            prompt,
            stream=True,
            max_tokens=max_tokens_hint,
            **passthrough_kwargs,
        ))

        try:
            while True:
                # next(gen)을 스레드 풀에서 실행
                chunk = await loop.run_in_executor(None, next, gen, None)
                if chunk is None:
                    break

                text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="ignore")
                if text:
                    yield ChatGenerationChunk(message=AIMessageChunk(content=text))
        finally:
            try: gen.close()
            except: pass

    def _format_messages(self, messages: List[BaseMessage]) -> str:
        """메시지 리스트를 프롬프트 문자열로 변환"""
        tokenizer = get_tokenizer_for_model(self.model_name)
        chat_format = []
        for m in messages:
            role = "user"
            if isinstance(m, SystemMessage): role = "system"
            elif isinstance(m, AIMessage): role = "assistant"
            chat_format.append({"role": role, "content": m.content})

        try:
            return tokenizer.apply_chat_template(chat_format, tokenize=False, add_generation_prompt=True)
        except:
            # 템플릿 적용 실패 시 Fallback
            prompt = ""
            for m in chat_format:
                prompt += f"<|{m['role']}|>\n{m['content']}\n"
            return prompt + "<|assistant|>\n"

    @property
    def _llm_type(self) -> str:
        return "triton_chat"
