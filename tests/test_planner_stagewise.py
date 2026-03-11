import ast
import json
from pathlib import Path
from typing import Any


def _load_functions(*names: str):
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    module = ast.Module(body=selected, type_ignores=[])
    ns: dict[str, Any] = {"Any": Any}
    exec(compile(module, filename="server3.py", mode="exec"), ns, ns)
    return [ns[name] for name in names]


def test_extract_single_project_seed_prefers_single_pjt_id():
    (_extract_single_project_seed,) = _load_functions("_extract_single_project_seed")
    seed = _extract_single_project_seed([{"pjt_id": "1711015550"}, {"pjt_id": "1711015550"}])
    assert seed == {"pjt_id": ["1711015550"]}


def test_has_join_seed_id_uses_allowed_keys_only():
    (_has_join_seed_id,) = _load_functions("_has_join_seed_id")
    assert _has_join_seed_id({"org_id": ["ETRI"]}) is False
    assert _has_join_seed_id({"pjt_id": ["1711015550"]}) is True


def test_regression_fixture_has_minimum_cases():
    cases = json.loads(Path("tests/fixtures/planner_regression_cases.json").read_text(encoding="utf-8"))
    assert len(cases) >= 12
