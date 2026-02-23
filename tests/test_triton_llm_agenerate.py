from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Generator, List, Optional, cast


class BaseChatModel:
    pass


class BaseMessage:
    def __init__(self, content: str = "") -> None:
        self.content = content


class AIMessage(BaseMessage):
    pass


class HumanMessage(BaseMessage):
    pass


class SystemMessage(BaseMessage):
    pass


class AIMessageChunk(BaseMessage):
    pass


class ChatGeneration:
    def __init__(self, message: AIMessage) -> None:
        self.message = message


class ChatGenerationChunk:
    def __init__(self, message: AIMessageChunk) -> None:
        self.message = message


class ChatResult:
    def __init__(self, generations: List[ChatGeneration]) -> None:
        self.generations = generations


def _load_triton_chat_model(triton_infer_impl):
    source = Path("triton_llm.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="triton_llm.py")

    class_node = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "TritonChatModel"
    )

    module = ast.Module(body=[class_node], type_ignores=[])
    ast.fix_missing_locations(module)

    namespace = {
        "Any": Any,
        "List": List,
        "Optional": Optional,
        "AsyncIterator": AsyncIterator,
        "Generator": Generator,
        "cast": cast,
        "BaseChatModel": BaseChatModel,
        "BaseMessage": BaseMessage,
        "AIMessage": AIMessage,
        "HumanMessage": HumanMessage,
        "SystemMessage": SystemMessage,
        "AIMessageChunk": AIMessageChunk,
        "ChatResult": ChatResult,
        "ChatGeneration": ChatGeneration,
        "ChatGenerationChunk": ChatGenerationChunk,
        "triton_infer": triton_infer_impl,
        "get_tokenizer_for_model": lambda *_: None,
    }
    exec(compile(module, filename="triton_llm.py", mode="exec"), namespace)
    return namespace["TritonChatModel"]


def test_agenerate_maps_non_stream_string_to_aimessage() -> None:
    captured: dict[str, Any] = {}

    def _fake_triton_infer(model_name, prompt, *, stream=True, max_tokens=None, **kwargs):
        captured["model_name"] = model_name
        captured["prompt"] = prompt
        captured["stream"] = stream
        captured["max_tokens"] = max_tokens
        assert stream is False
        return "테스트 응답"

    model_cls = _load_triton_chat_model(_fake_triton_infer)
    model = model_cls()
    model._format_messages = lambda _: "formatted-prompt"

    result = asyncio.run(
        model._agenerate([HumanMessage(content="질문")], max_tokens=128)
    )

    assert captured == {
        "model_name": model.model_name,
        "prompt": "formatted-prompt",
        "stream": False,
        "max_tokens": 128,
    }
    assert result.generations
    generated_message = result.generations[0].message
    assert isinstance(generated_message, AIMessage)
    assert generated_message.content == "테스트 응답"
