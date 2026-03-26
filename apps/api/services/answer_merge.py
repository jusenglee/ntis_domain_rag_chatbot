"""Answer-selection policy shared by the dual-model generation path.\n\nThe selector is pure so fallback and timeout behavior can be tested without running the\ncomplete LangGraph workflow.\n"""

from __future__ import annotations

from typing import Any


def _looks_like_context_refusal(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).lower()
    if not normalized:
        return False
    return (
        ("제공된 정보" in normalized and ("찾을 수 없" in normalized or "확인할 수 없" in normalized or "안내가 어렵" in normalized))
        or "주어진 정보만으로" in normalized
    )


def select_final_answer(
    *,
    answer_solar: str,
    answer_gemma: str,
    solar_meta: dict[str, Any] | None,
    policy: str,
    fallback_message: str,
    min_answer_chars: int,
) -> dict[str, Any]:
    """Solar와 Gemma 답변, 스트리밍 메타, 정책 설정을 바탕으로 최종 답변을 선택한다.

    Solar 출력이 너무 짧거나 스트리밍 guard에 걸리면 Gemma나 fallback 메시지로 내리고,
    선택 근거와 실패 사유는 호출자가 그대로 로그와 merge_debug에 남길 수 있게 함께 반환한다.
    """
    solar_answer = (answer_solar or "").strip()
    gemma_answer = (answer_gemma or "").strip()
    meta = solar_meta or {}

    solar_fail_reasons: list[str] = []
    solar_warning_reasons: list[str] = []

    deadline_exceeded = bool(
        meta.get("deadline_exceeded")
        or meta.get("ttft_deadline_exceeded")
        or meta.get("gen_deadline_exceeded")
    )
    ttft_any_ms = meta.get("ttft_any_ms")
    ttft_content_ms = meta.get("ttft_content_ms")
    content_chars = int(meta.get("content_chars") or 0)
    stream_content_emitted_chunks = int(meta.get("stream_content_emitted_chunks") or 0)
    answer_kind = str(meta.get("answer_kind") or "").strip().lower()

    if deadline_exceeded:
        if stream_content_emitted_chunks == 0:
            solar_fail_reasons.append("deadline_without_stream_content")
        if ttft_any_ms is None:
            solar_fail_reasons.append("deadline_stream_not_started_or_stalled")
        elif ttft_content_ms is None and content_chars == 0:
            solar_fail_reasons.append("deadline_content_not_started")
        elif ttft_content_ms is None:
            solar_warning_reasons.append("deadline_content_delayed")
        else:
            solar_warning_reasons.append("deadline_with_partial_or_delayed_content")

    if bool(meta.get("char_limited")):
        solar_fail_reasons.append("char_limited")

    marker = (fallback_message or "").strip()
    if marker and marker in solar_answer:
        solar_fail_reasons.append("contains_fallback_notice")

    bypass_like_answer = answer_kind in {"detail_cache", "detail_profile", "no_result", "clarification", "direct_answer", "error"}
    refusal_like_answer = (
        answer_kind in {"llm_streamed", "llm_collected"}
        and _looks_like_context_refusal(solar_answer)
    )
    if refusal_like_answer:
        solar_fail_reasons.append("refusal_like_answer")
    if len(solar_answer) < min_answer_chars and not bypass_like_answer:
        solar_fail_reasons.append(f"too_short<{min_answer_chars}")

    solar_failed = bool(solar_fail_reasons)
    normalized_policy = str(policy or "solar_first").strip().lower() or "solar_first"
    if normalized_policy == "gemma_first" and gemma_answer:
        selected_model = "gemma"
        selected_answer = gemma_answer
        selection_reason = "policy_gemma_first"
    else:
        if solar_failed:
            if gemma_answer:
                selected_model = "gemma"
                selected_answer = gemma_answer
                selection_reason = "solar_failed_use_gemma"
            else:
                selected_model = "fallback"
                selected_answer = fallback_message
                selection_reason = "solar_failed_no_gemma"
        else:
            selected_model = "solar"
            selected_answer = solar_answer
            selection_reason = "solar_ok"

    if not selected_answer:
        if gemma_answer:
            selected_model = "gemma"
            selected_answer = gemma_answer
            selection_reason = "gemma_only_available"
        elif solar_answer:
            selected_model = "solar"
            selected_answer = solar_answer
            selection_reason = "solar_only_available"
        else:
            selected_model = "fallback"
            selected_answer = fallback_message
            selection_reason = "both_models_abnormal"

    return {
        "selected_model": selected_model,
        "selected_answer": selected_answer,
        "selection_reason": selection_reason,
        "solar_failed": solar_failed,
        "solar_fail_reasons": solar_fail_reasons,
        "solar_warning_reasons": solar_warning_reasons,
        "solar_answer_chars": len(solar_answer),
        "gemma_answer_chars": len(gemma_answer),
        "solar_meta": meta,
    }


