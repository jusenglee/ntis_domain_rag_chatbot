from __future__ import annotations

import pytest

from rag_parts.filters import validate_join_filter_must_keys, validate_join_mode_key_inputs


class _Cond:
    def __init__(self, key: str):
        self.key = key


def test_group_mode_rejects_join_ids():
    with pytest.raises(ValueError):
        validate_join_mode_key_inputs(mode="group", join_ids=["202300001234"], pjt_nos=["PNO-1"])


def test_instance_mode_rejects_pjt_nos():
    with pytest.raises(ValueError):
        validate_join_mode_key_inputs(mode="instance", join_ids=["202300001234"], pjt_nos=["PNO-1"])


def test_must_key_mismatch_rejected_by_mode():
    with pytest.raises(ValueError):
        validate_join_filter_must_keys(mode="group", must_conditions=[_Cond("pjt_id")])

    with pytest.raises(ValueError):
        validate_join_filter_must_keys(mode="instance", must_conditions=[_Cond("pjt_no")])
