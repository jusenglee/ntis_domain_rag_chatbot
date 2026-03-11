from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
import sys
from typing import Any, Optional

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_parts.log_keys import CHANGED_BY_PLANNER_MERGE
from rag_parts.planner_contract import StrategyViolation
from rag_parts.pipeline_steps import build_changed_fields


@dataclass
class DummyIntent:
    base_route: str = "project"
    action: str = "topic"
    mode: Optional[str] = "search"
    relation: Optional[tuple[str, str]] = None
    join_key_mode: Optional[str] = None
    target_cols: list[str] = field(default_factory=list)
    ids_map: dict[str, list[str]] = field(default_factory=dict)
    wants_rank: bool = False


def _get_question_analysis_source_segment() -> str:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "QuestionAnalysisV2":
            return ast.get_source_segment(source, node) or ""
    raise AssertionError("QuestionAnalysisV2 class not found")


def _load_apply_planner_strategy():
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    target_names = {"_normalize_hint_terms", "apply_planner_strategy"}
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in target_names
    ]
    module = ast.Module(body=selected, type_ignores=[])

    captured_events: list[dict[str, Any]] = []

    def _log_event(name: str, **fields: Any) -> None:
        captured_events.append({"name": name, **fields})

    ns: dict[str, Any] = {
        "Any": Any,
        "Optional": Optional,
        "replace": replace,
        "os": os,
        "StrategyViolation": StrategyViolation,
        "build_changed_fields": build_changed_fields,
        "CHANGED_BY_PLANNER_MERGE": CHANGED_BY_PLANNER_MERGE,
        "_log_event": _log_event,
        "QuestionAnalysis": Any,
    }
    exec(compile(module, filename="server3.py", mode="exec"), ns, ns)
    return ns["apply_planner_strategy"], captured_events


def test_apply_planner_strategy_strict_mode_raises_on_action_mode_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    apply_planner_strategy, captured_events = _load_apply_planner_strategy()
    monkeypatch.setenv("RAG_STRICT_STRATEGY_CONSISTENCY", "1")

    intent = DummyIntent()
    qa = type(
        "QA",
        (),
        {
            "confidence": 0.91,
            "action": "list",
            "mode": "search",
            "relation": None,
            "join_key_mode": None,
            "target_cols": [],
            "ids_map": {},
            "wants_rank": False,
            "head": "project",
        },
    )()

    with pytest.raises(StrategyViolation) as exc_info:
        apply_planner_strategy(intent, qa, request_id="req-1", conversation_id="conv-1")

    assert exc_info.value.error_code == "PLANNER_ACTION_MODE_MISMATCH"
    assert any(event["name"] == "RAG.STRATEGY.ACTION_MODE_MISMATCH" for event in captured_events)


def test_apply_planner_strategy_compat_mode_corrects_action_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    apply_planner_strategy, captured_events = _load_apply_planner_strategy()
    monkeypatch.setenv("RAG_STRICT_STRATEGY_CONSISTENCY", "0")

    intent = DummyIntent()
    qa = type(
        "QA",
        (),
        {
            "confidence": 0.88,
            "action": "list",
            "mode": "search",
            "relation": None,
            "join_key_mode": None,
            "target_cols": [],
            "ids_map": {},
            "wants_rank": False,
            "head": "project",
        },
    )()

    patched, applied = apply_planner_strategy(intent, qa, request_id="req-2", conversation_id="conv-2")

    assert applied is True
    assert patched.mode == "lookup"
    corrected_event = next(event for event in captured_events if event["name"] == "RAG.STRATEGY.ACTION_MODE_CORRECTED")
    assert corrected_event["request_id"] == "req-2"
    assert corrected_event["conversation_id"] == "conv-2"
    assert corrected_event["planner_action"] == "list"
    assert corrected_event["original_mode"] == "search"
    assert corrected_event["corrected_mode"] == "lookup"


def test_question_analysis_v2_declares_forbidden_people_org_relation_code() -> None:
    segment = _get_question_analysis_source_segment()
    assert "PLANNER_RELATION_FORBIDDEN_PEOPLE_ORG" in segment
    assert "_FORBIDDEN_PEOPLE_ORG_RELATIONS" in segment


def test_question_analysis_v2_relation_allowlist_is_project_perf_only() -> None:
    segment = _get_question_analysis_source_segment()
    assert '"project_perf"' in segment
    assert '"perf_project"' in segment
    assert "PLANNER_RELATION_INVALID" in segment


def test_apply_planner_strategy_allows_join_with_list_when_relation_present_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply_planner_strategy, captured_events = _load_apply_planner_strategy()
    monkeypatch.setenv("RAG_STRICT_STRATEGY_CONSISTENCY", "1")

    intent = DummyIntent(mode="lookup")
    qa = type(
        "QA",
        (),
        {
            "confidence": 0.93,
            "action": "list",
            "mode": "join",
            "relation": "project_perf",
            "join_key_mode": "instance",
            "target_cols": ["ntis_project_v1", "ntis_perf_v1"],
            "ids_map": {"pjt_id": ["1711015550"]},
            "wants_rank": False,
            "head": "perf",
        },
    )()

    patched, applied = apply_planner_strategy(intent, qa, request_id="req-join", conversation_id="conv-join")

    assert applied is True
    assert patched.mode == "join"
    assert patched.relation == ("project", "perf")
    assert all(event["name"] != "RAG.STRATEGY.ACTION_MODE_MISMATCH" for event in captured_events)
