from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict


def _load_helpers() -> Dict[str, Any]:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    targets = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"sanitize_llm_json", "_strip_gpt_oss_sections"}
    ]

    module = ast.Module(body=targets, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: Dict[str, Any] = {
        "Any": Any,
        "json": __import__("json"),
        "re": __import__("re"),
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    return namespace


def test_strip_prefers_assistantfinal_suffix() -> None:
    helper = _load_helpers()["_strip_gpt_oss_sections"]

    assert helper("analysis...assistantfinal{\"mode\":\"LOOKUP\"}") == '{"mode":"LOOKUP"}'


def test_strip_uses_last_newline_assistant_section() -> None:
    helper = _load_helpers()["_strip_gpt_oss_sections"]

    text = "prefix\nassistant 중간\nassistant{\"k\":1}"
    assert helper(text) == '{"k":1}'


def test_strip_removes_leading_analysis_header() -> None:
    helper = _load_helpers()["_strip_gpt_oss_sections"]

    assert helper("analysis\n{\"x\":1}") == '{"x":1}'


def test_sanitize_applies_strip_before_json_extraction() -> None:
    sanitize = _load_helpers()["sanitize_llm_json"]

    raw = "analysis...assistantfinal\n```json\n{\"mode\":\"JOIN\"}\n```"
    assert sanitize(raw) == '{"mode":"JOIN"}'
