from typing import Any, List, Optional, AsyncIterator
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, SystemMessage, AIMessageChunk
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk

# 湲곗〈 肄붾뱶???⑥닔 ?꾪룷??(寃쎈줈???섍꼍??留욊쾶 議곗젙?섏꽭??
from apps.core.triton_client import triton_infer, get_tokenizer_for_model

class TritonChatModel(BaseChatModel):
    """LangChain ?명꽣?섏씠?ㅻ줈 Triton LLM??媛먯떥??梨꾪똿 紐⑤뜽 ?대뙌?곕떎."""
    model_name: str = "gpt_oss_0"

    def _generate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        # ?숆린 ?몄텧? 援ы쁽 ?앸왂 (?꾩슂 ??異붽?)
        """?숆린 ?앹꽦 寃쎈줈??吏?먰븯吏 ?딄퀬, ?몄텧 ??紐낆떆?곸쑝濡?留됰뒗??"""
        raise NotImplementedError("Use astream for streaming")

    async def _agenerate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        # 鍮꾨룞湲??몄텧 (?ㅽ듃由щ컢 ?놁씠 寃곌낵留?諛섑솚)
        """鍮꾩뒪?몃━諛?Triton ?몄텧 寃곌낵瑜???踰덉뿉 紐⑥븘 ChatResult濡?諛섑솚?쒕떎."""
        prompt = self._format_messages(messages)
        full_text = ""
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))
        # stream=False濡??몄텧
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

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=full_text))])

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        """Triton???숆린 generator瑜?executor濡?媛먯떥 LangChain ?ㅽ듃由?泥?겕濡??섎젮蹂대궦??

        ?대젃寃??댁빞 ?대깽??猷⑦봽瑜?留됱? ?딄퀬??湲곗〈 Triton client瑜?洹몃?濡??ъ궗?⑺븷 ???덈떎.
        """
        prompt = self._format_messages(messages)
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))

        # Triton generator瑜?鍮꾨룞湲?猷⑦봽?먯꽌 ?ㅽ뻾 (block 諛⑹?)
        import asyncio
        loop = asyncio.get_running_loop()

        # stream=True濡?泥?겕 ?⑥쐞 寃곌낵瑜?諛쏅뒗??
        gen = triton_infer(
            self.model_name,
            prompt,
            stream=True,
            max_tokens=max_tokens_hint,
            temperature=kwargs.get("temperature"),
            top_p=kwargs.get("top_p"),
            top_k=kwargs.get("top_k"),
        )

        try:
            while True:
                # next(gen)???ㅻ젅????먯꽌 ?ㅽ뻾
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
        """LangChain message 紐⑸줉??Triton tokenizer媛 ?댄빐?섎뒗 chat prompt濡?蹂?섑븳??

        chat template ?곸슜???ㅽ뙣?섎㈃ role marker 湲곕컲 fallback prompt瑜?留뚮뱾???몄텧 ?먯껜??怨꾩냽 吏꾪뻾?쒕떎.
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
            # ?쒗뵆由??곸슜 ?ㅽ뙣 ??Fallback
            prompt = ""
            for m in chat_format:
                prompt += f"<|{m['role']}|>\n{m['content']}\n"
            return prompt + "<|assistant|>\n"

    @property
    def _llm_type(self) -> str:
        """LangChain??紐⑤뜽 醫낅쪟瑜??앸퀎?????곕뒗 怨좎젙 臾몄옄?댁쓣 ?뚮젮以??"""
        return "triton_chat"


