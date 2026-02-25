from __future__ import annotations

from pathlib import Path

import rag_pipeline


def test_planner_invalid_fallback_flag_and_logs_exist() -> None:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")

    assert "RAG_PLANNER_INVALID_FALLBACK" in source
    assert "RAG.PLAN.FALLBACK_ON_INVALID_PLANNER" in source
    assert "RAG.PLAN.FALLBACK_ON_CONTRACT_VIOLATION" in source
    assert "ids_or_id_query=>lookup_else_search" in source


def test_join_hop1_filter_applies_compiled_spec_filter() -> None:
    hop1_filter, executed_spec = rag_pipeline._build_join_hop1_filter(
        relation=("project", "perf"),
        hop1_col="ntis_project_v1",
        hop1_filter={"must": ["base"]},
        compiled_hop1_spec={"collection": "project", "qdrant_filter": {"must": ["planner"]}},
    )

    assert executed_spec["planner_hop1_filter_applied"] == 1
    assert hop1_filter is not None
    assert hasattr(hop1_filter, "must")


def test_join_hop1_filter_raises_on_compiled_spec_collection_mismatch() -> None:
    try:
        rag_pipeline._build_join_hop1_filter(
            relation=("project", "perf"),
            hop1_col="ntis_project_v1",
            hop1_filter={"must": []},
            compiled_hop1_spec={"collection": "perf"},
        )
    except rag_pipeline.StrategyViolation as exc:
        assert exc.error_code == "PLANNER_JOIN_HOP1_COLLECTION_MISMATCH"
    else:
        raise AssertionError("expected StrategyViolation")


def test_planner_lock_resync_helper_and_fallback_usage() -> None:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")

    assert "def _derive_planner_locks(plan: QueryPlan)" in source
    assert "planner_mode_locked, planner_relation_locked, planner_target_cols_locked = _derive_planner_locks(plan)" in source
    assert source.count("planner_mode_locked, planner_relation_locked, planner_target_cols_locked = _derive_planner_locks(plan)") >= 4
