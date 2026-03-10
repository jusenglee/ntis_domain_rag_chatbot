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

    assert 'rendered_context_key = f"rendered_context_used_{final_field.replace(\'answer_\', \'\')}"' in src
    assert 'rendered_context_key: rendered_context_used' in src
    assert '"fallback_context_used": bool(getattr(state, "fallback_context_used", False))' not in src
    assert '"degraded": bool(getattr(state, "degraded", False))' not in src


def test_merge_answers_reads_and_returns_all_state_flags() -> None:
    node = _find_async_function("node_merge_answers")
    src = ast.get_source_segment(SOURCE, node) or ""

    assert 'rendered_context_used = bool(getattr(state, "rendered_context_used_gemma", False)) or bool(getattr(state, "rendered_context_used_solar", False))' in src
    assert 'has_docs_context = bool(getattr(state, "context", None) or getattr(state, "prev_context", None))' in src
    assert 'has_fallback_context = bool(getattr(state, "fallback_context", None))' in src
    assert 'fallback_context_used = (not has_docs_context) and has_fallback_context' in src
    assert 'degraded = bool(getattr(state, "degraded", False)) or (selected_answer == DUAL_MODEL_FALLBACK_MESSAGE)' in src
    assert '"rendered_context_used": rendered_context_used' in src
    assert '"fallback_context_used": fallback_context_used' in src


def test_agent_state_bool_flags_use_merge_bool_reducer() -> None:
    assert 'rendered_context_used: Annotated[bool, merge_bool_flag] = False' in SOURCE
    assert 'fallback_context_used: Annotated[bool, merge_bool_flag] = False' in SOURCE
    assert 'degraded: Annotated[bool, merge_bool_flag] = False' in SOURCE


def test_event_stream_extracts_question_analysis_payload_field() -> None:
    assert 'question_analysis = output.get("question_analysis")' in SOURCE
