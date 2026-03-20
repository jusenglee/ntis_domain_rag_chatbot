from __future__ import annotations

from apps.api.services.answer_generation import build_answer_context


def test_build_answer_context_prefers_canonical_evidence():
    result = build_answer_context(
        docs_for_ctx=[{"title": "legacy"}],
        canonical_evidence=[
            {
                "identity": "1711015550",
                "ids": {"pjt_id": "1711015550", "pjt_no": "PJT-2020-1234-5678"},
                "facts": {"title": "AI related project", "summary": "project summary", "year": "2024"},
                "roles": {"lead_org_name": ["ETRI"], "participant_org_name": ["KISTI"]},
            }
        ],
        render_profile={"name": "detail", "context_kind": "project"},
        qa=None,
        model_name="gemma_triton_0",
        refine_documents_rule_based_fn=lambda *args, **kwargs: "LEGACY_CONTEXT",
        priority_context_fields=(),
        max_field_sentences=3,
        max_field_tokens=120,
        default_max_doc_sentences=3,
        default_max_doc_tokens=120,
        solar_max_doc_sentences=3,
        solar_max_doc_tokens=120,
        solar_max_context_chars=1000,
        split_sentences_fn=lambda text: [line for line in text.splitlines() if line.strip()],
        logger=None,
    )

    assert result["context_source"] == "canonical_evidence"
    assert "RenderProfile" in result["context_text"]
    assert "PJT_ID=1711015550" in result["context_text"]
    assert "LEAD_ORG=ETRI" in result["context_text"]
    assert "LEGACY_CONTEXT" not in result["context_text"]


def test_build_answer_context_derives_canonical_evidence_from_retrieved_docs():
    result = build_answer_context(
        docs_for_ctx=[
            {
                "pjt_id": "1711015550",
                "pjt_no": "PJT-2020-1234-5678",
                "org_nm": "ETRI",
                "title_text": "AI related project",
                "summary": "project summary",
            }
        ],
        canonical_evidence=[],
        render_profile={"name": "detail", "context_kind": "project"},
        qa=None,
        model_name="gemma_triton_0",
        refine_documents_rule_based_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy renderer must not run")),
        priority_context_fields=(),
        max_field_sentences=3,
        max_field_tokens=120,
        default_max_doc_sentences=3,
        default_max_doc_tokens=120,
        solar_max_doc_sentences=3,
        solar_max_doc_tokens=120,
        solar_max_context_chars=1000,
        split_sentences_fn=lambda text: [line for line in text.splitlines() if line.strip()],
        logger=None,
    )

    assert result["context_source"] == "derived_canonical_evidence"
    assert "PJT_ID=1711015550" in result["context_text"]
    assert "LEAD_ORG=ETRI" in result["context_text"]


def test_build_answer_context_prefers_normalized_intent_over_question_analysis_for_profile_fallback():
    result = build_answer_context(
        docs_for_ctx=[
            {
                "pjt_id": "1711015550",
                "pjt_no": "PJT-2020-1234-5678",
                "org_nm": "ETRI",
                "title_text": "AI related project",
                "summary": "project summary",
            }
        ],
        canonical_evidence=[],
        render_profile={},
        normalized_intent={"output_type": "relation", "base_route": "perf"},
        strategy={"mode": "join"},
        qa=type("QA", (), {"output_type": "summary", "head": "project"})(),
        model_name="gemma_triton_0",
        refine_documents_rule_based_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy renderer must not run")),
        priority_context_fields=(),
        max_field_sentences=3,
        max_field_tokens=120,
        default_max_doc_sentences=3,
        default_max_doc_tokens=120,
        solar_max_doc_sentences=3,
        solar_max_doc_tokens=120,
        solar_max_context_chars=1000,
        split_sentences_fn=lambda text: [line for line in text.splitlines() if line.strip()],
        logger=None,
    )

    assert result["context_source"] == "derived_canonical_evidence"
    assert "[RenderProfile] name=relation kind=perf" in result["context_text"]
