from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict


def _load_functions():
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    targets = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {"_normalize_hint_terms", "_collect_researcher_name_terms"}:
            targets.append(node)

    module = ast.Module(body=targets, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: Dict[str, Any] = {
        "Any": Any,
        "Dict": Dict,
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    return namespace


def test_collect_researcher_name_terms_supports_alias_keys() -> None:
    ns = _load_functions()
    collect = ns["_collect_researcher_name_terms"]

    filters = {
        "researcher": "김재수",
        "participant_researchers": ["이몽룡"],
        "researcher_names": ["성춘향"],
    }

    assert collect(filters) == ["이몽룡", "성춘향", "김재수"]


def test_build_intent_payload_uses_researcher_alias_collector() -> None:
    source = Path("server3.py").read_text(encoding="utf-8")

    assert "people_terms_hint = _collect_researcher_name_terms(filters)" in source
    assert "names = _collect_researcher_name_terms(filters)" in source
