from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_parts.planner_contract import validate_planner_contract


def _violations_for(head: str, relation: tuple[str, str]):
    return validate_planner_contract(
        mode="join",
        head=head,
        relation=relation,
        target_cols=["ntis_project_v1", "ntis_perf_v1"],
        ids_map={},
        relation_target_cols=("ntis_project_v1", "ntis_perf_v1") if relation == ("project", "perf") else ("ntis_perf_v1", "ntis_project_v1"),
        join_key_mode="instance",
    )


def test_join_head_project_perf_target_passes() -> None:
    violations = _violations_for("perf", ("project", "perf"))
    assert violations == []


def test_join_head_perf_project_target_passes() -> None:
    violations = _violations_for("project", ("perf", "project"))
    assert violations == []


def test_join_head_perf_project_source_fails() -> None:
    violations = _violations_for("perf", ("perf", "project"))
    assert any(v.error_code == "PLANNER_JOIN_RELATION_HEAD_TARGET_MISMATCH" for v in violations)


def test_join_instance_allows_empty_ids_map_for_perf_project_title_query_shape() -> None:
    violations = validate_planner_contract(
        mode="join",
        head="project",
        relation=("perf", "project"),
        target_cols=["ntis_perf_v1", "ntis_project_v1"],
        ids_map={},
        relation_target_cols=("ntis_perf_v1", "ntis_project_v1"),
        join_key_mode="instance",
    )
    assert violations == []


def test_join_instance_person_org_ids_do_not_replace_relation_target_contract() -> None:
    violations = validate_planner_contract(
        mode="join",
        head="project",
        relation=("perf", "project"),
        target_cols=["ntis_perf_v1", "ntis_project_v1"],
        ids_map={"person_no": ["P001"], "org_id": ["O001"]},
        relation_target_cols=("ntis_perf_v1", "ntis_project_v1"),
        join_key_mode="instance",
    )
    assert violations == []
