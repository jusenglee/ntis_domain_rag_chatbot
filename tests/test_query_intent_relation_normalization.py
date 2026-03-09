from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict


def _load_relation_symbols() -> Dict[str, Any]:
    source = Path("rag_parts/query_intent.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="rag_parts/query_intent.py")

    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {"_normalize_join_relation", "pick_relation"}:
            selected.append(node)

    module = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module)
    ns: Dict[str, Any] = {"Optional": __import__("typing").Optional, "Tuple": __import__("typing").Tuple}
    exec(compile(module, filename="rag_parts/query_intent.py", mode="exec"), ns)
    return ns


def test_relation_normalization_drops_people_org() -> None:
    symbols = _load_relation_symbols()
    normalize = symbols["_normalize_join_relation"]
    assert normalize(("people", "project")) is None
    assert normalize(("org", "perf")) is None
    assert normalize(("project", "perf")) == ("project", "perf")


def test_pick_relation_uses_allowed_relation_values() -> None:
    symbols = _load_relation_symbols()
    pick_relation = symbols["pick_relation"]
    rel = pick_relation(
        "이 과제의 논문 성과 알려줘",
        "project",
        has_project=True,
        has_perf=True,
        has_people=False,
        has_org=False,
    )
    assert rel in {None, ("project", "perf"), ("perf", "project")}
