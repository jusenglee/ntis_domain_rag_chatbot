"""Answer-selection policy shared by the dual-model generation path.

The selector is pure so fallback and timeout behavior can be tested without running the
complete LangGraph workflow.
"""

from __future__ import annotations

import re
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
_INTERNAL_CONTEXT_MARKERS = (
    "[detail_evidence]",
    "entity_kind:",
    "requested_fields:",
    "missing_fields:",
    "answer_rule:",
    "\uc9c8\ubb38\uc744 \uc785\ub825\ud574\uc8fc\uc138\uc694.",
)
_INTERNAL_CONTEXT_FIELDS = {
    "title",
    "pjt_id",
    "pjt_no",
    "rst_id",
    "person_no",
    "org_id",
    "org_code",
    "biz_no",
    "doi",
    "issn",
    "year",
    "lead_org",
    "participant_org",
    "researchers",
    "summary",
    "goal",
    "period",
    "budget",
    "outputs",
    "perf_type",
    "affiliation",
}


def _looks_like_context_refusal(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).lower()
    if not normalized:
        return False
    has_context_marker = any(marker in normalized for marker in _REFUSAL_CONTEXT_MARKERS)
    has_refusal_phrase = any(phrase in normalized for phrase in _REFUSAL_PHRASES)
    return (has_context_marker and has_refusal_phrase) or ("\uc8fc\uc5b4\uc9c4 \uc815\ubcf4\ub9cc\uc73c\ub85c" in normalized)


def _looks_like_internal_context_leak(text: str) -> bool:
    normalized_lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not normalized_lines:
        return False
    normalized_text = "\n".join(normalized_lines)
    normalized_lower = normalized_text.lower()
    if re.search(r"\[\s*detail[\s_-]*evidence\s*\]", normalized_lower):
        return True
    if any(marker in normalized_lower for marker in _INTERNAL_CONTEXT_MARKERS):
        return True

    structured_line_count = 0
    for line in normalized_lower.splitlines():
        matched = re.match(r"^([a-z_]+)\s*:\s+\S", line)
        if not matched:
            continue
        if matched.group(1) in _INTERNAL_CONTEXT_FIELDS:
            structured_line_count += 1
            if structured_line_count >= 3:
                return True
    return False


def _evaluate_model_answer(
    *,
    answer_text: str,
    answer_meta: dict[str, Any] | None,
    fallback_message: str,
    min_answer_chars: int,
) -> dict[str, Any]:
    answer = (answer_text or "").strip()
    meta = answer_meta or {}
    fail_reasons: list[str] = []
    warning_reasons: list[str] = []

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
            fail_reasons.append("deadline_without_stream_content")
        if ttft_any_ms is None:
            fail_reasons.append("deadline_stream_not_started_or_stalled")
        elif ttft_content_ms is None and content_chars == 0:
            fail_reasons.append("deadline_content_not_started")
        elif ttft_content_ms is None:
            warning_reasons.append("deadline_content_delayed")
        else:
            warning_reasons.append("deadline_with_partial_or_delayed_content")

    if bool(meta.get("char_limited")):
        fail_reasons.append("char_limited")

    marker = (fallback_message or "").strip()
    if marker and marker in answer:
        fail_reasons.append("contains_fallback_notice")

    bypass_like_answer = answer_kind in {"detail_cache", "detail_profile", "no_result", "clarification", "direct_answer", "error"}
    refusal_like_answer = (
        answer_kind in {"llm_streamed", "llm_collected"}
        and _looks_like_context_refusal(answer)
    )
    if refusal_like_answer:
        fail_reasons.append("refusal_like_answer")
    if _looks_like_internal_context_leak(answer):
        fail_reasons.append("internal_context_leak")
    if len(answer) < min_answer_chars and not bypass_like_answer:
        fail_reasons.append(f"too_short<{min_answer_chars}")

    return {
        "answer": answer,
        "meta": meta,
        "failed": bool(fail_reasons),
        "fail_reasons": fail_reasons,
        "warning_reasons": warning_reasons,
        "answer_kind": answer_kind,
        "answer_chars": len(answer),
    }


def select_final_answer(
    *,
    answer_solar: str,
    answer_gemma: str,
    solar_meta: dict[str, Any] | None,
    gemma_meta: dict[str, Any] | None = None,
    policy: str,
    fallback_message: str,
    min_answer_chars: int,
) -> dict[str, Any]:
    """Select the final answer from Solar/Gemma outputs and streamed metadata."""
    solar_eval = _evaluate_model_answer(
        answer_text=answer_solar,
        answer_meta=solar_meta,
        fallback_message=fallback_message,
        min_answer_chars=min_answer_chars,
    )
    gemma_eval = _evaluate_model_answer(
        answer_text=answer_gemma,
        answer_meta=gemma_meta,
        fallback_message=fallback_message,
        min_answer_chars=min_answer_chars,
    )

    solar_answer = solar_eval["answer"]
    gemma_answer = gemma_eval["answer"]
    solar_failed = bool(solar_eval["failed"])
    gemma_failed = bool(gemma_eval["failed"])
    normalized_policy = str(policy or "solar_first").strip().lower() or "solar_first"
    solar_valid = bool(solar_answer) and not solar_failed
    gemma_valid = bool(gemma_answer) and not gemma_failed

    if solar_valid and gemma_valid:
        if normalized_policy == "gemma_first":
            selected_model = "gemma"
            selected_answer = gemma_answer
            selection_reason = "policy_gemma_first"
        else:
            selected_model = "solar"
            selected_answer = solar_answer
            selection_reason = "policy_solar_first"
    elif gemma_valid:
        selected_model = "gemma"
        selected_answer = gemma_answer
        selection_reason = "gemma_only_valid"
    elif solar_valid:
        selected_model = "solar"
        selected_answer = solar_answer
        selection_reason = "solar_only_valid"
    else:
        selected_model = "fallback"
        selected_answer = fallback_message
        selection_reason = "both_models_invalid"

    if not selected_answer:
        if gemma_valid:
            selected_model = "gemma"
            selected_answer = gemma_answer
            selection_reason = "gemma_only_available"
        elif solar_valid:
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
        "solar_fail_reasons": list(solar_eval["fail_reasons"]),
        "solar_warning_reasons": list(solar_eval["warning_reasons"]),
        "solar_answer_chars": len(solar_answer),
        "gemma_answer_chars": len(gemma_answer),
        "solar_meta": solar_eval["meta"],
        "gemma_failed": gemma_failed,
        "gemma_fail_reasons": list(gemma_eval["fail_reasons"]),
        "gemma_warning_reasons": list(gemma_eval["warning_reasons"]),
        "gemma_meta": gemma_eval["meta"],
    }
