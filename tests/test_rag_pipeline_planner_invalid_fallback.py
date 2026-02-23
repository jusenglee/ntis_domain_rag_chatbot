from __future__ import annotations

from pathlib import Path


def test_planner_invalid_fallback_flag_and_logs_exist() -> None:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")

    assert "RAG_PLANNER_INVALID_FALLBACK" in source
    assert "RAG.PLAN.FALLBACK_ON_INVALID_PLANNER" in source
    assert "RAG.PLAN.FALLBACK_ON_CONTRACT_VIOLATION" in source
    assert "ids_or_id_query=>lookup_else_search" in source
