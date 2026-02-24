from __future__ import annotations

import pytest

from rag_parts.filters import (
    validate_group_join_runtime_keys,
    validate_join_filter_must_keys,
    validate_planner_join_keys,
    validate_resolved_join_keys,
)


class _Cond:
    def __init__(self, key: str):
        self.key = key


def test_planner_join_keys_keeps_xor_contract():
    with pytest.raises(ValueError, match="PLANNER_MIXED_PROJECT_KEYS"):
        validate_planner_join_keys(
            mode="join",
            ids_map={"pjt_id": ["202300001234"], "pjt_no": ["PNO-1"]},
        )


def test_resolved_group_mode_allows_optional_pjt_ids_when_pjt_no_exists():
    validate_resolved_join_keys(mode="group", pjt_nos=["PNO-1"], pjt_ids=["202300001234"])


def test_resolved_group_mode_requires_seed_pjt_no():
    with pytest.raises(ValueError, match="EXECUTOR_GROUP_PJT_NO_REQUIRED"):
        validate_resolved_join_keys(mode="group", pjt_nos=[], pjt_ids=["202300001234"])


def test_group_runtime_validation_allows_pjt_id_only_fallback():
    validate_group_join_runtime_keys(pjt_nos=[], pjt_ids=["202300001234"])


def test_group_runtime_validation_rejects_empty_keys():
    with pytest.raises(ValueError, match="EXECUTOR_GROUP_RUNTIME_KEY_REQUIRED"):
        validate_group_join_runtime_keys(pjt_nos=[], pjt_ids=[])


def test_resolved_instance_mode_rejects_pjt_nos():
    with pytest.raises(ValueError, match="EXECUTOR_INSTANCE_PJT_NO_FORBIDDEN"):
        validate_resolved_join_keys(mode="instance", pjt_nos=["PNO-1"], pjt_ids=["202300001234"])


def test_must_key_mismatch_rejected_by_mode():
    with pytest.raises(ValueError):
        validate_join_filter_must_keys(mode="group", must_conditions=[_Cond("pjt_id")])

    with pytest.raises(ValueError):
        validate_join_filter_must_keys(mode="instance", must_conditions=[_Cond("pjt_no")])
