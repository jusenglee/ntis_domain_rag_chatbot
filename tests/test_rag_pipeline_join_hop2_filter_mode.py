from __future__ import annotations

from typing import Any, Dict

import pytest
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
    assert executed_spec["_meta"]["join_ids_count"] == 0
    assert executed_spec["_meta"]["pjt_nos_count"] == 1


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
    assert executed_spec["_meta"]["join_ids_count"] == 1
    assert executed_spec["_meta"]["pjt_nos_count"] == 0


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

    assert executed_spec["_meta"]["planner_hop2_filter_applied"] == 1
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


def test_diff_filter_spec_semantic_subset_allows_executed_meta_on_join_filter() -> None:
    planner_filter_spec = {
        "join_filter": {
            "must": [
                {"key": "pjt_id", "match": {"any": ["202300001234"]}},
            ]
        }
    }
    executed_filter_spec = {
        "join_filter": {
            "must": [
                {"key": "pjt_id", "match": {"any": ["202300001234"]}},
            ],
            "should": [],
            "_meta": {
                "join_key_mode": "instance",
                "join_ids_count": 1,
            },
        }
    }

    diff = rag_pipeline._diff_filter_spec(
        planner_filter_spec=planner_filter_spec,
        executed_filter_spec=executed_filter_spec,
    )

    assert diff["planner_keys"] == ["join_filter"]
    assert diff["changed"] == {}


def test_join_hop2_filter_group_perf_uses_pjt_id_fallback_when_pjt_no_missing(monkeypatch) -> None:
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
        join_pjt_nos=[],
        join_ids=[],
        q="질문",
        hop2_tag_filters=None,
        people_terms=[],
        org_terms=[],
        planner_filter_spec={},
    )

    assert captured["join_key_mode"] == "instance"
    assert captured["join_ids"] == []
    assert captured["resolved_pjt_ids"] == ["202300001234"]
    assert executed_spec["_meta"]["join_key_mode"] == "group"
    assert executed_spec["_meta"]["effective_perf_join_mode"] == "instance"
    assert executed_spec["_meta"]["join_compile_selection"] == "group_perf_pjt_id_fallback"


def test_join_group_keys_unresolved_error_code_on_empty_hop1() -> None:
    with pytest.raises(rag_pipeline.StrategyViolation) as exc:
        rag_pipeline._ensure_join_mode_has_keys(
            has_join_keys=False,
            join_key_mode="group",
            hop1_top=[],
            hop1_col="ntis_project",
            join_pjt_ids_count=0,
            join_pjt_nos_count=0,
        )

    assert exc.value.error_code == "JOIN_GROUP_KEYS_UNRESOLVED"
