from __future__ import annotations

import ast
from pathlib import Path


SOURCE_PATH = Path("rag_pipeline.py")
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(SOURCE_PATH))


LOG_CALL_NAMES = {"log_kv", "log_section", "log_top_points"}


def _extract_event_tiers() -> dict[str, str]:
    event_tiers: dict[str, str] = {}

    for node in ast.walk(TREE):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id not in LOG_CALL_NAMES:
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
            continue

        tier = None
        for kw in node.keywords:
            if kw.arg != "tier":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                tier = kw.value.value
            break

        if tier is not None:
            event_tiers[first_arg.value] = tier

    return event_tiers


def test_log_tier_snapshot_for_core_events() -> None:
    tiers = _extract_event_tiers()

    expected_normal = {
        "RAG.PLAN": "normal",
        "RAG.RETRIEVE": "normal",
        "RAG.RESULT.TOP": "normal",
        "RAG.CONTEXT": "normal",
        "RAG.ERROR.JOIN_KEYS": "normal",
    }
    expected_debug = {
        "RAG.STRATEGY.POLICY": "debug",
        "RAG.PLAN.MODE_CONFLICT": "debug",
        "RAG.PLAN.RELATION_LOOKUP_POLICY": "debug",
        "RAG.PLAN.JOIN_EXECUTED": "debug",
    }

    for event, expected_tier in expected_normal.items():
        assert tiers.get(event) == expected_tier, f"{event} tier expected={expected_tier}, actual={tiers.get(event)}"

    for event, expected_tier in expected_debug.items():
        assert tiers.get(event) == expected_tier, f"{event} tier expected={expected_tier}, actual={tiers.get(event)}"
