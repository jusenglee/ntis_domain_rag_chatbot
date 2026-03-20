from __future__ import annotations

from pathlib import Path
import pytest

from apps.core.rag_dense_runtime_support import build_dense_runtime_support


class DummyViolation(Exception):
    def __init__(self, *, error_code: str, reason: str):
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class Point:
    def __init__(self, score=None, payload=None):
        self.score = score
        self.payload = payload if payload is not None else {}


class NoPayloadPoint:
    pass


def _support(retrieve_fn, *, logs=None):
    sink = logs if logs is not None else []
    return build_dense_runtime_support(
        dense_retrieve_hybrid_multi_fn=retrieve_fn,
        strategy_violation_cls=DummyViolation,
        log_kv_fn=lambda *args, **kwargs: sink.append((args, kwargs)),
    )


def test_precomputed_embedding_returns_same_vector_for_query_and_text():
    support = _support(lambda **kwargs: {})
    emb = support.precomputed_embedding_cls([0.1, 0.2])
    assert emb.get_query_embedding("q") == [0.1, 0.2]
    assert emb.get_text_embedding("t") == [0.1, 0.2]


def test_call_dense_retrieve_hybrid_multi_supports_client_signature():
    captured = {}

    def retrieve_fn(*, client, expanded_text, keywords, collection_name, query_filter, **kwargs):
        captured.update(
            {
                "client": client,
                "expanded_text": expanded_text,
                "keywords": keywords,
                "collection_name": collection_name,
                "query_filter": query_filter,
                **kwargs,
            }
        )
        return {"dense": {}, "lexical": [], "hybrid": []}

    support = _support(retrieve_fn)
    result = support.call_dense_retrieve_hybrid_multi_fn(
        qdr="client-obj",
        emb_map={"e5i": object()},
        qtext="hello",
        kws=["kw"],
        collection="ntis_project_v1",
        lexical_fields=["title"],
        sparse_vector_name="bm25",
        sparse_topk=20,
        top_k_dense=10,
        top_k_lex_cand=30,
        top_k_lex=15,
        query_filter={"kind": "f"},
        timings_out={},
    )

    assert result == {"dense": {}, "lexical": [], "hybrid": []}
    assert captured["client"] == "client-obj"
    assert captured["collection_name"] == "ntis_project_v1"
    assert captured["expanded_text"] == "hello"
    assert captured["sparse_vector_name"] == "bm25"
    assert captured["top_k_lexical"] == 15



def test_call_dense_retrieve_hybrid_multi_supports_qdr_signature():
    captured = {}

    def retrieve_fn(*, qdr, collection, query_text, keywords, filter_obj, **kwargs):
        captured.update(
            {
                "qdr": qdr,
                "collection": collection,
                "query_text": query_text,
                "keywords": keywords,
                "filter_obj": filter_obj,
                **kwargs,
            }
        )
        return {"dense": {}, "lexical": [], "hybrid": []}

    support = _support(retrieve_fn)
    support.call_dense_retrieve_hybrid_multi_fn(
        qdr="qdr-obj",
        emb_map={"e5i": object()},
        qtext="hello",
        kws=["kw"],
        collection="ntis_perf_v1",
        lexical_fields=["title"],
        sparse_vector_name="bm25",
        sparse_topk=20,
        top_k_dense=10,
        top_k_lex_cand=30,
        top_k_lex=15,
        query_filter={"kind": "f"},
        timings_out={},
    )

    assert captured["qdr"] == "qdr-obj"
    assert captured["collection"] == "ntis_perf_v1"
    assert captured["query_text"] == "hello"
    assert captured["filter_obj"] == {"kind": "f"}



def test_hybrid_contract_requires_dense_and_sparse_when_enabled():
    support = _support(lambda **kwargs: {})

    with pytest.raises(DummyViolation) as exc:
        support.call_dense_retrieve_hybrid_multi_fn(
            qdr=object(),
            emb_map={},
            qtext="hello",
            kws=["kw"],
            collection="ntis_project_v1",
            lexical_fields=["title"],
            sparse_vector_name="bm25",
            sparse_topk=20,
            top_k_dense=10,
            top_k_lex_cand=30,
            top_k_lex=15,
            query_filter=None,
            timings_out={},
            require_hybrid_both_sides=True,
            contract_scope="lookup.project",
            violation_on_contract=True,
        )

    assert exc.value.error_code == "LOOKUP_JOIN_DENSE_REQUIRED"



def test_ensure_collection_mark_handles_dict_and_object_payloads():
    support = _support(lambda **kwargs: {})
    dict_point = {"payload": {"title": "x"}}
    object_point = Point(payload={"title": "y"})
    missing_payload = NoPayloadPoint()

    support.ensure_collection_mark_fn([dict_point, object_point, missing_payload], "ntis_project_v1")

    assert dict_point["payload"]["_collection"] == "ntis_project_v1"
    assert dict_point["_collection"] == "ntis_project_v1"
    assert object_point.payload["_collection"] == "ntis_project_v1"
    assert getattr(missing_payload, "payload")["_collection"] == "ntis_project_v1"



def test_dense_score_weight_handles_empty_same_and_mixed_scores():
    support = _support(lambda **kwargs: {})
    assert support.dense_score_weight_fn([]) == 1.0
    assert support.dense_score_weight_fn([Point(0.5), Point(0.5)]) == 1.0
    mixed = support.dense_score_weight_fn([Point(0.2), Point(0.6), Point(1.0)])
    assert mixed == pytest.approx(0.5)



def test_apply_dense_threshold_filters_and_logs_stats():
    logs = []
    support = _support(lambda **kwargs: {}, logs=logs)
    sr = {"dense": {"e5i_qa": [Point(0.2), Point(0.7), Point(0.9)]}}

    support.apply_dense_threshold_fn(
        sr,
        use_dense_threshold=True,
        min_dense_score=0.5,
        log_prefix="RAG.DENSE.THRESHOLD",
        col="ntis_project_v1",
    )

    kept_scores = [p.score for p in sr["dense"]["e5i_qa"]]
    assert kept_scores == [0.7, 0.9]
    assert any(args and args[0] == "RAG.DENSE.THRESHOLD" for args, _ in logs)
    assert any(args and args[0] == "RAG.DENSE.SCORE.STATS" for args, _ in logs)



def test_rag_pipeline_uses_dense_support_module():
    source = Path("apps/core/rag_pipeline.py").read_text(encoding="utf-8")
    assert "build_dense_runtime_support(" in source
    assert "def _call_dense_retrieve_hybrid_multi(" not in source
    assert "def _apply_dense_threshold(" not in source
    assert "def _dense_score_weight(" not in source
    assert "def _ensure_collection_mark(" not in source
    assert "def _validate_lookup_join_hybrid_metrics(" not in source
