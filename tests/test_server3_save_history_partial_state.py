from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import asyncio


SOURCE = Path("server3.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename="server3.py")


def _load_save_history_symbols() -> Dict[str, Any]:
    wanted_assigns = {"MAX_HISTORY_TURNS"}
    wanted_funcs = {"_state_log_summary_fields", "node_save_history"}

    selected_nodes = []
    for node in TREE.body:
        if isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & wanted_assigns:
                selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_funcs:
            selected_nodes.append(node)
        elif isinstance(node, ast.AsyncFunctionDef) and node.name in wanted_funcs:
            node.decorator_list = []
            selected_nodes.append(node)

    module = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    events = []

    def _log_event(name: str, **fields: Any) -> None:
        events.append((name, fields))

    namespace: Dict[str, Any] = {
        "Any": Any,
        "Dict": Dict,
        "Optional": __import__("typing").Optional,
        "kv_store": None,
        "REDIS_TTL": 60,
        "json": __import__("json"),
        "_serialize_history": lambda items: items,
        "_compute_total_ms_from_start": lambda start: 0,
        "_log_event": _log_event,
        "logger": SimpleNamespace(debug=lambda *args, **kwargs: None),
        "AgentState": object,
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    namespace["_events"] = events
    return namespace


def test_node_save_history_handles_partial_state_without_exception() -> None:
    symbols = _load_save_history_symbols()
    node_save_history = symbols["node_save_history"]

    partial_state = SimpleNamespace(
        conversation_id="cid-1",
        messages=[],
        request_started_at=None,
    )

    result = asyncio.run(node_save_history(partial_state))

    assert result == {}
    assert [name for name, _ in symbols["_events"]] == ["REQ.SUMMARY", "REQ.END"]
