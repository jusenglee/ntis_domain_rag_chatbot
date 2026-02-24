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
    assert captured["resolved_pjt_ids"] == ["202300001234"]
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
    assert captured["resolved_pjt_ids"] == ["202300001234"]
    assert executed_spec["join_ids_count"] == 1
    assert executed_spec["pjt_nos_count"] == 0


def test_join_hop2_filter_applies_compiled_spec_filter(monkeypatch) -> None:
    captured: Dict[str, Any] = {}

    def _fake_build_collection_join_filter(**kwargs):
        captured.update(kwargs)
        return {"must": ["base"]}

    monkeypatch.setattr(rag_pipeline, "build_collection_join_filter", _fake_build_collection_join_filter)

    hop2_filter, executed_spec = rag_pipeline._build_join_hop2_filter(
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
        compiled_hop2_spec={"collection": "ntis_perf", "qdrant_filter": {"must": ["planner"]}},
    )

    assert executed_spec["planner_hop2_filter_applied"] == 1
    assert hop2_filter == {"must": ["base", "planner"]}


def test_join_hop2_filter_raises_on_compiled_spec_collection_mismatch(monkeypatch) -> None:
    def _fake_build_collection_join_filter(**kwargs):
        return {"must": []}

    monkeypatch.setattr(rag_pipeline, "build_collection_join_filter", _fake_build_collection_join_filter)

    try:
        rag_pipeline._build_join_hop2_filter(
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
            compiled_hop2_spec={"collection": "ntis_project"},
        )
    except rag_pipeline.StrategyViolation as exc:
        assert exc.error_code == "PLANNER_JOIN_HOP2_COLLECTION_MISMATCH"
    else:
        raise AssertionError("expected StrategyViolation")
