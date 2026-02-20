from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional


class _LoggerStub:
    def warning(self, *args, **kwargs):
        return None


def _load_merge_symbols() -> Dict[str, Any]:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    wanted_assigns = {
        "SOLAR_DEADLINE_MS",
        "DUAL_MODEL_MERGE_POLICY",
        "DUAL_MODEL_FALLBACK_MESSAGE",
    }
    wanted_funcs = {"_as_nonempty_text", "_latency_ms", "_select_final_answer"}

    selected_nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if targets & wanted_assigns:
                selected_nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted_funcs:
            selected_nodes.append(node)

    module = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: Dict[str, Any] = {
        "os": __import__("os"),
        "Any": Any,
        "Dict": Dict,
        "Optional": Optional,
        "AgentState": object,
        "logger": _LoggerStub(),
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    return namespace


def test_select_final_answer_prefers_solar_within_deadline() -> None:
    symbols = _load_merge_symbols()
    select_final_answer = symbols["_select_final_answer"]

    state = SimpleNamespace(
        answer_solar="solar 응답",
        answer_gemma="gemma 응답",
        latencies={"generate_answer_solar": 1.2, "generate_answer_gemma": 0.9},
    )

    selected = select_final_answer(state, solar_deadline_ms=2000)

    assert selected["chosen_model"] == "solar"
    assert selected["reason"] == "solar_ok_within_deadline"
    assert selected["answer"] == "solar 응답"
    assert selected["solar_dt_ms"] == 1200
    assert selected["gemma_dt_ms"] == 900


def test_select_final_answer_uses_gemma_when_solar_timeout() -> None:
    symbols = _load_merge_symbols()
    select_final_answer = symbols["_select_final_answer"]

    state = SimpleNamespace(
        answer_solar="solar 느림",
        answer_gemma="gemma 정상",
        latencies={"generate_answer_solar": 5.5, "generate_answer_gemma": 1.0},
    )

    selected = select_final_answer(state, solar_deadline_ms=3000)

    assert selected["chosen_model"] == "gemma"
    assert selected["reason"] == "solar_timeout_or_empty_use_gemma"
    assert selected["answer"] == "gemma 정상"
    assert selected["solar_dt_ms"] == 5500


def test_select_final_answer_uses_gemma_when_solar_empty() -> None:
    symbols = _load_merge_symbols()
    select_final_answer = symbols["_select_final_answer"]

    state = SimpleNamespace(
        answer_solar="   ",
        answer_gemma="gemma 정상",
        latencies={"generate_answer_solar": 0.5, "generate_answer_gemma": 0.6},
    )

    selected = select_final_answer(state, solar_deadline_ms=3000)

    assert selected["chosen_model"] == "gemma"
    assert selected["reason"] == "solar_timeout_or_empty_use_gemma"
    assert selected["answer"] == "gemma 정상"


def test_select_final_answer_uses_fallback_when_both_abnormal() -> None:
    symbols = _load_merge_symbols()
    select_final_answer = symbols["_select_final_answer"]
    fallback_message = symbols["DUAL_MODEL_FALLBACK_MESSAGE"]

    state = SimpleNamespace(
        answer_solar="",
        answer_gemma=None,
        latencies={"generate_answer_solar": 6.0, "generate_answer_gemma": 1.0},
    )

    selected = select_final_answer(state, solar_deadline_ms=3000)

    assert selected["chosen_model"] == "fallback"
    assert selected["reason"] == "both_models_abnormal"
    assert selected["answer"] == fallback_message


def test_select_final_answer_supports_gemma_first_policy() -> None:
    symbols = _load_merge_symbols()
    select_final_answer = symbols["_select_final_answer"]

    state = SimpleNamespace(
        answer_solar="solar 정상",
        answer_gemma="gemma 우선",
        latencies={"generate_answer_solar": 0.8, "generate_answer_gemma": 1.1},
    )

    selected = select_final_answer(state, policy="gemma_first", solar_deadline_ms=2000)

    assert selected["chosen_model"] == "gemma"
    assert selected["reason"] == "policy_gemma_first"
    assert selected["answer"] == "gemma 우선"
