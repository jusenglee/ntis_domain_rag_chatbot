import ast
from pathlib import Path
from typing import Any
import re


def _load_sanitizer():
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_sanitize_ids_map_semantics"]
    module = ast.Module(body=selected, type_ignores=[])
    ns: dict[str, Any] = {"Any": Any, "re": re}
    exec(compile(module, filename="server3.py", mode="exec"), ns, ns)
    return ns["_sanitize_ids_map_semantics"]


def test_sanitize_ids_map_semantics_removes_invalid_values():
    sanitize = _load_sanitizer()
    cleaned, invalid = sanitize({"pjt_id": ["ETRI", "1711015550"], "doi": ["10.1000/xyz", "bad"]})
    assert cleaned["pjt_id"] == ["1711015550"]
    assert cleaned["doi"] == ["10.1000/xyz"]
    assert any(item["key"] == "pjt_id" for item in invalid)
