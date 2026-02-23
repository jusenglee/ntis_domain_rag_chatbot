from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import ast
from pathlib import Path
from typing import Any, Dict

from rag_parts.pipeline_steps import NormalizedIntent


def _load_select_mode_policy():
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="rag_pipeline.py")
    target = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_select_mode_policy")
    module = ast.Module(body=[target], type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: Dict[str, Any] = {
        "NormalizedIntent": NormalizedIntent,
        "Tuple": tuple,
        "Optional": __import__("typing").Optional,
        "_has_relation_join_ids": lambda _it: False,
        "_has_any_ids": lambda _it: False,
    }
    exec(compile(module, filename="rag_pipeline.py", mode="exec"), namespace)
    return namespace["_select_mode_policy"]


def _make_intent(**kwargs: Any) -> NormalizedIntent:
    defaults = dict(
        action="topic",
        base_route="project",
        relation=None,
        is_id_query=False,
        mode=None,
        output_type=None,
        join_key_mode=None,
        parsing_warnings=[],
        contract_violations=[],
        categories=[],
        planner_limit=None,
        retrieval_query=None,
        planner_confidence=None,
        years=[],
        year_from=None,
        year_to=None,
        people_terms=[],
        gender_terms=[],
        org_terms=[],
        org_role=None,
        lead_org_terms=[],
        participant_org_terms=[],
        people_affiliation_org_terms=[],
        perf_types=[],
        keywords=[],
        title=[],
        perf_tag_filters=[],
        project_tag_filters=[],
        tag_filters=[],
        ids_map={},
        ids_flat=[],
        remove_terms_for_head=[],
        people_terms_match_mode=None,
        people_terms_min_should=None,
        lookup_filter_policy=None,
        lookup_filter_policy_hint=None,
        target_cols=[],
    )
    defaults.update(kwargs)
    return NormalizedIntent(**defaults)


def test_select_mode_policy_prefers_lookup_for_people_name() -> None:
    fn = _load_select_mode_policy()
    mode, reason = fn(_make_intent(people_terms=["신동구"], action="topic"))
    assert mode == "lookup"
    assert reason == "people_org_name_lookup"


def test_select_mode_policy_prefers_lookup_for_org_name() -> None:
    fn = _load_select_mode_policy()
    mode, reason = fn(_make_intent(org_terms=["삼성"], action="topic"))
    assert mode == "lookup"
    assert reason == "people_org_name_lookup"
