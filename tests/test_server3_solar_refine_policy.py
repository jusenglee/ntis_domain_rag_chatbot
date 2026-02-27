from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List


SOURCE = Path("server3.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename="server3.py")


def _find_function(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"함수 {name} 를 찾을 수 없습니다.")


def test_generate_answer_has_solar_specific_refine_branch() -> None:
    node = _find_function("_generate_answer")
    src = ast.get_source_segment(SOURCE, node) or ""

    assert 'is_solar = model_name == "solar_vllm_0"' in src
    assert 'relax_limits=False if is_solar else True' in src
    assert 'max_doc_sentences=SOLAR_MAX_DOC_SENTENCES if is_solar else None' in src
    assert 'max_doc_tokens=SOLAR_MAX_DOC_TOKENS if is_solar else None' in src
    assert 'context_text = context_text[:SOLAR_MAX_CONTEXT_CHARS]' in src
    assert 'ctx_sentences=' in src
    assert 'ctx_tokens_est=' in src


def test_refine_documents_rule_based_uses_model_aware_caps_and_priority_lines() -> None:
    node = _find_function("refine_documents_rule_based")
    src = ast.get_source_segment(SOURCE, node) or ""

    assert "max_doc_sentences: Optional[int] = None" in src
    assert "max_doc_tokens: Optional[int] = None" in src
    assert "doc_max_sentences = max_doc_sentences if max_doc_sentences is not None else MAX_DOC_SENTENCES" in src
    assert "doc_max_tokens = max_doc_tokens if max_doc_tokens is not None else MAX_DOC_TOKENS" in src
    assert "priority_lines = _collect_priority_field_lines(mapped_doc)" in src
    assert "extra_lines = [line for line in priority_lines + [researcher_line, org_line] if line]" in src
    assert "len(body_sentences) + len(extra_sentences) > doc_max_sentences" in src
    assert "body_token_count + extra_token_count > doc_max_tokens" in src


def test_collect_priority_field_lines_preserves_core_fields() -> None:
    wanted = []
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_collect_priority_field_lines":
            wanted.append(node)

    module = ast.Module(body=wanted, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: Dict[str, Any] = {
        "List": List,
        "Dict": Dict,
        "Any": Any,
        "PRIORITY_CONTEXT_FIELDS": ("title", "pjt_id", "pjt_no"),
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)

    func = namespace["_collect_priority_field_lines"]
    lines = func(
        {
            "title": "양자 암호 통신",
            "pjt_id": "1711000001",
            "pjt_no": "1345000012",
            "noise": "x",
            "empty": "",
        }
    )

    assert "- title: 양자 암호 통신" in lines
    assert "- pjt_id: 1711000001" in lines
    assert "- pjt_no: 1345000012" in lines
