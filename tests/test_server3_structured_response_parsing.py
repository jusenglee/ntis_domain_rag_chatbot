from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import pytest


@dataclass
class KnowledgeSufficiency:
    requires_new_knowledge: str
    search_intent: str
    retrieval_query: str
    confidence: float


class _FakeParser:
    def invoke(self, value: Any) -> KnowledgeSufficiency:
        raw = value.content if hasattr(value, "content") else str(value)
        payload = json.loads(raw)
        return KnowledgeSufficiency(**payload)


class _AIMessage:
    def __init__(self, content: str):
        self.content = content


def _load_parse_helper(*, sanitize_impl: Optional[Any]) -> Any:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    target = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_structured_response"
    )

    module = ast.Module(body=[target], type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: Dict[str, Any] = {
        "Any": Any,
        "PydanticOutputParser": _FakeParser,
    }
    if sanitize_impl is not None:
        namespace["sanitize_llm_json"] = sanitize_impl

    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    return namespace["_parse_structured_response"]


def test_parse_structured_response_without_fallback_success() -> None:
    helper = _load_parse_helper(
        sanitize_impl=lambda *_: (_ for _ in ()).throw(AssertionError("sanitize should not be called"))
    )
    parser = _FakeParser()

    parsed = helper(
        parser,
        _AIMessage(
            content=(
                '{"requires_new_knowledge":"low",'
                '"search_intent":"기존 문맥으로 충분",'
                '"retrieval_query":"",'
                '"confidence":0.97}'
            )
        ),
        fallback_to_json_extraction=False,
    )

    assert isinstance(parsed, KnowledgeSufficiency)
    assert parsed.requires_new_knowledge == "low"
    assert parsed.search_intent == "기존 문맥으로 충분"
    assert parsed.confidence == 0.97


def test_parse_structured_response_without_fallback_failure() -> None:
    helper = _load_parse_helper(sanitize_impl=None)
    parser = _FakeParser()

    with pytest.raises(json.JSONDecodeError):
        helper(
            parser,
            _AIMessage("not-json"),
            fallback_to_json_extraction=False,
        )


def test_parse_structured_response_with_fallback_success() -> None:
    helper = _load_parse_helper(
        sanitize_impl=lambda message: '{"requires_new_knowledge":"high","search_intent":"일반 검색","retrieval_query":"x","confidence":0.5}'
    )
    parser = _FakeParser()

    parsed = helper(
        parser,
        _AIMessage("```json\nmalformed\n```"),
        fallback_to_json_extraction=True,
    )

    assert isinstance(parsed, KnowledgeSufficiency)
    assert parsed.requires_new_knowledge == "high"
    assert parsed.search_intent == "일반 검색"


def test_parse_structured_response_with_fallback_failure() -> None:
    helper = _load_parse_helper(sanitize_impl=lambda _: "{invalid json}")
    parser = _FakeParser()

    with pytest.raises(json.JSONDecodeError):
        helper(
            parser,
            _AIMessage("invalid"),
            fallback_to_json_extraction=True,
        )


def test_parse_structured_response_fallback_without_sanitize_definition() -> None:
    helper = _load_parse_helper(sanitize_impl=None)
    parser = _FakeParser()

    with pytest.raises(json.JSONDecodeError):
        helper(
            parser,
            _AIMessage("still not json"),
            fallback_to_json_extraction=True,
        )
