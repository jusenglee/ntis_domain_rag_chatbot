from __future__ import annotations

import pytest

from apps.core.filters import validate_planner_join_keys, validate_resolved_join_keys


def test_validate_planner_join_keys_normalizes_ids_map_and_rejects_mixed_project_keys():
    normalized = validate_planner_join_keys(mode="lookup", ids_map={"pjt_id": "1711015550"})

    assert normalized == {"pjt_id": ["1711015550"]}

    with pytest.raises(ValueError, match="PLANNER_MIXED_PROJECT_KEYS"):
        validate_planner_join_keys(
            mode="join",
            ids_map={"pjt_id": ["1711015550"], "pjt_no": ["PJT-2020-1234-5678"]},
        )


def test_validate_resolved_join_keys_allows_group_runtime_with_pjt_no_only():
    validate_resolved_join_keys(mode="group", pjt_nos=["PJT-2020-1234-5678"], pjt_ids=[])


def test_validate_resolved_join_keys_allows_group_runtime_with_resolved_pjt_id_fallback():
    validate_resolved_join_keys(mode="group", pjt_nos=[], pjt_ids=["1711015550"])


def test_validate_resolved_join_keys_rejects_group_runtime_without_materialized_keys():
    with pytest.raises(ValueError, match="EXECUTOR_GROUP_RUNTIME_KEY_REQUIRED"):
        validate_resolved_join_keys(mode="group", pjt_nos=[], pjt_ids=[])


def test_validate_resolved_join_keys_rejects_instance_runtime_without_pjt_id():
    with pytest.raises(ValueError, match="EXECUTOR_INSTANCE_PJT_ID_REQUIRED"):
        validate_resolved_join_keys(mode="instance", pjt_nos=[], pjt_ids=[])


def test_validate_resolved_join_keys_rejects_instance_runtime_with_pjt_no():
    with pytest.raises(ValueError, match="EXECUTOR_INSTANCE_PJT_NO_FORBIDDEN"):
        validate_resolved_join_keys(mode="instance", pjt_nos=["PJT-2020-1234-5678"], pjt_ids=["1711015550"])
