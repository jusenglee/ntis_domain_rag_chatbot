from __future__ import annotations

from pathlib import Path

from apps.core.rag_runtime_observability import (
    build_runtime_observability,
    get_code_fingerprint_fields,
)


def _observability():
    return build_runtime_observability(
        get_meta_fn=lambda payload: dict(payload.get("meta") or {}),
        resolve_collection_fn=lambda point, payload: str(payload.get("_collection") or ""),
    )


def test_timing_put_adds_dup_keys_when_value_changes():
    obs = _observability()
    timings = obs.init_timings_fn()
    obs.timing_put_fn(timings, "phase.total", 1.0)
    obs.timing_put_fn(timings, "phase.total", 2.0)
    assert timings["phase.total"] == 1.0
    assert timings["phase.total.dup2"] == 2.0


def test_record_col_timings_writes_stats_and_phase_keys():
    obs = _observability()
    timings = obs.init_timings_fn()
    obs.record_col_timings_fn(timings, "ntis_project_v1", stats={"hits": 3}, local_timings={"dense": 0.4})
    assert timings["col.ntis_project_v1.stats.hits"] == 3.0
    assert timings["col.ntis_project_v1.phase.dense"] == 0.4


def test_resolve_env_topn_uses_fallback(monkeypatch):
    obs = _observability()
    monkeypatch.delenv("PRIMARY_TOPN", raising=False)
    monkeypatch.setenv("FALLBACK_TOPN", "11")
    assert obs.resolve_env_topn_fn("PRIMARY_TOPN", default=3, fallback_keys=["FALLBACK_TOPN"]) == 11


def test_merge_log_fields_suffixes_conflict():
    obs = _observability()
    merged = obs.merge_log_fields_fn({"a": 1}, {"a": 2, "b": 3})
    assert merged["a"] == 1
    assert merged["a_extra"] == 2
    assert merged["b"] == 3


def test_code_fingerprint_fields_keep_rag_pipeline_key():
    fields = get_code_fingerprint_fields()
    assert "rag_pipeline_sha256" in fields
    assert fields["rag_pipeline_sha256"]


def test_rag_pipeline_uses_observability_module():
    source = Path("apps/core/rag_pipeline.py").read_text(encoding="utf-8")
    assert "build_runtime_observability(" in source
    assert "def log_section(" not in source
    assert "def log_kv(" not in source
    assert "def log_top_points(" not in source
    assert "def _init_timings(" not in source
    assert "def _timing_put(" not in source
    assert "def _record_col_timings(" not in source
