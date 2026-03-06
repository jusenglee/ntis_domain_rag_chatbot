from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_stub_modules() -> None:
    if "qdrant_client" not in sys.modules:
        qdrant_mod = types.ModuleType("qdrant_client")

        class _DummyQdrantClient:  # pragma: no cover - test import stub
            def __init__(self, *args, **kwargs):
                pass

        qdrant_mod.QdrantClient = _DummyQdrantClient
        sys.modules["qdrant_client"] = qdrant_mod

    if "qdrant_client.http" not in sys.modules:
        qdrant_http_mod = types.ModuleType("qdrant_client.http")
        qdrant_http_mod.models = types.SimpleNamespace()
        sys.modules["qdrant_client.http"] = qdrant_http_mod

    if "llama_index" not in sys.modules:
        sys.modules["llama_index"] = types.ModuleType("llama_index")
    if "llama_index.embeddings" not in sys.modules:
        sys.modules["llama_index.embeddings"] = types.ModuleType("llama_index.embeddings")
    if "llama_index.embeddings.huggingface" not in sys.modules:
        hf_mod = types.ModuleType("llama_index.embeddings.huggingface")

        class _DummyEmbedding:  # pragma: no cover - test import stub
            def __init__(self, *args, **kwargs):
                pass

        hf_mod.HuggingFaceEmbedding = _DummyEmbedding
        sys.modules["llama_index.embeddings.huggingface"] = hf_mod

    if "triton_client" not in sys.modules:
        triton_mod = types.ModuleType("triton_client")

        def _get_triton_client():  # pragma: no cover - test import stub
            return object()

        triton_mod.get_triton_client = _get_triton_client
        sys.modules["triton_client"] = triton_mod


def _load_module():
    _install_stub_modules()
    if "rag_pipeline" in sys.modules:
        return importlib.reload(sys.modules["rag_pipeline"])
    return importlib.import_module("rag_pipeline")


def test_hit_key_prefers_payload_doc_id_over_point_id() -> None:
    rag_pipeline = _load_module()

    point = SimpleNamespace(
        id="point-uuid-001",
        payload={"doc_id": "biz-doc-123", "_collection": "project"},
    )

    assert rag_pipeline._hit_key(point) == ("project", "biz-doc-123")


def test_normalize_values_zscore_with_all_equal_values_returns_half() -> None:
    rag_pipeline = _load_module()

    values = [7.0, 7.0, 7.0]
    norm = rag_pipeline._normalize_values(values, "zscore")

    assert norm == [0.5, 0.5, 0.5]


def test_normalize_values_zscore_handles_negative_values_and_preserves_order() -> None:
    rag_pipeline = _load_module()

    values = [-10.0, -3.0, -1.0]
    norm = rag_pipeline._normalize_values(values, "zscore")

    assert all(0.0 < v < 1.0 for v in norm)
    assert norm[0] < norm[1] < norm[2]


def test_normalize_values_minmax_with_negative_and_outlier_values() -> None:
    rag_pipeline = _load_module()

    values = [-10.0, -2.0, 30.0]
    norm = rag_pipeline._normalize_values(values, "minmax")

    assert norm[0] == 0.0
    assert norm[-1] == 1.0
    assert norm[0] < norm[1] < norm[2]


def test_join_instance_without_seed_keys_uses_hop1_search_path() -> None:
    rag_pipeline = _load_module()

    policy = rag_pipeline._resolve_join_execution_policy(
        relation=("perf", "project"),
        mode="join",
        action="relation",
        join_key_mode="instance",
        seed_join_pjt_ids=[],
        seed_join_pjt_nos=[],
        has_people_org_gate=False,
    )

    assert policy["hop1_strategy"] == "search"
    assert policy["reason"] == "default_hop1_search"
