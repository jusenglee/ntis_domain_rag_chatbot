from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from langchain_core.documents import Document
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
    from langchain_core.output_parsers import PydanticOutputParser
    from langchain_core.prompts import ChatPromptTemplate
except ModuleNotFoundError:
    @dataclass
    class BaseMessage:
        content: str = ""
        additional_kwargs: dict[str, Any] = field(default_factory=dict)


    @dataclass
    class SystemMessage(BaseMessage):
        pass


    @dataclass
    class HumanMessage(BaseMessage):
        pass


    @dataclass
    class AIMessage(BaseMessage):
        pass


    @dataclass
    class Document:
        page_content: str = ""
        metadata: dict[str, Any] = field(default_factory=dict)


    class _CompatRunnableChain:
        def __init__(self, steps: list[Any] | None = None) -> None:
            self.steps = list(steps or [])

        def __or__(self, other: Any) -> "_CompatRunnableChain":
            return _CompatRunnableChain([*self.steps, other])

        async def ainvoke(self, *_args: Any, **_kwargs: Any) -> Any:
            raise ModuleNotFoundError(
                "langchain_core is required for runnable prompt/LLM execution. "
                "The compatibility shim only supports import-time fallback for tests."
            )


    class ChatPromptTemplate:
        def __init__(self, messages: list[Any]) -> None:
            self.messages = list(messages)

        @classmethod
        def from_messages(cls, messages: list[Any]) -> "ChatPromptTemplate":
            return cls(list(messages))

        def __or__(self, other: Any) -> _CompatRunnableChain:
            return _CompatRunnableChain([self, other])


    class PydanticOutputParser:
        def __init__(self, *, pydantic_object: Any) -> None:
            self.pydantic_object = pydantic_object

        def get_format_instructions(self) -> str:
            return "JSON"

        def parse(self, text: str) -> Any:
            if hasattr(self.pydantic_object, "model_validate_json"):
                return self.pydantic_object.model_validate_json(text)
            if hasattr(self.pydantic_object, "parse_raw"):
                return self.pydantic_object.parse_raw(text)
            raise ModuleNotFoundError(
                "langchain_core is unavailable and the compatibility parser cannot validate this payload."
            )
