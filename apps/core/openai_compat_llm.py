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

# ???섑띁??vLLM/OpenAI-compat ?묐떟??LangChain 硫붿떆吏濡??뺢퇋?뷀븳??
# ?뱁엳 reasoning delta瑜?content? 遺꾨━??downstream ?ㅽ듃由щ컢 怨꾩링??# ?덉쟾?섍쾶 ?꾪꽣留곹븷 ???덈룄濡?`stream_field` 洹쒖빟??遺숈뿬二쇰뒗 寃껋씠 以묒슂?섎떎.

STREAM_FIELD_KEY = "stream_field"          # "content" | "reasoning"
STREAM_REASONING_KEY = "reasoning_text"    # additional_kwargs?먮쭔 ???

class OpenAICompatStreamError(RuntimeError):
    """OpenAI ?명솚 ?ㅽ듃由쇱뿉??援ъ“媛 源⑥?嫄곕굹 ?꾩닔 ?꾨뱶媛 鍮꾩뿀?????곕뒗 ?덉쇅??
    ?곸쐞 ?몄텧?먮뒗 ???덉쇅瑜??듯빐 provider ?묐떟??臾댄슚?덈떎???ъ떎???쇰컲 ??꾩븘?껉낵 援щ텇?????덈떎.
    """


class EmptyStreamContentError(OpenAICompatStreamError):
    """streaming ?묐떟???앸궗?붾뜲???ъ슜?먯뿉寃?諛쒗뻾??content媛 ?꾪? ?놁쓣 ??諛쒖깮?쒗궎???덉쇅??
    ?댁쑀? trace id瑜??④퍡 蹂댁〈??stream triage?먯꽌 鍮??묐떟???먯씤???곕줈 異붿쟻?????덇쾶 ?쒕떎.
    """


class OpenAICompatChatModel(BaseChatModel):
    """LangChain ChatModel 怨꾩빟??OpenAI ?명솚 provider ?꾩뿉 ?뱀? ?대뙌?곕떎.
    non-stream, stream, reasoning content 遺꾨━, trace id ?섏쭛, provider蹂?extra body 援ъ꽦????怨녹뿉???ㅻ，??
    """
    """OpenAI ?명솚 API(vLLM ????LangChain ?섑띁"""

    # 湲곕낯 ?곌껐 ?ㅼ젙
    model_name: str = "/model"
    base_url: str = "http://vllm_solar:8010/v1"
    api_key: str = "EMPTY"
    timeout: float = 120.0

    # 湲곕낯 ?섑뵆留??ㅼ젙
    default_temperature: float = 0.2
    default_top_p: float = 0.8

    # Solar/vLLM reasoning ?쒖뼱(湲곕낯: thinking ?꾧린 + reasoning 異쒕젰 ?ы븿 ????
    # - reasoning_effort: "low" | "medium" | "high" | None
    # - include_reasoning: True | False | None  (vLLM ?꾨줈?좎퐳??議댁옱)
    default_reasoning_effort: Optional[str] = "low"
    default_include_reasoning: Optional[bool] = False

    # stream?먯꽌 reasoning delta媛 癒쇱? ???理쒖쥌 ?듬?(content)???욎씠吏 ?딄쾶:
    # - True  : reasoning??"?대깽??濡쒕뒗 ?대낫?대릺(content=""), reasoning ?띿뒪?몃뒗 additional_kwargs?먮쭔 ?댁쓬(異붿쿇)
    # - False : reasoning ?대깽???먯껜瑜??대낫?댁? ?딆쓬(?대씪?댁뼵?몃뒗 content ?섏삱 ?뚭퉴吏 臾댁쓳?듭쿂??蹂댁씪 ???덉쓬)
    emit_reasoning_events: bool = True

    _client: Optional[AsyncOpenAI] = PrivateAttr(default=None)

    # ---- LangChain required ----
    def _generate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        """LangChain???숆린 generate 怨꾩빟??non-stream invoke 寃쎈줈濡??곌껐?쒕떎.
        ?대??곸쑝濡쒕뒗 `ainvoke_non_stream`???몄텧?섍퀬, 諛섑솚??text? trace ?뺣낫瑜?`ChatResult` ?뺥깭濡??ы룷?ν븳??
        """
        raise NotImplementedError("Use ainvoke/astream")

    @property
    def _llm_type(self) -> str:
        """LangChain????紐⑤뜽??援щ텇???????덉젙?곸씤 type name???뚮젮以??
        provider 李⑥씠? 臾닿??섍쾶 媛숈? adapter濡??몄떇?섍쾶 ??tracing怨?serialization???⑥닚?뷀븳??
        """
        return "openai_compat_chat"

    # ---- internal helpers ----
    def _get_client(self) -> AsyncOpenAI:
        """provider base URL怨?API key濡?OpenAI ?명솚 async client瑜?吏???앹꽦?쒕떎.
        媛숈? 紐⑤뜽 ?몄뒪?댁뒪?먯꽌????踰?留뚮뱺 client瑜??ъ궗?⑺븯???곌껐 ?ㅻ쾭?ㅻ뱶瑜?以꾩씤??
        """
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
            )
        return self._client

    async def aclose(self) -> None:
        """?대? async client? 留ㅻ떖???덈뒗 transport ?먯썝???뺣━?쒕떎.
        ?쒕퉬??醫낅즺???뚯뒪??留덈Т由??쒖젏??connection leak???④린吏 ?딄쾶 ?섎뒗 醫낃껐 ?꾩쿂由щ떎.
        """
        if self._client is not None:
            await self._client.close()
            self._client = None

    @staticmethod
    def _delta_get(delta: Any, key: str) -> Any:
        """stream chunk?먯꽌 provider蹂?delta field瑜??덉쟾?섍쾶 媛?몄삩??
        OpenAI ?명솚 API?쇨퀬 ?대룄 chunk shape媛 誘몃쵖?섍쾶 ?ㅻ? ???덉뼱 ???ы띁媛 ?꾨뱶 ?묎렐 李⑥씠瑜??≪닔?쒕떎.
        """
        if delta is None:
            return None
        if isinstance(delta, dict):
            return delta.get(key)
        return getattr(delta, key, None)

    @staticmethod
    def _resolve_trace_ids(kwargs: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """response header? body?먯꽌 異붿쟻 媛?ν븳 trace/request id瑜??섏쭛?쒕떎.
        鍮??묐떟?대굹 provider ?ㅻ쪟瑜?triage????媛숈? ?몄텧???ㅼ떆 李얠쓣 ???덇쾶 ?섎뒗 吏꾨떒 硫뷀??곗씠?곕떎.
        """
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
        """LangChain message 紐⑸줉??OpenAI chat completion 硫붿떆吏 ?뺤떇?쇰줈 蹂?섑븳??
        system/human/assistant/tool role??蹂댁〈?섍퀬, provider媛 紐??쎈뒗 遺媛 ?꾨뱶???쒓굅?섏뿬 ?붿껌 body瑜?媛꾧껐?섍쾶 ?좎??쒕떎.
        """
        converted: List[dict[str, str]] = []
        for m in messages:
            if isinstance(m, SystemMessage):
                role = "system"
            elif isinstance(m, HumanMessage):
                role = "user"
            else:
                # AIMessage ?ы븿 (洹???BaseMessage???덉쟾?섍쾶 assistant濡?泥섎━)
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
        """model, messages, timeout, streaming ?뚮옒洹몃? provider ?붿껌 kwargs濡?議고빀?쒕떎.
        怨듯넻 body? provider-specific extra body瑜?遺꾨━??reasoning ?듭뀡???쇰컲 chat payload瑜??ㅼ뿼?쒗궎吏 ?딄쾶 ?쒕떎.
        """
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
        """provider蹂꾨줈 ?덉슜?섎뒗 異붽? request body瑜??좊퀎??留뚮뱺??
        reasoning effort, parallel tool calls, response format 媛숈? ?좏깮 ?듭뀡???명솚 踰붿쐞 ?댁뿉?쒕쭔 ?ｌ뼱 蹂대궦??
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

        top_k = kwargs.get("top_k")
        if top_k is not None:
            body.setdefault("top_k", int(top_k))

        # --- chat template hints (vLLM examples) ---
        # ?쇰? ?쒕쾭/紐⑤뜽?먯꽌 ?ㅺ? ?ㅻ? ???덉뼱 thinking + enable_thinking ?????ｌ뼱 ?명솚???뺣낫
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
        """provider stream object媛 ?몄텧??aclose/close ?꾪겕瑜??덉쟾?섍쾶 ?몄텧?쒕떎.
        ?덉쇅 ?꾩쨷?먮룄 stream transport媛 ?⑥? ?딄쾶 ?뺣━瑜?蹂댁옣?섎뒗 ?꾩쿂由??ы띁??
        """
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
        """LangChain??鍮꾨룞湲?generate 怨꾩빟??non-stream invoke 寃쎈줈濡??곌껐?쒕떎.
        `_generate`? 媛숈? 寃곌낵 shape瑜?留욎텛?? event loop ?덉뿉??吏곸젒 await 媛?ν븯寃??쒓났?쒕떎.
        """
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
        # reasoning? ?욎? ?딄퀬 李멸퀬?⑹쑝濡쒕쭔 (?꾩슂??濡쒓렇/硫뷀듃由?쑝濡??ъ슜)
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
        """streaming???ъ슜?섏? ?딅뒗 ?쇰컲 chat completion ?몄텧???ㅽ뻾?쒕떎.
        provider ?묐떟?먯꽌 answer text, reasoning text, usage, trace id瑜?遺꾨━???곸쐞 ?덉씠?닿? 洹몃?濡??뚮퉬?????덈뒗 ?뺤뀛?덈━濡??뚮젮以??
        """
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
        """OpenAI ?명솚 streaming ?묐떟??LangChain `ChatGenerationChunk` ?먮쫫?쇰줈 諛붽씔??
        reasoning content? user-visible content瑜?援щ텇???꾩쟻?섍퀬, 鍮?肄섑뀗痢?醫낅즺??`EmptyStreamContentError`濡?蹂?섑빐 ?곸쐞???뚮┛??
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

                # ttft_any: content/reasoning 以?萸먮뱺 泥섏쓬 ?꾩갑???쒖젏
                if ttft_any_ms is None and (content or reasoning):
                    ttft_any_ms = (time.monotonic() - t0) * 1000

                # reasoning-only ?대깽?? 理쒖쥌 content???욎? ?딅룄濡?content=""濡??대낫?닿퀬,
                # reasoning ?띿뒪?몃뒗 additional_kwargs?먮쭔 ?ㅼ뼱 蹂대깂.
                if reasoning and not content:
                    reasoning_char_n += len(reasoning)
                    emitted_any_chunk_n += 1
                    if self.emit_reasoning_events:
                        emitted_reasoning_event_n += 1
                        yield ChatGenerationChunk(
                            message=AIMessageChunk(
                                content="",  # 以묒슂: 理쒖쥌 ?듬? 臾몄옄?댁뿉 ?욎씠吏 ?딄쾶
                                additional_kwargs={
                                    STREAM_FIELD_KEY: "reasoning",
                                    STREAM_REASONING_KEY: reasoning,
                                },
                            )
                        )
                    continue

                # content ?대깽??                if content:
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
                # 怨꾩빟 ?꾨컲?쇰줈 媛꾩＜: ?ㅽ듃由쇱? ?앸궗吏留??ъ슜?먯뿉寃??꾨떖 媛?ν븳 content ?좏겙??0媛?
                # ?ш린?쒕뒗 ?ъ떆???대갚???섑뻾?섏? ?딄퀬 EmptyStreamContentError瑜??곸쐞濡??꾪뙆?쒕떎.
                # ?ㅼ젣 ?ъ떆???잛닔/?대갚 諛⑹떇(?? non-stream ?몄텧)? ?곸쐞 ?ㅼ??ㅽ듃?덉씠???덉씠?댁뿉??寃곗젙?쒕떎.
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
                # close ?ㅽ뙣??_close_stream?먯꽌 濡쒓퉭/raise 泥섎━
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

