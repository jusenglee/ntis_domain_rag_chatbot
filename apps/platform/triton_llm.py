from typing import Any, AsyncIterator, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from apps.platform.triton_client import get_tokenizer_for_model, triton_infer


class TritonChatModel(BaseChatModel):
    """LangChain chat-model adapter for Triton-backed LLMs."""

    model_name: str = "gpt_oss_0"

    def _generate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        """Disable sync generation and force callers onto the streaming path."""
        raise NotImplementedError("Use astream for streaming")

    async def _agenerate(self, messages: List[BaseMessage], **kwargs: Any) -> ChatResult:
        """Collect the Triton generator into a single chat result."""
        prompt = self._format_messages(messages)
        full_text = ""
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))
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

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Bridge the blocking Triton generator into LangChain async chunks."""
        prompt = self._format_messages(messages)
        max_tokens_hint = kwargs.get("max_tokens_hint", kwargs.get("max_tokens"))

        import asyncio

        loop = asyncio.get_running_loop()
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
                chunk = await loop.run_in_executor(None, next, gen, None)
                if chunk is None:
                    break

                text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="ignore")
                if text:
                    yield ChatGenerationChunk(message=AIMessageChunk(content=text))
        finally:
            try:
                gen.close()
            except Exception:
                pass

    def _format_messages(self, messages: List[BaseMessage]) -> str:
        """Render messages through the tokenizer chat template when available."""
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
            return tokenizer.apply_chat_template(chat_format, tokenize=False, add_generation_prompt=True)
        except Exception:
            prompt = ""
            for message in chat_format:
                prompt += f"<|{message['role']}|>\n{message['content']}\n"
            return prompt + "<|assistant|>\n"

    @property
    def _llm_type(self) -> str:
        """Return the LangChain model type identifier."""
        return "triton_chat"
