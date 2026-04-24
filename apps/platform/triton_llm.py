from typing import Any, AsyncIterator, List, Optional
import time
from loguru import logger

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from apps.platform.triton_client import get_tokenizer_for_model, triton_infer

"""
Triton 추론 서버 통신 모듈입니다.
이 모듈은 NVIDIA Triton Inference Server를 통해 LLM 추론을 수행하며, 
LangChain의 ChatModel 인터페이스를 구현하여 시스템의 다른 컴포넌트와 유연하게 결합됩니다.

핵심 기능:
1. 프롬프트 템플릿 적용: 모델별 토크나이저를 사용하여 대화 내역을 모델 최적화 포맷으로 변환합니다.
2. 실시간 스트리밍: 모델이 생성하는 토큰을 실시간으로 클라이언트에 전달합니다.
3. 파라미터 제어: Temperature, Top-P, Max Tokens 등을 통해 모델의 응답 특성을 조정합니다.
4. 재현성 확보: 추론 호출 시 사용된 모든 파라미터와 소요 시간을 로그에 기록합니다.
"""

class TritonChatModel(BaseChatModel):
    """
    Triton 추론 서버를 사용하는 LLM을 위한 LangChain ChatModel 어댑터입니다.
    NVIDIA Triton 서버와 통신하며 스트리밍 및 비스트리밍 방식을 모두 지원합니다.
    """

    model_name: str = "gpt_oss_0"

    def _generate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        """
        동기 방식의 답변 생성을 처리합니다.
        하지만 효율적인 리소스 사용을 위해 스트리밍 방식(astream) 사용을 권장하며, 
        필요 시 이 메서드를 직접 구현하여 사용할 수 있습니다.
        """
        raise NotImplementedError("스트리밍 방식(astream) 또는 비동기 방식(agenerate)을 사용해 주세요.")

    async def _agenerate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        """
        비동기 방식의 답변 생성을 처리합니다. Triton 서버로부터 전체 답변을 한 번에 받아옵니다.
        """
        prompt = self._format_messages(messages)
        full_text = ""
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))
        
        # 호출 파라미터 기록 (재현성 확보)
        logger.info(f"Triton sync call: model={self.model_name}, max_tokens={max_tokens_hint}, temperature={kwargs.get('temperature')}")
        
        t0 = time.time()
        gen = triton_infer(
            self.model_name,
            prompt,
            stream=False,
            max_tokens=max_tokens_hint,
            temperature=kwargs.get("temperature"),
            top_p=kwargs.get("top_p"),
            top_k=kwargs.get("top_k"),
        )
        for chunk in gen:
            if chunk:
                full_text += chunk
        
        dt = time.time() - t0
        logger.info(f"Triton sync done: model={self.model_name}, dt={dt:.3f}s, length={len(full_text)}")

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=full_text))])

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """
        Triton 서버의 응답을 실시간으로 스트리밍하여 반환합니다.
        사용자는 답변이 생성되는 과정을 실시간으로 볼 수 있습니다.
        """
        prompt = self._format_messages(messages)
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))

        # 호출 파라미터 기록 (재현성 확보)
        logger.info(f"Triton stream start: model={self.model_name}, max_tokens={max_tokens_hint}")

        import asyncio
        loop = asyncio.get_running_loop()
        t0 = time.time()
        
        gen = triton_infer(
            self.model_name,
            prompt,
            stream=True,
            max_tokens=max_tokens_hint,
            temperature=kwargs.get("temperature"),
            top_p=kwargs.get("top_p"),
            top_k=kwargs.get("top_k"),
        )

        chunk_count = 0
        try:
            while True:
                # Triton 제너레이터로부터 다음 청크를 비동기적으로 가져옵니다.
                chunk = await loop.run_in_executor(None, next, gen, None)
                if chunk is None:
                    break

                text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="ignore")
                if text:
                    chunk_count += 1
                    yield ChatGenerationChunk(message=AIMessageChunk(content=text))
        finally:
            dt = time.time() - t0
            logger.info(f"Triton stream end: model={self.model_name}, dt={dt:.3f}s, chunks={chunk_count}")
            try:
                gen.close()
            except Exception:
                pass

    def _format_messages(self, messages: List[BaseMessage]) -> str:
        """
        LangChain 메시지 객체들을 모델이 이해할 수 있는 단일 문자열 프롬프트로 변환합니다.
        모델별 토크나이저의 chat_template을 적용하여 모델에 최적화된 입력을 생성합니다.
        """
        tokenizer = get_tokenizer_for_model(self.model_name)
        chat_format = []
        for message in messages:
            role = "user"
            if isinstance(message, SystemMessage):
                role = "system"
            elif isinstance(message, AIMessage):
                role = "assistant"
            elif isinstance(message, HumanMessage):
                role = "user"
            chat_format.append({"role": role, "content": message.content})

        try:
            # 토크나이저의 템플릿을 사용하여 프롬프트를 구성합니다.
            return tokenizer.apply_chat_template(chat_format, tokenize=False, add_generation_prompt=True)
        except Exception as e:
            logger.warning(f"Chat template failed for {self.model_name}, using fallback: {e}")
            # 템플릿 적용 실패 시 기본 형식을 사용합니다.
            prompt = ""
            for message in chat_format:
                prompt += f"<|{message['role']}|>\n{message['content']}\n"
            return prompt + "<|assistant|>\n"

    @property
    def _llm_type(self) -> str:
        """모델의 타입을 식별하기 위한 식별자입니다."""
        return "triton_chat"

