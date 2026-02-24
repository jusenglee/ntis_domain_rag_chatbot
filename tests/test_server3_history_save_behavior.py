from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

class HumanMessage:
    def __init__(self, content: str):
        self.content = content


class AIMessage:
    def __init__(self, content: str):
        self.content = content


def _load_history_functions() -> Dict[str, Any]:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    targets: List[ast.AST] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("_serialize_history", "_format_coq"):
            targets.append(node)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "node_save_history":
            node.decorator_list = []
            targets.append(node)

    module = ast.Module(body=targets, type_ignores=[])
    ast.fix_missing_locations(module)

    class _FakeLogger:
        def __init__(self):
            self.debug_calls = []

        def debug(self, *args, **kwargs):
            self.debug_calls.append((args, kwargs))

    ns: Dict[str, Any] = {
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "json": json,
        "AIMessage": AIMessage,
        "HumanMessage": HumanMessage,
        "BaseMessage": object,
        "MAX_HISTORY_TURNS": 10,
        "REDIS_TTL": 3600,
        "log_section": lambda *_a, **_k: None,
        "logger": _FakeLogger(),
        "kv_store": None,
    }
    exec(compile(module, filename="server3.py", mode="exec"), ns)
    return ns


class _FakeKV:
    def __init__(self):
        self.saved = {}

    async def set(self, key, value, ex=None):
        self.saved[key] = (value, ex)


def test_node_save_history_stores_only_single_human_and_ai_per_turn():
    ns = _load_history_functions()
    kv = _FakeKV()
    ns["kv_store"] = kv

    state = SimpleNamespace(
        conversation_id="cid-1",
        chat_history=[HumanMessage(content="이번 질문")],
        messages=[HumanMessage(content="이번 질문"), AIMessage(content="답변")],
        context=[{"id": 1}],
        fallback_context="fallback",
        latencies={},
        question="이번 질문",
    )

    asyncio.run(ns["node_save_history"](state))

    raw = kv.saved["conversation:cid-1:history"][0]
    saved = json.loads(raw)
    assert [m["type"] for m in saved] == ["human", "ai"]
    assert [m["content"] for m in saved] == ["이번 질문", "답변"]


def test_node_save_history_skips_when_kv_store_unavailable():
    ns = _load_history_functions()
    ns["kv_store"] = None

    state = SimpleNamespace(
        conversation_id="cid-2",
        chat_history=[HumanMessage(content="질문")],
        messages=[HumanMessage(content="질문"), AIMessage(content="답변")],
        context=[{"id": 1}],
        fallback_context="fallback",
        latencies={},
        question="질문",
    )

    asyncio.run(ns["node_save_history"](state))
    assert len(ns["logger"].debug_calls) >= 1


def test_format_coq_contains_separator_and_question():
    ns = _load_history_functions()
    out = ns["_format_coq"]("cid-3", "질문 내용")
    assert out == "coq: cid-3 | q: 질문 내용"
