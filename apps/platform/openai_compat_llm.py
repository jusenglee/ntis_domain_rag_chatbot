import inspect
import json
import time
from typing import Any, AsyncIterator, List, Optional

from loguru import logger
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from openai import AsyncOpenAI
from pydantic import PrivateAttr

"""
OpenAI 호환 API를 위한 LangChain ChatModel 어댑터입니다.
이 모듈은 vLLM, Solar API 등 OpenAI의 규약을 따르는 다양한 추론 엔진을 
LangChain 생태계와 연결해 주는 역할을 합니다.

주요 기능:
1. 메시지 정규화: LangChain의 메시지 형식을 OpenAI 규격에 맞게 변환합니다.
2. 사고 과정(Reasoning) 처리: 모델이 생성하는 추론 과정(Thinking)과 실제 답변(Content)을 분리하여 관리합니다.
3. 스트리밍 지원: 실시간으로 토큰을 전달하며, 추론 이벤트와 콘텐츠 이벤트를 구분해 발행합니다.
4. 오류 추적: 요청 ID와 대화 ID를 통해 문제 발생 시 원인을 추적할 수 있는 메타데이터를 관리합니다.
"""

STREAM_FIELD_KEY = "stream_field"          # 응답의 종류 구분 ("content" | "reasoning")
STREAM_REASONING_KEY = "reasoning_text"    # 사고 과정 텍스트를 저장할 키


class OpenAICompatStreamError(RuntimeError):
    """
    OpenAI 호환 스트림 처리 중 발생하는 일반적인 오류입니다.
    데이터 구조가 깨지거나 예상치 못한 응답이 올 때 사용합니다.
    """


class EmptyStreamContentError(OpenAICompatStreamError):
    """
    스트리밍이 완료되었으나 실질적인 답변 내용(Content)이 없는 경우 발생하는 오류입니다.
    모델이 답변을 생성하지 못했거나 필터링되었을 때의 추적을 위해 사용합니다.
    """


class OpenAICompatChatModel(BaseChatModel):
    """
    OpenAI 호환 API 서버를 위한 LangChain 래퍼 클래스입니다.
    vLLM이나 Solar와 같은 외부 추론 서버를 LangChain의 표준 방식으로 호출할 수 있게 합니다.
    """

    # 기본 모델 및 접속 정보 설정
    model_name: str = "/model"
    base_url: str = "http://vllm_solar:8010/v1"
    api_key: str = "EMPTY"
    # 2026-05-27: thinking 모드 활성 호출(PlannerAgent / LLMJudgeChecker)은 분 단위 응답 시간을 가질 수
    # 있어 client 전체 타임아웃을 충분히 길게 둔다. RAG_LLM_TIMEOUT_SECONDS 환경변수로 운영 토글.
    timeout: float = 600.0

    # 기본 샘플링 설정
    default_temperature: float = 0.2
    default_top_p: float = 0.8

    # 사고 과정(Reasoning) 제어 설정
    default_reasoning_effort: Optional[str] = "low"
    default_include_reasoning: Optional[bool] = False

    # 스트림에서 사고 과정 이벤트를 발행할지 여부
    emit_reasoning_events: bool = True

    _client: Optional[AsyncOpenAI] = PrivateAttr(default=None)

    def _generate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        """LangChain의 동기 호출 방식을 지원하기 위한 메서드입니다.
        현재는 비동기 방식(ainvoke/astream) 사용을 권장합니다.
        """
        raise NotImplementedError("ainvoke 또는 astream을 사용해 주세요.")

    @property
    def _llm_type(self) -> str:
        """LangChain 내에서 이 모델을 식별하기 위한 타입 이름입니다."""
        return "openai_compat_chat"

    def _get_client(self) -> AsyncOpenAI:
        """OpenAI 호환 비동기 클라이언트를 지연 생성(Lazy Initialization)하여 반환합니다."""
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
            )
        return self._client

    async def aclose(self) -> None:
        """할당된 HTTP 클라이언트 자원을 해제합니다."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    @staticmethod
    def _delta_get(delta: Any, key: str) -> Any:
        """스트림 청크(Delta)에서 특정 키의 값을 안전하게 추출합니다.
        객체나 딕셔너리 형태에 관계없이 동작하도록 설계되었습니다.
        """
        if delta is None:
            return None
        if isinstance(delta, dict):
            return delta.get(key)
        return getattr(delta, key, None)

    @staticmethod
    def _resolve_trace_context(kwargs: dict[str, Any]) -> dict[str, Optional[str]]:
        """로깅 및 추적을 위해 실행 문맥(Metadata) 정보를 수집합니다."""
        config = kwargs.get("config")
        metadata = kwargs.get("metadata")
        if metadata is None and isinstance(config, dict):
            metadata = config.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        def pick(key: str) -> Optional[str]:
            value = kwargs.get(key)
            if value is None:
                value = metadata.get(key)
            text = str(value).strip() if value is not None else ""
            return text or None

        return {
            "request_id": pick("request_id"),
            "conversation_id": pick("conversation_id"),
            "planner_stage": pick("planner_stage"),
            "planner_prompt_version": pick("planner_prompt_version"),
            "turn_id": pick("turn_id"),
        }

    @staticmethod
    def _resolve_trace_ids(kwargs: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """요청 ID와 대화 ID를 추출하여 반환합니다."""
        trace_context = OpenAICompatChatModel._resolve_trace_context(kwargs)
        return trace_context["request_id"], trace_context["conversation_id"]

    def _to_openai_messages(self, messages: List[BaseMessage]) -> List[dict[str, str]]:
        """LangChain 메시지 객체를 OpenAI API 규격의 딕셔너리 형태로 변환합니다."""
        converted: List[dict[str, str]] = []
        for m in messages:
            if isinstance(m, SystemMessage):
                role = "system"
            elif isinstance(m, HumanMessage):
                role = "user"
            else:
                role = "assistant"
            converted.append({"role": role, "content": str(m.content)})
        return converted

    def _build_openai_request_kwargs(
            self,
            *,
            request_id: Optional[str],
            stop: Optional[List[str]],
            kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """OpenAI 클라이언트 호출에 필요한 매개변수 꾸러미를 구성합니다."""
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))
        request_kwargs: dict[str, Any] = {
            "temperature": kwargs.get("temperature", self.default_temperature),
            "top_p": kwargs.get("top_p", self.default_top_p),
        }
        if max_tokens_hint is not None:
            request_kwargs["max_tokens"] = max_tokens_hint
            request_kwargs["max_completion_tokens"] = max_tokens_hint
        if stop:
            request_kwargs["stop"] = stop
        if request_id:
            request_kwargs["extra_headers"] = {"x-request-id": request_id}
        return request_kwargs

    def _build_extra_body(self, kwargs: dict[str, Any]) -> Optional[dict[str, Any]]:
        """표준 사양 외에 vLLM이나 Solar 전용으로 전달할 추가 파라미터를 구성합니다."""
        extra_body = kwargs.get("extra_body")
        body: dict[str, Any] = dict(extra_body) if isinstance(extra_body, dict) else {}

        top_k = kwargs.get("top_k")
        if top_k is not None:
            body.setdefault("top_k", int(top_k))

        # 사고 과정(Reasoning) 관련 설정 추가
        reasoning_effort = kwargs.get("reasoning_effort", self.default_reasoning_effort)
        if reasoning_effort is not None:
            body.setdefault("reasoning_effort", reasoning_effort)

        include_reasoning = kwargs.get("include_reasoning", self.default_include_reasoning)
        if include_reasoning is not None:
            body.setdefault("include_reasoning", include_reasoning)

        # 모델 내부의 '사고하기' 기능을 끌지 말지 결정 (RAG에서는 보통 끔)
        chat_template_kwargs = body.get("chat_template_kwargs")
        if not isinstance(chat_template_kwargs, dict):
            chat_template_kwargs = {}

        disable_thinking = kwargs.get("disable_thinking", True)
        if disable_thinking:
            chat_template_kwargs.setdefault("thinking", False)
            chat_template_kwargs.setdefault("enable_thinking", False)

        if chat_template_kwargs:
            body["chat_template_kwargs"] = chat_template_kwargs

        return body or None

    async def _close_stream(self, stream: Any, *, request_id: Optional[str]) -> None:
        """사용이 끝난 스트림 연결을 안전하게 닫습니다."""
        stream_type = type(stream).__name__
        has_aclose = callable(getattr(stream, "aclose", None))
        has_close = callable(getattr(stream, "close", None))
        try:
            if has_aclose:
                await stream.aclose()
            elif has_close:
                res = stream.close()
                if inspect.isawaitable(res):
                    await res
        except Exception:
            logger.exception(
                "stream close failed: request_id={} stream_type={}",
                request_id,
                stream_type,
            )
            raise

    async def _agenerate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        """비동기 방식으로 모델의 답변을 한 번에 가져옵니다. (Non-streaming)"""
        client = self._get_client()
        trace_context = self._resolve_trace_context(kwargs)
        request_id = trace_context["request_id"]
        
        request_kwargs = self._build_openai_request_kwargs(
            request_id=request_id,
            stop=kwargs.get("stop"),
            kwargs=kwargs,
        )
        extra_body = self._build_extra_body(kwargs)

        t0 = time.monotonic()
        response = await client.chat.completions.create(
            model=self.model_name,
            messages=self._to_openai_messages(messages),
            stream=False,
            extra_body=extra_body,
            **request_kwargs,
        )

        choices = getattr(response, "choices", None) or []
        choice0 = choices[0] if choices else None
        msg = choice0.message if choice0 is not None else None
        content = (getattr(msg, "content", None) if msg is not None else None) or ""
        reasoning = (
                (getattr(msg, "reasoning", None) if msg is not None else None)
                or (getattr(msg, "reasoning_content", None) if msg is not None else None)
                or ""
        )

        dt_ms = (time.monotonic() - t0) * 1000
        # 호출 요약 로그 기록 (재현성 및 모니터링용)
        logger.info(
            "non-stream summary: request_id={} model={} dt_ms={:.1f} content_char_n={} reasoning_char_n={}",
            request_id,
            self.model_name,
            dt_ms,
            len(content),
            len(reasoning),
        )
        # LLM 응답 본문 기록 (multiline은 JSON escape하여 단일 라인으로)
        logger.info(
            "non-stream content: request_id={} model={} content={}",
            request_id,
            self.model_name,
            json.dumps(content, ensure_ascii=False),
        )

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    async def ainvoke_non_stream(self, messages: List[BaseMessage], **kwargs: Any) -> AIMessage:
        """스트리밍을 사용하지 않는 일반 비동기 호출을 수행하고 결과 메시지만 반환합니다."""
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
        """모델의 답변을 실시간 스트리밍 방식으로 한 토큰씩 받아옵니다.
        사고 과정과 답변 내용을 구분하여 클라이언트에 전달합니다.
        """
        client = self._get_client()
        request_id, conversation_id = self._resolve_trace_ids(kwargs)

        request_kwargs = self._build_openai_request_kwargs(
            request_id=request_id,
            stop=stop,
            kwargs=kwargs,
        )
        extra_body = self._build_extra_body(kwargs)

        t0 = time.monotonic()
        stream = await client.chat.completions.create(
            model=self.model_name,
            messages=self._to_openai_messages(messages),
            stream=True,
            extra_body=extra_body,
            **request_kwargs,
        )

        chunk_n = 0
        emitted_content_chunk_n = 0
        content_char_n = 0
        reasoning_char_n = 0
        ttft_any_ms: Optional[float] = None
        ttft_content_ms: Optional[float] = None
        last_finish_reason = None
        closed = False
        primary_exc: Optional[BaseException] = None
        content_buffer: List[str] = []

        try:
            async for chunk in stream:
                chunk_n += 1
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue

                choice0 = choices[0]
                last_finish_reason = getattr(choice0, "finish_reason", None) or last_finish_reason
                delta = getattr(choice0, "delta", None)

                content = self._delta_get(delta, "content")
                reasoning = self._delta_get(delta, "reasoning") or self._delta_get(delta, "reasoning_content")

                # 첫 응답 도달 시간(TTFT) 측정
                if ttft_any_ms is None and (content or reasoning):
                    ttft_any_ms = (time.monotonic() - t0) * 1000

                # 사고 과정 데이터 처리
                if reasoning and not content:
                    reasoning_char_n += len(reasoning)
                    if self.emit_reasoning_events:
                        yield ChatGenerationChunk(
                            message=AIMessageChunk(
                                content="",  # 답변 본문에 섞이지 않도록 빈 문자열 전달
                                additional_kwargs={
                                    STREAM_FIELD_KEY: "reasoning",
                                    STREAM_REASONING_KEY: reasoning,
                                },
                            )
                        )
                    continue

                # 실제 답변 본문 데이터 처리
                if content:
                    if ttft_content_ms is None:
                        ttft_content_ms = (time.monotonic() - t0) * 1000
                    emitted_content_chunk_n += 1
                    content_char_n += len(content)
                    content_buffer.append(content)

                    yield ChatGenerationChunk(
                        message=AIMessageChunk(
                            content=content,
                            additional_kwargs={STREAM_FIELD_KEY: "content"},
                        )
                    )

            # 스트림은 끝났는데 답변 내용이 하나도 없는 경우 오류 발생
            if emitted_content_chunk_n == 0:
                raise EmptyStreamContentError(
                    f"No content emitted in stream. model={self.model_name}"
                )

        except BaseException as exc:
            primary_exc = exc
            raise
        finally:
            # 스트림 정리 및 최종 요약 로그 기록
            try:
                await self._close_stream(stream, request_id=request_id)
                closed = True
            except Exception:
                if primary_exc is None:
                    raise

            dt_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "stream summary: request_id={} dt_ms={:.1f} ttft_content_ms={} chunk_n={} content_char_n={} model={}",
                request_id,
                dt_ms,
                f"{ttft_content_ms:.1f}" if ttft_content_ms is not None else "none",
                chunk_n,
                content_char_n,
                self.model_name,
            )
            # LLM 응답 본문 기록 (multiline은 JSON escape하여 단일 라인으로)
            logger.info(
                "stream content: request_id={} model={} content={}",
                request_id,
                self.model_name,
                json.dumps("".join(content_buffer), ensure_ascii=False),
            )
