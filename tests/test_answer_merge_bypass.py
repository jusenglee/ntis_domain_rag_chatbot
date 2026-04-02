from apps.api.contracts.answer_groundedness import build_groundedness_snapshot_from_canonical_evidence
from apps.chat.answer_merge import select_final_answer


def _groundedness_snapshot(*, visible_count: int = 2):
    return build_groundedness_snapshot_from_canonical_evidence(
        canonical_evidence=[
            {
                "ids": {"pjt_id": "PJT-123", "pjt_no": "NO-123", "rst_id": "RST-321"},
                "facts": {"year": "2025", "title": "테스트 과제"},
                "roles": {"lead_org_name": ["한국전자통신연구원"]},
            }
        ],
        visible_count=visible_count,
    )


def test_select_final_answer_keeps_typed_short_answer_as_valid_solar_result():
    result = select_final_answer(
        answer_solar="tiny",
        answer_gemma="",
        solar_meta={
            "answer_kind": "detail_profile",
            "answer_source": "detail_lookup",
        },
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=60,
    )

    assert result["selected_model"] == "solar"
    assert result["selected_answer"] == "tiny"
    assert result["solar_failed"] is False
    assert result["solar_fail_reasons"] == []


def test_select_final_answer_degrades_context_refusal_and_falls_back_when_gemma_is_too_short():
    result = select_final_answer(
        answer_solar="제공된 정보에서 찾기 어렵습니다. 주어진 정보만으로는 안내가 어렵습니다.",
        answer_gemma="정상 답변",
        solar_meta={"answer_kind": "llm_collected", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
    )

    assert result["selected_model"] == "fallback"
    assert result["selected_answer"] == "fallback"
    assert result["solar_failed"] is True
    assert "refusal_like_answer" in result["solar_fail_reasons"]


def test_select_final_answer_uses_fallback_when_refusal_has_no_gemma():
    result = select_final_answer(
        answer_solar="주어진 정보만으로는 확인할 수 없습니다.",
        answer_gemma="",
        solar_meta={"answer_kind": "llm_streamed", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
    )

    assert result["selected_model"] == "fallback"
    assert result["selected_answer"] == "fallback"


def test_select_final_answer_degrades_included_info_refusal_phrase_and_falls_back():
    result = select_final_answer(
        answer_solar="신동구 연구자의 활동내역 20건은 제공된 정보에 포함되어 있지 않습니다. 따라서 요청하신 내용을 안내할 수 없습니다.",
        answer_gemma="gemma answer",
        solar_meta={"answer_kind": "llm_streamed", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
    )

    assert result["selected_model"] == "fallback"
    assert result["selected_answer"] == "fallback"
    assert "refusal_like_answer" in result["solar_fail_reasons"]


def test_select_final_answer_degrades_internal_detail_context_leak_and_prefers_gemma():
    result = select_final_answer(
        answer_solar="[detail_evidence]\nentity_kind: project\npjt_id: 1711135956\ntitle: 단일 반도체물질 기반 3진 논리 게이트 개발",
        answer_gemma="과제명은 단일 반도체물질 기반 3진 논리 게이트 개발입니다.",
        solar_meta={"answer_kind": "llm_streamed", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
    )

    assert result["selected_model"] == "gemma"
    assert "internal_context_leak" in result["solar_fail_reasons"]


def test_select_final_answer_falls_back_when_both_models_are_invalid():
    result = select_final_answer(
        answer_solar="제공된 정보에 포함되어 있지 않아 안내가 어렵습니다.",
        answer_gemma="질문을 입력해주세요.\n[detail_evidence]\nentity_kind: project",
        solar_meta={"answer_kind": "llm_streamed", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
    )

    assert result["selected_model"] == "fallback"
    assert result["selected_answer"] == "fallback"
    assert "internal_context_leak" in result["gemma_fail_reasons"]


def test_select_final_answer_degrades_inline_detail_evidence_suffix():
    result = select_final_answer(
        answer_solar="연구 책임자: 김봉준 [detail_evidence]\n연구 책임자 소속: 숙명여자대학 [detail_evidence]",
        answer_gemma="연구 책임자는 김봉준이며 소속은 숙명여자대학입니다.",
        solar_meta={"answer_kind": "llm_streamed", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
    )

    assert result["selected_model"] == "gemma"
    assert "internal_context_leak" in result["solar_fail_reasons"]


def test_select_final_answer_degrades_schema_parenthetical_labels_and_prefers_clean_gemma():
    result = select_final_answer(
        answer_solar="과제 ID: 2410012330 (pjt_id)\n수행기관: 한국자동차연구원 (lead_org)",
        answer_gemma="과제 ID는 2410012330이고 수행기관은 한국자동차연구원입니다.",
        solar_meta={"answer_kind": "llm_streamed", "answer_source": "solar"},
        gemma_meta={"answer_kind": "llm_streamed", "answer_source": "gemma"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
    )

    assert result["selected_model"] == "gemma"
    assert "internal_context_leak" in result["solar_fail_reasons"]


def test_select_final_answer_degrades_raw_missing_field_inventory():
    result = select_final_answer(
        answer_solar="outputs, biz_no, doi, issn, org_code, org_id, participant_org, rst_id 필드는 제공된 정보에서 확인할 수 없습니다.",
        answer_gemma="성과물 정보는 제공된 자료에서 확인되지 않습니다.",
        solar_meta={"answer_kind": "llm_streamed", "answer_source": "solar"},
        gemma_meta={"answer_kind": "llm_streamed", "answer_source": "gemma"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
    )

    assert result["selected_model"] == "gemma"
    assert "internal_context_leak" in result["solar_fail_reasons"]


def test_select_final_answer_reports_supported_structured_groundedness_passively():
    result = select_final_answer(
        answer_solar="과제 ID는 PJT-123입니다. 연도는 2025년이며 수행기관은 한국전자통신연구원입니다. 총 2건입니다.",
        answer_gemma="",
        solar_meta={"answer_kind": "llm_collected", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
        evidence_snapshot=_groundedness_snapshot(),
    )

    assert result["selected_model"] == "solar"
    assert result["solar_failed"] is False
    assert result["solar_groundedness"]["status"] == "supported"
    assert result["solar_groundedness"]["reason_codes"] == []


def test_select_final_answer_reports_unsupported_structured_groundedness_passively():
    result = select_final_answer(
        answer_solar="과제 ID는 PJT-999입니다. 연도는 2024년이며 수행기관은 한국과학기술원입니다. 총 3건입니다.",
        answer_gemma="",
        solar_meta={"answer_kind": "llm_collected", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
        evidence_snapshot=_groundedness_snapshot(),
    )

    assert result["selected_model"] == "solar"
    assert result["solar_failed"] is False
    assert result["solar_groundedness"]["status"] == "unsupported"
    assert "unsupported_project_id" in result["solar_groundedness"]["reason_codes"]
    assert "unsupported_year" in result["solar_groundedness"]["reason_codes"]
    assert "unsupported_org_name" in result["solar_groundedness"]["reason_codes"]
    assert "unsupported_count" in result["solar_groundedness"]["reason_codes"]


def test_select_final_answer_marks_groundedness_insufficient_without_snapshot():
    result = select_final_answer(
        answer_solar="과제 ID는 PJT-123입니다. 연도는 2025년입니다.",
        answer_gemma="",
        solar_meta={"answer_kind": "llm_collected", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
        evidence_snapshot={"available": False, "snapshot_source": "none"},
    )

    assert result["selected_model"] == "solar"
    assert result["solar_groundedness"]["status"] == "insufficient_snapshot"
    assert result["solar_groundedness"]["reason_codes"] == ["insufficient_snapshot"]


def test_select_final_answer_skips_groundedness_for_bypass_answer_kind():
    result = select_final_answer(
        answer_solar="cached detail",
        answer_gemma="",
        solar_meta={"answer_kind": "detail_cache", "answer_source": "detail_cache"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
        evidence_snapshot=_groundedness_snapshot(),
    )

    assert result["selected_model"] == "solar"
    assert result["solar_failed"] is False
    assert result["solar_groundedness"]["status"] == "skipped_bypass_kind"


def test_select_final_answer_prefers_supported_model_when_visible_order_is_protected():
    result = select_final_answer(
        answer_solar="PJT_ID=PJT-999, year=2025, 수행기관: 미등록기관",
        answer_gemma="PJT_ID=PJT-123, year=2025, 수행기관: 한국전자통신연구원",
        solar_meta={"answer_kind": "llm_collected", "answer_source": "solar"},
        gemma_meta={"answer_kind": "llm_collected", "answer_source": "gemma"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=10,
        evidence_snapshot=_groundedness_snapshot(),
        protect_visible_order=True,
    )

    assert result["selected_model"] == "gemma"
    assert result["selection_reason"] == "gemma_visible_order_safe"
