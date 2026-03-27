"""Answer-selection policy shared by the dual-model generation path.

The selector is pure so fallback and timeout behavior can be tested without running the
complete LangGraph workflow.
"""

from __future__ import annotations

from typing import Any


_REFUSAL_CONTEXT_MARKERS = (
    "\uc81c\uacf5\ub41c \uc815\ubcf4",
    "\uc8fc\uc5b4\uc9c4 \uc815\ubcf4",
)
_REFUSAL_PHRASES = (
    "\ucc3e\uc744 \uc218 \uc5c6",
    "\ud655\uc778\ud560 \uc218 \uc5c6",
    "\uc548\ub0b4\uac00 \uc5b4\ub835",
    "\ud3ec\ud568\ub418\uc5b4 \uc788\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4",
    "\uba85\uc2dc\ub418\uc5b4 \uc788\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4",
    "\uc548\ub0b4\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4",
    "\uc694\uccad\ud558\uc2e0 \ub0b4\uc6a9\uc744 \uc548\ub0b4\ud558\uae30 \uc5b4\ub835\uc2b5\ub2c8\ub2e4",
)


def _looks_like_context_refusal(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).lower()
    if not normalized:
        return False
    has_context_marker = any(marker in normalized for marker in _REFUSAL_CONTEXT_MARKERS)
    has_refusal_phrase = any(phrase in normalized for phrase in _REFUSAL_PHRASES)
    return (has_context_marker and has_refusal_phrase) or ("\uc8fc\uc5b4\uc9c4 \uc815\ubcf4\ub9cc\uc73c\ub85c" in normalized)


def select_final_answer(
    *,
    answer_solar: str,
    answer_gemma: str,
    solar_meta: dict[str, Any] | None,
    policy: str,
    fallback_message: str,
    min_answer_chars: int,
) -> dict[str, Any]:
    """Select the final answer from Solar/Gemma outputs and streamed metadata."""
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
