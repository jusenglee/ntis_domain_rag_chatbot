import ast
import json
from pathlib import Path
from typing import Any

"""Stagewise planner 보조 함수의 최소 회귀를 고정하는 테스트.

여기서는 전체 서버를 띄우지 않고 AST로 함수만 뽑아,
seed 추출과 fixture 규모 같은 가장 싼 invariant를 빠르게 확인한다.
"""


def _load_functions(*names: str):
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    module = ast.Module(body=selected, type_ignores=[])
    ns: dict[str, Any] = {"Any": Any}
    exec(compile(module, filename="server3.py", mode="exec"), ns, ns)
    return [ns[name] for name in names]


def test_extract_single_project_seed_prefers_single_pjt_id():
    """이전 컨텍스트에 단일 과제 anchor가 있을 때 pjt_id 하나만 남겨야 한다."""
    (_extract_single_project_seed,) = _load_functions("_extract_single_project_seed")
    seed = _extract_single_project_seed([{"pjt_id": "1711015550"}, {"pjt_id": "1711015550"}])
    assert seed == {"pjt_id": ["1711015550"]}


def test_has_join_seed_id_uses_allowed_keys_only():
    """JOIN seed 판정은 허용된 식별자 키에만 반응해야 한다."""
    (_has_join_seed_id,) = _load_functions("_has_join_seed_id")
    assert _has_join_seed_id({"org_id": ["ETRI"]}) is False
    assert _has_join_seed_id({"pjt_id": ["1711015550"]}) is True


def test_regression_fixture_has_minimum_cases():
    """회귀 fixture가 너무 작아져 planner 커버리지가 줄어드는 것을 막는다."""
    cases = json.loads(Path("tests/fixtures/planner_regression_cases.json").read_text(encoding="utf-8"))
    assert len(cases) >= 12
