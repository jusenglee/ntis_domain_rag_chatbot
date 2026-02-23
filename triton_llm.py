from typing import Any, List, Optional, AsyncIterator, Generator, cast
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk

# 기존 코드의 함수 임포트 (경로는 환경에 맞게 조정하세요)
from triton_client import triton_infer, get_tokenizer_for_model

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

        # triton_infer(..., stream=False) 계약: str 반환
        response_text = cast(str, triton_infer(
            self.model_name,
            prompt,
            stream=False,
            max_tokens=max_tokens_hint,
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

        # stream=True
        # triton_infer(..., stream=True) 계약: generator 반환
        gen = cast(Generator[str, None, None], triton_infer(
            self.model_name,
            prompt,
            stream=True,
            max_tokens=max_tokens_hint,
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
