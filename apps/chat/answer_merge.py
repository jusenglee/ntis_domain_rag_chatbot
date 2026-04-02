"""Answer-selection policy shared by the dual-model generation path.

The selector is pure so fallback and timeout behavior can be tested without running the
complete LangGraph workflow.
"""

from __future__ import annotations

import re
from typing import Any

from apps.api.contracts.answer_groundedness import (
    BYPASS_ANSWER_KINDS,
    build_groundedness_snapshot_from_canonical_evidence as _build_groundedness_snapshot_from_canonical_evidence,
    evaluate_answer_groundedness,
)
from apps.api.contracts.answer_state_consistency import (
    AnswerStateConsistencyVerdict,
    evaluate_answer_state_consistency,
)


_REFUSAL_CONTEXT_MARKERS = (
    "제공된 정보",
    "주어진 정보",
)
_REFUSAL_PHRASES = (
    "찾을 수 없",
    "확인할 수 없",
    "안내가 어렵",
    "포함되어 있지 않습니다",
    "명시되어 있지 않습니다",
    "안내할 수 없습니다",
    "요청하신 내용을 안내하기 어렵습니다",
)
_INTERNAL_CONTEXT_MARKERS = (
    "[detail_evidence]",
    "entity_kind:",
    "requested_fields:",
    "missing_fields:",
    "answer_rule:",
    "질문을 입력해주세요.",
)
_HTML_LEAK_MARKERS = (
    "<span",
    "</span",
    "<div",
    "</div",
    "<a ",
    "</a",
    "tooltip-wrap",
    "tooltip-target",
    "data-ref-index",
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
_INTERNAL_SCHEMA_LABEL_PATTERN = re.compile(
    r"\((?:pjt_id|pjt_no|rst_id|person_no|org_id|org_code|biz_no|doi|issn|lead_org|participant_org|researchers|summary|goal|period|budget|outputs|perf_type|affiliation)\)"
)
_RAW_INTERNAL_UNAVAILABLE_PHRASES = (
    "필드는 제공된 정보",
    "필드는 제공된 자료",
    "필드는 확인할 수 없",
    "필드가 제공되지",
    "field is not provided",
    "fields are not provided",
    "fields were not provided",
    "상세 정보 미제공",
)
_BYPASS_ANSWER_KINDS = BYPASS_ANSWER_KINDS
_STATE_CONSISTENCY_SEVERITY = {
    "unsupported_item_identity": 6,
    "unsupported_order": 5,
    "unsupported_count": 4,
    "no_structured_list": 3,
    "insufficient_snapshot": 2,
    "not_applicable": 1,
    "supported": 0,
}


def build_groundedness_snapshot_from_canonical_evidence(
    *,
    canonical_evidence: list[dict[str, Any]],
    visible_count: Any = None,
) -> dict[str, Any]:
    return _build_groundedness_snapshot_from_canonical_evidence(
        canonical_evidence=canonical_evidence,
        visible_count=visible_count,
    ).model_dump()


def _evaluate_groundedness(
    *,
    answer_text: str,
    answer_kind: str,
    evidence_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    return evaluate_answer_groundedness(
        answer_text=answer_text,
        answer_kind=answer_kind,
        evidence_snapshot=evidence_snapshot,
    ).model_dump()


def _evaluate_state_consistency(
    *,
    answer_text: str,
    answer_kind: str,
    state_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    return evaluate_answer_state_consistency(
        answer_text=answer_text,
        answer_kind=answer_kind,
        state_snapshot=state_snapshot,
    ).model_dump()


def _is_state_supported(verdict: dict[str, Any] | None) -> bool:
    return str((verdict or {}).get("status") or "").strip().lower() == "supported"


def _pick_stricter_state_consistency(
    *verdicts: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = [dict(verdict or {}) for verdict in verdicts if isinstance(verdict, dict)]
    if not normalized:
        return AnswerStateConsistencyVerdict(status="not_applicable").model_dump()
    return max(
        normalized,
        key=lambda verdict: _STATE_CONSISTENCY_SEVERITY.get(
            str(verdict.get("status") or "").strip().lower(),
            0,
        ),
    )


def _looks_like_context_refusal(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).lower()
    if not normalized:
        return False
    has_context_marker = any(marker in normalized for marker in _REFUSAL_CONTEXT_MARKERS)
    has_refusal_phrase = any(phrase in normalized for phrase in _REFUSAL_PHRASES)
    return (has_context_marker and has_refusal_phrase) or ("주어진 정보만으로" in normalized)


def _looks_like_internal_context_leak(text: str) -> bool:
    raw_text = str(text or "")
    normalized_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not normalized_lines:
        return False
    normalized_text = "\n".join(normalized_lines)
    normalized_lower = normalized_text.lower()
    if re.search(r"\[\s*detail[\s_-]*evidence\s*\]", normalized_lower):
        return True
    if any(marker in normalized_lower for marker in _INTERNAL_CONTEXT_MARKERS):
        return True
    if any(marker in normalized_lower for marker in _HTML_LEAK_MARKERS):
        return True
    if _INTERNAL_SCHEMA_LABEL_PATTERN.search(normalized_lower):
        return True

    internal_field_mentions = {
        field
        for field in _INTERNAL_CONTEXT_FIELDS
        if re.search(rf"(?<![a-z0-9_]){re.escape(field)}(?![a-z0-9_])", normalized_lower)
    }
    if internal_field_mentions and any(
        marker in normalized_lower for marker in _RAW_INTERNAL_UNAVAILABLE_PHRASES
    ):
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
    evidence_snapshot: dict[str, Any] | None = None,
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

    bypass_like_answer = answer_kind in _BYPASS_ANSWER_KINDS
    groundedness = _evaluate_groundedness(
        answer_text=answer,
        answer_kind=answer_kind,
        evidence_snapshot=evidence_snapshot,
    )
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
        "groundedness": groundedness,
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
    evidence_snapshot: dict[str, Any] | None = None,
    protect_visible_order: bool = False,
    state_consistency_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select the final answer from Solar/Gemma outputs and streamed metadata."""
    solar_eval = _evaluate_model_answer(
        answer_text=answer_solar,
        answer_meta=solar_meta,
        fallback_message=fallback_message,
        min_answer_chars=min_answer_chars,
        evidence_snapshot=evidence_snapshot,
    )
    gemma_eval = _evaluate_model_answer(
        answer_text=answer_gemma,
        answer_meta=gemma_meta,
        fallback_message=fallback_message,
        min_answer_chars=min_answer_chars,
        evidence_snapshot=evidence_snapshot,
    )

    solar_answer = solar_eval["answer"]
    gemma_answer = gemma_eval["answer"]
    solar_failed = bool(solar_eval["failed"])
    gemma_failed = bool(gemma_eval["failed"])
    normalized_policy = str(policy or "solar_first").strip().lower() or "solar_first"
    solar_valid = bool(solar_answer) and not solar_failed
    gemma_valid = bool(gemma_answer) and not gemma_failed
    solar_visible_order_safe = solar_valid and str((solar_eval["groundedness"] or {}).get("status") or "").strip().lower() != "unsupported"
    gemma_visible_order_safe = gemma_valid and str((gemma_eval["groundedness"] or {}).get("status") or "").strip().lower() != "unsupported"
    if protect_visible_order:
        solar_state_consistency = _evaluate_state_consistency(
            answer_text=solar_answer,
            answer_kind=solar_eval["answer_kind"],
            state_snapshot=state_consistency_snapshot,
        )
        gemma_state_consistency = _evaluate_state_consistency(
            answer_text=gemma_answer,
            answer_kind=gemma_eval["answer_kind"],
            state_snapshot=state_consistency_snapshot,
        )
    else:
        solar_state_consistency = AnswerStateConsistencyVerdict(status="not_applicable").model_dump()
        gemma_state_consistency = AnswerStateConsistencyVerdict(status="not_applicable").model_dump()
    state_guard_applies = (
        bool(protect_visible_order)
        and bool((state_consistency_snapshot or {}).get("available"))
        and any(
            str((verdict or {}).get("status") or "").strip().lower() != "not_applicable"
            for verdict in (solar_state_consistency, gemma_state_consistency)
        )
    )
    solar_state_safe = solar_valid and _is_state_supported(solar_state_consistency)
    gemma_state_safe = gemma_valid and _is_state_supported(gemma_state_consistency)

    if state_guard_applies and solar_state_safe != gemma_state_safe:
        if solar_state_safe:
            selected_model = "solar"
            selected_answer = solar_answer
            selection_reason = "solar_state_consistent"
        else:
            selected_model = "gemma"
            selected_answer = gemma_answer
            selection_reason = "gemma_state_consistent"
    elif state_guard_applies and not solar_state_safe and not gemma_state_safe:
        selected_model = "fallback"
        selected_answer = fallback_message
        selection_reason = "both_models_state_inconsistent"
    elif protect_visible_order and not state_guard_applies and solar_visible_order_safe != gemma_visible_order_safe:
        if solar_visible_order_safe:
            selected_model = "solar"
            selected_answer = solar_answer
            selection_reason = "solar_visible_order_safe"
        else:
            selected_model = "gemma"
            selected_answer = gemma_answer
            selection_reason = "gemma_visible_order_safe"
    elif solar_valid and gemma_valid:
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
        "solar_groundedness": dict(solar_eval["groundedness"]),
        "gemma_groundedness": dict(gemma_eval["groundedness"]),
        "groundedness_snapshot": dict(evidence_snapshot or {}),
        "solar_state_consistency": dict(solar_state_consistency),
        "gemma_state_consistency": dict(gemma_state_consistency),
        "state_consistency_snapshot": dict(state_consistency_snapshot or {}),
        "selected_state_consistency": _pick_stricter_state_consistency(
            solar_state_consistency if selected_model == "solar" else None,
            gemma_state_consistency if selected_model == "gemma" else None,
            None if selected_model in {"solar", "gemma"} else solar_state_consistency,
            None if selected_model in {"solar", "gemma"} else gemma_state_consistency,
        ),
    }
