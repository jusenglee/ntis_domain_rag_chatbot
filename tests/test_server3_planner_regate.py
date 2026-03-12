from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Optional


def _load_regate_functions():
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    target_names = {
        "_has_join_seed_id",
        "_collect_regate_seed_map",
        "_has_new_regate_seed",
        "_re_gate_locked_strategy",
    }
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            target_ids = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if target_ids & {"PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS"}:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in target_names:
            selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])

    captured_events: list[dict[str, Any]] = []

    def _log_event(name: str, **fields: Any) -> None:
        captured_events.append({"name": name, **fields})

    ns: dict[str, Any] = {
        "Any": Any,
        "Optional": Optional,
        "PlannerStage1Decision": Any,
        "PlannerStage2Slots": Any,
        "_log_event": _log_event,
    }
    exec(compile(module, filename="server3.py", mode="exec"), ns, ns)
    return ns, captured_events


def test_re_gate_locked_strategy_promotes_lookup_to_join_with_new_seed() -> None:
    ns, captured_events = _load_regate_functions()
    re_gate = ns["_re_gate_locked_strategy"]

    stage1 = type("Stage1", (), {"relation_candidate": "project_perf", "action": "list", "head": "project"})()
    stage2 = type("Stage2", (), {"ids_map": {"pjt_id": ["1711015550"]}})()
    locked_strategy = {
        "mode": "LOOKUP",
        "head": "project",
        "action": "list",
        "relation": None,
        "join_key_mode": None,
        "target_cols": ["ntis_project_v1"],
        "gate_seed_map": {},
    }

    updated = re_gate(
        request_id="req-1",
        conversation_id="conv-1",
        stage1=stage1,
        stage2=stage2,
        locked_strategy=locked_strategy,
    )

    assert updated["mode"] == "JOIN"
    assert updated["relation"] == "project_perf"
    assert updated["join_key_mode"] == "instance"
    assert updated["target_cols"] == ["ntis_project_v1", "ntis_perf_v1"]
    regate_event = next(event for event in captured_events if event["name"] == "PLANNER.REGATE")
    assert regate_event["regate_eligible"] == 1
    assert regate_event["regate_changed"] == 1


def test_re_gate_locked_strategy_keeps_strategy_when_not_eligible() -> None:
    ns, captured_events = _load_regate_functions()
    re_gate = ns["_re_gate_locked_strategy"]

    stage1 = type("Stage1", (), {"relation_candidate": None, "action": "list", "head": "project"})()
    stage2 = type("Stage2", (), {"ids_map": {"pjt_id": ["1711015550"]}})()
    locked_strategy = {
        "mode": "LOOKUP",
        "head": "project",
        "action": "list",
        "relation": None,
        "join_key_mode": None,
        "target_cols": ["ntis_project_v1"],
        "gate_seed_map": {},
    }

    updated = re_gate(
        request_id="req-2",
        conversation_id="conv-2",
        stage1=stage1,
        stage2=stage2,
        locked_strategy=locked_strategy,
    )

    assert updated == locked_strategy
    regate_event = next(event for event in captured_events if event["name"] == "PLANNER.REGATE")
    assert regate_event["regate_eligible"] == 0
    assert regate_event["regate_changed"] == 0
