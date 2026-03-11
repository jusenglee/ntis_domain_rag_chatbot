from __future__ import annotations

from pathlib import Path


def test_staging_debug_disable_planner_invalid_fallback() -> None:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    assert 'runtime_env = str(os.getenv("APP_ENV", os.getenv("ENV", "")) or "").strip().lower()' in source
    assert 'if runtime_env in {"staging", "debug"}:' in source
    assert 'planner_invalid_fallback = False' in source
    assert 'runtime_env=runtime_env or "unknown"' in source
