from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional


def _load_context_selection_functions() -> Dict[str, Any]:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    targets: List[ast.AST] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_is_low_quality_context_text":
            targets.append(node)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_generate_answer":
            targets.append(node)

    module = ast.Module(body=targets, type_ignores=[])
    ast.fix_missing_locations(module)

    captured: Dict[str, Any] = {"human_prompt": "", "logger_calls": []}

    class _FakeLLM:
        def __init__(self, **_kwargs):
            pass

        async def ainvoke(self, messages, max_tokens_hint=None):
            captured["human_prompt"] = messages[1].content
            captured["max_tokens_hint"] = max_tokens_hint
            return SimpleNamespace(content="테스트 응답<eos>")

    class _FakeLogger:
        def info(self, *args, **kwargs):
            captured["logger_calls"].append((args, kwargs))

    namespace: Dict[str, Any] = {
        "Any": Any,
        "Dict": Dict,
        "AgentState": object,
        "TritonChatModel": _FakeLLM,
        "_filter_hit_documents": lambda docs: docs,
        "_build_researcher_hints_from_question_analysis": lambda _qa: [],
        "refine_documents_rule_based": lambda docs, *_args, **_kwargs: docs[0]["refined"],
        "load_system_prompt": lambda _path: asyncio.sleep(0, result="SYSTEM"),
        "Path": Path,
        "SystemMessage": lambda content: SimpleNamespace(content=content),
        "HumanMessage": lambda content: SimpleNamespace(content=content),
        "_select_max_tokens_hint": lambda _qa: 123,
        "log_section": lambda *_args, **_kwargs: None,
        "logger": _FakeLogger(),
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    namespace["_captured"] = captured
    return namespace


def _make_state(*, refined: str, fallback: Optional[str] = None, has_context: bool = True):
    context = [{"refined": refined}] if has_context else []
    return SimpleNamespace(
        knowledge_sufficiency=None,
        question_analysis=SimpleNamespace(mode="SINGLE", filters=None, ids_map=None),
        context=context,
        prev_context=[],
        fallback_context=fallback,
        messages=[SimpleNamespace(content="질문")],
    )


def test_low_quality_refined_context_uses_fallback_reference_path():
    ns = _load_context_selection_functions()
    gen = ns["_generate_answer"]

    state = _make_state(refined="정보 없음\n정보 없음", fallback="백업 문맥", has_context=True)
    out = asyncio.run(gen(state, "m", "answer_gpt"))

    assert out["answer_gpt"] == "테스트 응답"
    assert "[참고 문맥(근거 아님)]\n백업 문맥" in ns["_captured"]["human_prompt"]


def test_normal_docs_context_keeps_existing_behavior_without_fallback_switch():
    ns = _load_context_selection_functions()
    gen = ns["_generate_answer"]

    refined = "## 출처 1. 과제\n과제명: A\n주관기관: B\n연구목표: C"
    state = _make_state(refined=refined, fallback="백업 문맥", has_context=True)
    asyncio.run(gen(state, "m", "answer_gpt"))

    prompt = ns["_captured"]["human_prompt"]
    assert refined in prompt
    assert "[참고 문맥(근거 아님)]" not in prompt


def test_low_quality_detector_identifies_missing_core_fields():
    ns = _load_context_selection_functions()
    detector = ns["_is_low_quality_context_text"]

    assert detector("## 출처 1. 제목\n설명만 있음") is True
    assert detector("## 출처 1. 제목\n과제명: A\n주관기관: B\n연구목표: C") is False
