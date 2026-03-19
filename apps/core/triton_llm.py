from typing import Any, List, Optional, AsyncIterator
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk

# 기존 코드의 함수 임포트 (경로는 환경에 맞게 조정하세요)
from apps.core.triton_client import triton_infer, get_tokenizer_for_model

class TritonChatModel(BaseChatModel):
    """LangChain 인터페이스로 Triton LLM을 감싸는 채팅 모델 어댑터다."""
    model_name: str = "gpt_oss_0"

    def _generate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        # 동기 호출은 구현 생략 (필요 시 추가)
        """동기 생성 경로는 지원하지 않고, 호출 시 명시적으로 막는다."""
        raise NotImplementedError("Use astream for streaming")

    async def _agenerate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        # 비동기 호출 (스트리밍 없이 결과만 반환)
        """비스트리밍 Triton 호출 결과를 한 번에 모아 ChatResult로 반환한다."""
        prompt = self._format_messages(messages)
        full_text = ""
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))
        # stream=False로 호출
        gen = triton_infer(
            self.model_name,
            prompt,
            stream=False,
            max_tokens=max_tokens_hint,
        )
        for chunk in gen:
            if chunk:
                full_text += chunk

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=full_text))])

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        """Triton의 동기 generator를 executor로 감싸 LangChain 스트림 청크로 흘려보낸다.

        이렇게 해야 이벤트 루프를 막지 않고도 기존 Triton client를 그대로 재사용할 수 있다.
        """
        prompt = self._format_messages(messages)
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))

        # Triton generator를 비동기 루프에서 실행 (block 방지)
        import asyncio
        loop = asyncio.get_running_loop()

        # stream=True로 청크 단위 결과를 받는다.
        gen = triton_infer(
            self.model_name,
            prompt,
            stream=True,
            max_tokens=max_tokens_hint,
        )

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
        """LangChain message 목록을 Triton tokenizer가 이해하는 chat prompt로 변환한다.

        chat template 적용에 실패하면 role marker 기반 fallback prompt를 만들어 호출 자체는 계속 진행한다.
        """
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
        """LangChain이 모델 종류를 식별할 때 쓰는 고정 문자열을 돌려준다."""
        return "triton_chat"
