from __future__ import annotations

from apps.core.planner_contract import validate_planner_contract


def _codes(violations):
    return [v.error_code for v in violations]


def test_validate_planner_contract_rejects_join_without_join_key_mode():
    violations = validate_planner_contract(
        mode="join",
        head="perf",
        relation=("project", "perf"),
        target_cols=["ntis_project_v1", "ntis_perf_v1"],
        ids_map={"pjt_id": ["1711015550"]},
        relation_target_cols=("ntis_project_v1", "ntis_perf_v1"),
        join_key_mode=None,
    )

    assert _codes(violations) == ["PLANNER_JOIN_KEY_MODE_INVALID"]


def test_validate_planner_contract_rejects_non_join_with_join_key_mode():
    violations = validate_planner_contract(
        mode="lookup",
        head="project",
        relation=None,
        target_cols=["ntis_project_v1"],
        ids_map={"pjt_id": ["1711015550"]},
        relation_target_cols=None,
        join_key_mode="group",
    )

    assert _codes(violations) == ["PLANNER_JOIN_KEY_MODE_INVALID"]


def test_validate_planner_contract_rejects_group_join_with_instance_key_only():
    violations = validate_planner_contract(
        mode="join",
        head="perf",
        relation=("project", "perf"),
        target_cols=["ntis_project_v1", "ntis_perf_v1"],
        ids_map={"pjt_id": ["1711015550"]},
        relation_target_cols=("ntis_project_v1", "ntis_perf_v1"),
        join_key_mode="group",
    )

    assert _codes(violations) == ["PLANNER_JOIN_KEY_MODE_IDS_MISMATCH"]


def test_validate_planner_contract_rejects_instance_join_with_group_key_only():
    violations = validate_planner_contract(
        mode="join",
        head="perf",
        relation=("project", "perf"),
        target_cols=["ntis_project_v1", "ntis_perf_v1"],
        ids_map={"pjt_no": ["PJT-2020-1234-5678"]},
        relation_target_cols=("ntis_project_v1", "ntis_perf_v1"),
        join_key_mode="instance",
    )

    assert _codes(violations) == ["PLANNER_JOIN_KEY_MODE_IDS_MISMATCH"]


def test_validate_planner_contract_accepts_valid_group_join_seed():
    violations = validate_planner_contract(
        mode="join",
        head="perf",
        relation=("project", "perf"),
        target_cols=["ntis_project_v1", "ntis_perf_v1"],
        ids_map={"pjt_no": ["PJT-2020-1234-5678"]},
        relation_target_cols=("ntis_project_v1", "ntis_perf_v1"),
        join_key_mode="group",
    )

    assert violations == []
