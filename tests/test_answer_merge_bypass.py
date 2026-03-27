from apps.api.services.answer_merge import select_final_answer


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


def test_select_final_answer_degrades_context_refusal_and_prefers_gemma():
    result = select_final_answer(
        answer_solar="제공된 정보에서 찾기 어렵습니다. 주어진 정보만으로는 안내가 어렵습니다.",
        answer_gemma="정상 답변",
        solar_meta={"answer_kind": "llm_collected", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
    )

    assert result["selected_model"] == "gemma"
    assert result["selected_answer"] == "정상 답변"
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


def test_select_final_answer_degrades_included_info_refusal_phrase():
    result = select_final_answer(
        answer_solar="신동구 연구자의 활동내역 20건은 제공된 정보에 포함되어 있지 않습니다. 따라서 요청하신 내용을 안내할 수 없습니다.",
        answer_gemma="gemma answer",
        solar_meta={"answer_kind": "llm_streamed", "answer_source": "solar"},
        policy="solar_first",
        fallback_message="fallback",
        min_answer_chars=20,
    )

    assert result["selected_model"] == "gemma"
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
