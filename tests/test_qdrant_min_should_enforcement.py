from __future__ import annotations

import pytest


def test_qdrant_should_gate_enforced_with_min_should_or_nested_gate() -> None:
    qdrant = pytest.importorskip("qdrant_client")
    models = pytest.importorskip("qdrant_client.http.models")

    client = qdrant.QdrantClient(":memory:")
    cname = "min_should_gate_test"
    client.recreate_collection(
        collection_name=cname,
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )
    client.upsert(
        collection_name=cname,
        points=[
            models.PointStruct(id=1, vector=[1.0, 0.0], payload={"name": "alpha"}),
            models.PointStruct(id=2, vector=[0.0, 1.0], payload={"name": "beta"}),
        ],
    )

    should = [models.FieldCondition(key="name", match=models.MatchValue(value="missing"))]

    min_should_filter = models.Filter(should=should, min_should={"min_count": 1})
    min_should_hits, _ = client.scroll(collection_name=cname, scroll_filter=min_should_filter, limit=10)
    assert len(min_should_hits) == 0

    nested_gate = models.Filter(must=[models.Filter(should=should)])
    nested_hits, _ = client.scroll(collection_name=cname, scroll_filter=nested_gate, limit=10)
    assert len(nested_hits) == 0
