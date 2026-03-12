from __future__ import annotations

from pathlib import Path


def test_staging_debug_disable_planner_invalid_fallback() -> None:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    assert 'planner_pipeline = str(os.getenv("RAG_PLANNER_PIPELINE", "staged") or "staged").strip().lower()' in source
    assert 'if planner_pipeline == "staged":' in source
    assert 'runtime_env = str(os.getenv("APP_ENV", os.getenv("ENV", "")) or "").strip().lower()' in source
    assert 'if runtime_env in {"staging", "debug"}:' in source
    assert 'planner_invalid_fallback = False' in source
    assert 'runtime_env=runtime_env or "unknown"' in source


def test_staged_pipeline_disables_promotion() -> None:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    assert 'promotion_feature_mode = str(os.getenv("RAG_PROMOTION_MODE", "disable") or "disable").strip().lower()' in source
    assert 'if planner_pipeline == "staged":' in source
    assert 'promotion_enabled = False' in source
