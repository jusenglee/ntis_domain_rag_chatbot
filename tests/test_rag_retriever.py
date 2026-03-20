from __future__ import annotations

from types import SimpleNamespace

from apps.api.services.rag_retriever import resolve_rag_queries


def test_resolve_rag_queries_prefers_knowledge_sufficiency_over_question_analysis():
    state = SimpleNamespace(
        question="raw query",
        intent_payload=SimpleNamespace(
            normalized_intent=SimpleNamespace(retrieval_query="intent-query"),
        ),
    )
    qa = SimpleNamespace(retrieval_query="qa-query", confidence=0.1)
    ks = SimpleNamespace(retrieval_query="ks-query", confidence=0.9)

    raw_query, hint_query, search_query, confidence = resolve_rag_queries(
        state=state,
        qa=qa,
        ks=ks,
        min_confidence=0.55,
    )

    assert raw_query == "raw query"
    assert hint_query == "ks-query"
    assert search_query == "ks-query"
    assert confidence == 0.9


def test_resolve_rag_queries_prefers_normalized_intent_over_question_analysis_when_ks_missing():
    state = SimpleNamespace(
        question="raw query",
        intent_payload=SimpleNamespace(
            normalized_intent=SimpleNamespace(retrieval_query="intent-query"),
        ),
    )
    qa = SimpleNamespace(retrieval_query="qa-query", confidence=0.8)

    _, hint_query, search_query, confidence = resolve_rag_queries(
        state=state,
        qa=qa,
        ks=None,
        min_confidence=0.55,
    )

    assert hint_query == "intent-query"
    assert search_query == "intent-query"
    assert confidence == 0.8

from apps.api.services.rag_retriever import CustomRAGRetriever


def test_custom_rag_retriever_formats_reverse_trace_documents(monkeypatch):
    reverse_trace = {
        "relation_chain": ["perf", "project", "perf"],
        "origin_perf": [{"doc_id": "paper-1", "perf_title": "Digital Twin Paper"}],
        "origin_projects": [{"pjt_id": "1711015550", "pjt_no": "PJT-2020-1234-5678", "project_title": "Digital Twin Project"}],
        "followup_perf": [{"doc_id": "paper-2", "perf_title": "Digital Twin Patent"}],
    }

    monkeypatch.setattr(
        "apps.api.services.rag_retriever.run_rag_ab_compare",
        lambda **kwargs: {
            "M": SimpleNamespace(
                reranked_hits=[],
                aggregation=None,
                series=None,
                reverse_trace=reverse_trace,
                canonical_evidence=[{"kind": "project"}],
                render_profile={"name": "relation"},
                timings={},
            )
        },
    )

    retriever = CustomRAGRetriever(model_name="test", top_k=3)
    payload = retriever.retrieve("digital twin origin project")

    assert payload["documents"][0]["source_type"] == "reverse_trace"
    assert payload["documents"][0]["origin_project"]["pjt_id"] == "1711015550"
    assert payload["documents"][0]["followup_perf"][0]["doc_id"] == "paper-2"


def test_custom_rag_retriever_formats_pattern_analysis_documents(monkeypatch):
    pattern_analysis = {
        "status": "ok",
        "pattern_kind": "perf_mix_gap",
        "candidate_docs": 4,
        "subject_count": 1,
        "support_doc_count": 3,
        "items": [
            {
                "project_title": "AI Forecast",
                "group_key": "1711015550",
                "paper_count": 3,
                "patent_count": 0,
                "report_count": 1,
                "gap_kind": "paper_without_patent",
            }
        ],
    }

    monkeypatch.setattr(
        "apps.api.services.rag_retriever.run_rag_ab_compare",
        lambda **kwargs: {
            "M": SimpleNamespace(
                reranked_hits=[],
                aggregation=None,
                series=None,
                reverse_trace=None,
                pattern_analysis=pattern_analysis,
                canonical_evidence=[{"kind": "project"}],
                render_profile={"name": "stats"},
                timings={},
            )
        },
    )

    retriever = CustomRAGRetriever(model_name="test", top_k=3)
    payload = retriever.retrieve("pattern gap")

    assert payload["documents"][0]["source_type"] == "pattern_analysis"
    assert payload["documents"][0]["pattern_kind"] == "perf_mix_gap"
    assert payload["documents"][0]["pattern_item"]["gap_kind"] == "paper_without_patent"


def test_custom_rag_retriever_formats_multi_hop_bundle_documents(monkeypatch):
    multi_hop_bundle = {
        "status": "partial",
        "bundle_kind": "project_outputs",
        "projects": [{"pjt_id": "1711015550", "project_title": "AI Forecast"}],
        "bundles": [
            {"target_kind": "paper", "item_count": 1, "selection_policy": "top_reranked", "items": [{"perf_title": "AI Forecast Paper", "perf_type": "paper"}]},
            {"target_kind": "patent", "item_count": 1, "selection_policy": "top_reranked", "items": [{"perf_title": "AI Forecast Patent", "perf_type": "patent"}]},
        ],
        "ambiguities": ["researcher_name_only"],
        "guidance_message": "동일한 과제번호 후보가 여러 개여서 구분이 필요합니다.",
    }

    monkeypatch.setattr(
        "apps.api.services.rag_retriever.run_rag_ab_compare",
        lambda **kwargs: {
            "M": SimpleNamespace(
                reranked_hits=[],
                aggregation=None,
                series=None,
                reverse_trace=None,
                pattern_analysis=None,
                multi_hop_bundle=multi_hop_bundle,
                canonical_evidence=[{"kind": "project"}],
                render_profile={"name": "relation"},
                timings={},
            )
        },
    )

    retriever = CustomRAGRetriever(model_name="test", top_k=3)
    payload = retriever.retrieve("bundle")

    assert payload["documents"][0]["source_type"] == "multi_hop_bundle"
    assert payload["documents"][0]["bundle_kind"] == "project_outputs"
    assert payload["documents"][0]["bundles"][0]["target_kind"] == "paper"


from apps.core.schemas import IntentPayloadV3


def test_build_rag_intent_payload_emits_v3_transport_fields():
    payload = IntentPayloadV3(
        intent_payload_version="v3",
        normalized_intent=SimpleNamespace(target_cols=["ntis_project_v1"]),
        question_analysis=SimpleNamespace(strategy_version="v3"),
        strategy_meta={"project_key_policy": "ambiguous_or"},
    )

    out = CustomRAGRetriever._build_rag_intent_payload(payload)

    assert out["intent_payload_version"] == "v3"
    assert out["question_analysis"].strategy_version == "v3"
    assert out["strategy_meta"]["project_key_policy"] == "ambiguous_or"
