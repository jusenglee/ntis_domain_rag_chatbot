from __future__ import annotations

from typing import Any, Dict

import rag_pipeline


def test_join_hop2_filter_group_mode_clears_join_ids(monkeypatch) -> None:
    captured: Dict[str, Any] = {}

    def _fake_build_collection_join_filter(**kwargs):
        captured.update(kwargs)
        return {"must": []}

    monkeypatch.setattr(rag_pipeline, "build_collection_join_filter", _fake_build_collection_join_filter)

    _, executed_spec = rag_pipeline._build_join_hop2_filter(
        relation=("project", "perf"),
        hop2_col="ntis_perf",
        join_key_mode="group",
        join_pjt_ids=["202300001234"],
        join_pjt_nos=["PNO-1"],
        join_ids=["should-be-cleared"],
        q="질문",
        hop2_tag_filters=None,
        people_terms=[],
        org_terms=[],
        planner_filter_spec={},
    )

    assert captured["join_ids"] == []
    assert captured["pjt_nos"] == ["PNO-1"]
    assert executed_spec["join_ids_count"] == 0
    assert executed_spec["pjt_nos_count"] == 1


def test_join_hop2_filter_instance_mode_uses_join_pjt_ids(monkeypatch) -> None:
    captured: Dict[str, Any] = {}

    def _fake_build_collection_join_filter(**kwargs):
        captured.update(kwargs)
        return {"must": []}

    monkeypatch.setattr(rag_pipeline, "build_collection_join_filter", _fake_build_collection_join_filter)

    _, executed_spec = rag_pipeline._build_join_hop2_filter(
        relation=("project", "perf"),
        hop2_col="ntis_perf",
        join_key_mode="instance",
        join_pjt_ids=["202300001234"],
        join_pjt_nos=[],
        join_ids=None,
        q="질문",
        hop2_tag_filters=None,
        people_terms=[],
        org_terms=[],
        planner_filter_spec={},
    )

    assert captured["join_ids"] == ["202300001234"]
    assert executed_spec["join_ids_count"] == 1
    assert executed_spec["pjt_nos_count"] == 0
