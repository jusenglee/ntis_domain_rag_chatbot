import inspect
import logging
import time
from typing import Any, AsyncIterator, List, Optional

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

logger = logging.getLogger(__name__)

STREAM_FIELD_KEY = "stream_field"          # "content" | "reasoning"
STREAM_REASONING_KEY = "reasoning_text"    # additional_kwargs에만 저장


class OpenAICompatStreamError(RuntimeError):
    """OpenAI 호환 모델 스트리밍 계약 위반 계열 기본 예외"""


class EmptyStreamContentError(OpenAICompatStreamError):
    """스트림에서 실제 텍스트(content) 청크를 수신하지 못했을 때 발생"""


class OpenAICompatChatModel(BaseChatModel):
    """OpenAI 호환 API(vLLM 등)용 LangChain 래퍼"""

    # 기본 연결 설정
    model_name: str = "/model"
    base_url: str = "http://vllm_solar:8010/v1"
    api_key: str = "EMPTY"
    timeout: float = 120.0

    # 기본 샘플링 설정
    default_temperature: float = 0.2
    default_top_p: float = 0.8

    # Solar/vLLM reasoning 제어(기본: thinking 끄기 + reasoning 출력 포함 안 함)
    # - reasoning_effort: "low" | "medium" | "high" | None
    # - include_reasoning: True | False | None  (vLLM 프로토콜에 존재)
    default_reasoning_effort: Optional[str] = "low"
    default_include_reasoning: Optional[bool] = False

    # stream에서 reasoning delta가 먼저 와도 최종 답변(content)에 섞이지 않게:
    # - True  : reasoning을 "이벤트"로는 내보내되(content=""), reasoning 텍스트는 additional_kwargs에만 담음(추천)
    # - False : reasoning 이벤트 자체를 내보내지 않음(클라이언트는 content 나올 때까지 무응답처럼 보일 수 있음)
    emit_reasoning_events: bool = True

    _client: Optional[AsyncOpenAI] = PrivateAttr(default=None)

    # ---- LangChain required ----
    def _generate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        raise NotImplementedError("Use ainvoke/astream")

    @property
    def _llm_type(self) -> str:
        return "openai_compat_chat"

    # ---- internal helpers ----
    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    @staticmethod
    def _delta_get(delta: Any, key: str) -> Any:
        if delta is None:
            return None
        if isinstance(delta, dict):
            return delta.get(key)
        return getattr(delta, key, None)

    @staticmethod
    def _resolve_trace_ids(kwargs: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        request_id = kwargs.get("request_id")
        conversation_id = kwargs.get("conversation_id")

        config = kwargs.get("config")
        metadata = kwargs.get("metadata")
        if metadata is None and isinstance(config, dict):
            metadata = config.get("metadata")

        if isinstance(metadata, dict):
            request_id = request_id or metadata.get("request_id")
            conversation_id = conversation_id or metadata.get("conversation_id")

        request_id_str = str(request_id).strip() if request_id is not None else ""
        conversation_id_str = str(conversation_id).strip() if conversation_id is not None else ""
        return (request_id_str or None, conversation_id_str or None)

    def _to_openai_messages(self, messages: List[BaseMessage]) -> List[dict[str, str]]:
        converted: List[dict[str, str]] = []
        for m in messages:
            if isinstance(m, SystemMessage):
                role = "system"
            elif isinstance(m, HumanMessage):
                role = "user"
            else:
                # AIMessage 포함 (그 외 BaseMessage도 안전하게 assistant로 처리)
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
        """
        vLLM(OpenAI-compatible)에서 비표준/확장 필드는 extra_body로 넣는 게 가장 안전함.
        - reasoning_effort/include_reasoning: vLLM OpenAI 프로토콜 필드
        - chat_template_kwargs.thinking/enable_thinking: vLLM reasoning 예시에 등장
        """
        extra_body = kwargs.get("extra_body")
        body: dict[str, Any] = dict(extra_body) if isinstance(extra_body, dict) else {}

        # --- reasoning controls ---
        reasoning_effort = kwargs.get("reasoning_effort", self.default_reasoning_effort)
        if reasoning_effort is not None:
            body.setdefault("reasoning_effort", reasoning_effort)

        include_reasoning = kwargs.get("include_reasoning", self.default_include_reasoning)
        if include_reasoning is not None:
            body.setdefault("include_reasoning", include_reasoning)

        # --- chat template hints (vLLM examples) ---
        # 일부 서버/모델에서 키가 다를 수 있어 thinking + enable_thinking 둘 다 넣어 호환성 확보
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
        except Exception as exc:
            logger.warning(
                "[openai_compat_llm] stream close failed: request_id=%s stream_type=%s has_aclose=%s has_close=%s",
                request_id,
                stream_type,
                has_aclose,
                has_close,
                exc_info=exc,
            )
            raise

    # ---- non-stream ----
    async def _agenerate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        client = self._get_client()
        request_id, conversation_id = self._resolve_trace_ids(kwargs)

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

        msg = response.choices[0].message if response.choices else None
        content = (getattr(msg, "content", None) if msg is not None else None) or ""
        # reasoning은 섞지 않고 참고용으로만 (필요시 로그/메트릭으로 사용)
        reasoning = (
                (getattr(msg, "reasoning", None) if msg is not None else None)
                or (getattr(msg, "reasoning_content", None) if msg is not None else None)
                or ""
        )

        dt_ms = (time.monotonic() - t0) * 1000
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None

        logger.info(
            "[openai_compat_llm] non-stream summary: request_id=%s conversation_id=%s model=%s dt_ms=%.1f message_n=%d content_char_n=%d reasoning_char_n=%d request_max_tokens=%s usage=%s base_url=%s",
            request_id,
            conversation_id,
            self.model_name,
            dt_ms,
            len(messages),
            len(content),
            len(reasoning),
            request_kwargs.get("max_tokens"),
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

    # ---- stream ----
    async def _astream(
            self,
            messages: List[BaseMessage],
            stop: Optional[List[str]] = None,
            **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
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

        # metrics
        chunk_n = 0
        emitted_any_chunk_n = 0
        emitted_content_chunk_n = 0
        emitted_reasoning_event_n = 0

        content_char_n = 0
        reasoning_char_n = 0

        ttft_any_ms: Optional[float] = None
        ttft_content_ms: Optional[float] = None
        last_finish_reason = None

        closed = False
        primary_exc: Optional[BaseException] = None

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

                # ttft_any: content/reasoning 중 뭐든 처음 도착한 시점
                if ttft_any_ms is None and (content or reasoning):
                    ttft_any_ms = (time.monotonic() - t0) * 1000

                # reasoning-only 이벤트: 최종 content에 섞지 않도록 content=""로 내보내고,
                # reasoning 텍스트는 additional_kwargs에만 실어 보냄.
                if reasoning and not content:
                    reasoning_char_n += len(reasoning)
                    emitted_any_chunk_n += 1
                    if self.emit_reasoning_events:
                        emitted_reasoning_event_n += 1
                        yield ChatGenerationChunk(
                            message=AIMessageChunk(
                                content="",  # 중요: 최종 답변 문자열에 섞이지 않게
                                additional_kwargs={
                                    STREAM_FIELD_KEY: "reasoning",
                                    STREAM_REASONING_KEY: reasoning,
                                },
                            )
                        )
                    continue

                # content 이벤트
                if content:
                    if ttft_content_ms is None:
                        ttft_content_ms = (time.monotonic() - t0) * 1000
                    emitted_any_chunk_n += 1
                    emitted_content_chunk_n += 1
                    content_char_n += len(content)

                    yield ChatGenerationChunk(
                        message=AIMessageChunk(
                            content=content,
                            additional_kwargs={STREAM_FIELD_KEY: "content"},
                        )
                    )

            if emitted_content_chunk_n == 0:
                raise EmptyStreamContentError(
                    "No content emitted in stream. "
                    f"model={self.model_name}, base_url={self.base_url}, request_kwargs={request_kwargs}, extra_body={extra_body}"
                )

        except BaseException as exc:
            primary_exc = exc
            raise
        finally:
            try:
                await self._close_stream(stream, request_id=request_id)
                closed = True
            except Exception:
                # close 실패는 _close_stream에서 로깅/raise 처리
                if primary_exc is None:
                    raise

            dt_ms = (time.monotonic() - t0) * 1000
            logger.info(
                "[openai_compat_llm] stream summary: request_id=%s conversation_id=%s dt_ms=%.1f ttft_any_ms=%s ttft_content_ms=%s "
                "chunk_n=%d emitted_any_chunk_n=%d emitted_content_chunk_n=%d emitted_reasoning_event_n=%d "
                "content_char_n=%d reasoning_char_n=%d request_max_tokens=%s finish_reason=%s closed=%s model=%s base_url=%s",
                request_id,
                conversation_id,
                dt_ms,
                f"{ttft_any_ms:.1f}" if ttft_any_ms is not None else "none",
                f"{ttft_content_ms:.1f}" if ttft_content_ms is not None else "none",
                chunk_n,
                emitted_any_chunk_n,
                emitted_content_chunk_n,
                emitted_reasoning_event_n,
                content_char_n,
                reasoning_char_n,
                request_kwargs.get("max_tokens"),
                last_finish_reason,
                closed,
                self.model_name,
                self.base_url,
            )
