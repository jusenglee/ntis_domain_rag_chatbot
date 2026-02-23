from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


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


def _load_parse_helper() -> Any:
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
        "sanitize_llm_json": lambda *_: (_ for _ in ()).throw(AssertionError("sanitize should not be called")),
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    return namespace["_parse_structured_response"]


def test_parse_structured_response_validates_schema_object_without_json_sanitize() -> None:
    helper = _load_parse_helper()
    parser = _FakeParser()

    llm_result = _AIMessage(
        content=(
            '{"requires_new_knowledge":"low",'
            '"search_intent":"기존 문맥으로 충분",'
            '"retrieval_query":"",'
            '"confidence":0.97}'
        )
    )

    parsed = helper(
        parser,
        llm_result,
        fallback_to_json_extraction=False,
    )

    assert isinstance(parsed, KnowledgeSufficiency)
    assert parsed.requires_new_knowledge == "low"
    assert parsed.search_intent == "기존 문맥으로 충분"
    assert parsed.confidence == 0.97
