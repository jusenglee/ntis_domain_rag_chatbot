from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path("server3.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename="server3.py")


def _find_async_function(name: str) -> ast.AsyncFunctionDef:
    for node in TREE.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"함수 {name} 를 찾을 수 없습니다.")


def test_generate_answer_returns_state_flags_for_downstream_nodes() -> None:
    node = _find_async_function("_generate_answer")
    src = ast.get_source_segment(SOURCE, node) or ""

    assert '"rendered_context_used": rendered_context_used' in src
    assert '"fallback_context_used": bool(getattr(state, "fallback_context_used", False))' in src
    assert '"degraded": bool(getattr(state, "degraded", False))' in src


def test_merge_answers_reads_and_returns_all_state_flags() -> None:
    node = _find_async_function("node_merge_answers")
    src = ast.get_source_segment(SOURCE, node) or ""

    assert 'fallback_context_used = bool(getattr(state, "fallback_context_used", False))' in src
    assert 'degraded = bool(getattr(state, "degraded", False)) or (selected_answer == DUAL_MODEL_FALLBACK_MESSAGE)' in src
    assert '"fallback_context_used": fallback_context_used' in src
