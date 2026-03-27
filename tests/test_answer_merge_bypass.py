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
