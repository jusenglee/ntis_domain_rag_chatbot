from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _load_resolve_join_execution_policy():
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="rag_pipeline.py")
    target = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_resolve_join_execution_policy"
    )
    module = ast.Module(body=[target], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace: Dict[str, Any] = {
        "Optional": Optional,
        "List": list,
        "Tuple": Tuple,
        "Dict": Dict,
        "Any": Any,
    }
    exec(compile(module, filename="rag_pipeline.py", mode="exec"), namespace)
    return namespace["_resolve_join_execution_policy"]


def test_resolve_join_execution_policy_instance_seed_uses_ids_map_source() -> None:
    fn = _load_resolve_join_execution_policy()
    policy = fn(
        relation=("project", "perf"),
        mode="join",
        action="relation",
        join_key_mode="instance",
        seed_join_pjt_ids=["202300001234"],
        seed_join_pjt_nos=[],
        has_people_org_gate=False,
    )

    assert policy["hop1_strategy"] == "skip"
    assert policy["seed_key_source"] == "ids_map.pjt_id"
    assert policy["seed_key_count"] == 1


def test_resolve_join_execution_policy_people_gate_uses_lookup() -> None:
    fn = _load_resolve_join_execution_policy()
    policy = fn(
        relation=("project", "perf"),
        mode="join",
        action="relation",
        join_key_mode="group",
        seed_join_pjt_ids=[],
        seed_join_pjt_nos=[],
        has_people_org_gate=True,
    )

    assert policy["hop1_strategy"] == "lookup"
    assert policy["seed_key_source"] == "people_org_conditions"
    assert policy["seed_key_count"] == 0


def test_resolve_join_execution_policy_group_seed_forces_lookup() -> None:
    fn = _load_resolve_join_execution_policy()
    policy = fn(
        relation=("project", "perf"),
        mode="join",
        action="relation",
        join_key_mode="group",
        seed_join_pjt_ids=[],
        seed_join_pjt_nos=["PNO-2024-0001"],
        has_people_org_gate=False,
    )

    assert policy["hop1_strategy"] == "lookup"
    assert policy["reason"] == "group_seed_pjt_no_expand"
    assert policy["seed_key_source"] == "ids_map.pjt_no"
    assert policy["seed_key_count"] == 1
