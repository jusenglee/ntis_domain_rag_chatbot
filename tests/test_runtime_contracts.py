from types import SimpleNamespace

from apps.api.contracts.runtime_contracts import friendly_strategy_violation_message, sanitize_ids_map_semantics


def test_friendly_strategy_violation_message_for_join_keys_missing():
    message = friendly_strategy_violation_message(
        error_code="JOIN_KEYS_MISSING",
        reason="join keys were not extracted from Hop1",
        question_analysis=SimpleNamespace(mode="JOIN", action="list", ids_map={}),
    )

    assert "과제 키" in message
    assert "다시 질문" in message


def test_friendly_strategy_violation_message_for_lookup_detail_empty_result():
    message = friendly_strategy_violation_message(
        error_code="RAG_EMPTY_RESULT_CONTRACT",
        reason="reranked result violated contract",
        question_analysis=SimpleNamespace(mode="LOOKUP", action="detail", ids_map={"pjt_id": ["1711015550"]}),
    )

    assert "조회 결과" in message
    assert "식별자" in message


def test_sanitize_ids_map_semantics_keeps_labeled_alphanumeric_project_id():
    cleaned, candidates, invalid = sanitize_ids_map_semantics(
        {"pjt_id": ["AI2024X001"]},
        question_text="과제고유번호 AI2024X001 상세 조회",
    )

    assert cleaned == {"pjt_id": ["AI2024X001"]}
    assert candidates == {}
    assert invalid == []


def test_sanitize_ids_map_semantics_moves_ambiguous_project_number_to_candidate_keys():
    cleaned, candidates, invalid = sanitize_ids_map_semantics(
        {"pjt_id": ["AI_SEMICONDUCTOR_2023"]},
        question_text="과제번호 AI_SEMICONDUCTOR_2023 관련 성과",
    )

    assert cleaned == {}
    assert candidates == {
        "project_key": [
            {
                "value": "AI_SEMICONDUCTOR_2023",
                "candidate_types": ["pjt_id", "pjt_no"],
                "source": "label:과제번호",
                "confidence": 0.35,
            }
        ]
    }
    assert invalid == [{"key": "pjt_id", "value": "AI_SEMICONDUCTOR_2023", "reason": "ambiguous_project_key"}]
