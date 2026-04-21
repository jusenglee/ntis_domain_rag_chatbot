from __future__ import annotations

import json
import re
from typing import Any, Dict, Literal, Optional

from apps.platform.langchain_compat import ChatPromptTemplate, PydanticOutputParser, SystemMessage
from pydantic import BaseModel, Field

from apps.api.runtime_helpers import log_event
from apps.chat.llm_json import sanitize_llm_json
from apps.chat.llm_runtime import build_llm, load_prompt_file
from apps.conversation.followup_anchor import (
    is_referential_followup,
    parse_ordinal_reference,
    parse_relative_reference,
    parse_source_reference,
)
from apps.conversation.view_state import ConversationViewState, get_recent_mentions
from apps.planner.planner_defaults import PLANNER_DISABLE_THINKING, PLANNER_TEMPERATURE
from apps.planner.prompt_asset_paths import planner_prompt_path


TurnIntent = Literal["fresh", "followup", "ambiguous"]
ReferenceStyle = Literal["ordinal", "source_reference", "deictic", "named_subject", "refinement", "none"]

_YEAR_OR_RANGE_RE = re.compile(r"(?:19|20)\d{2}(?:\s*[~\-]\s*(?:19|20)\d{2})?\s*년?도?")
_SUBJECT_REFINEMENT_CUES = (
    "활동",
    "활동내역",
    "활동 내역",
    "활동이력",
    "활동 이력",
    "참여이력",
    "이력",
    "논문만",
    "성과만",
    "특허만",
    "보고서만",
    "최근",
    "연도",
    "년도",
)


class TurnTriggerResult(BaseModel):
    turn_intent: TurnIntent = "fresh"
    reference_style: ReferenceStyle = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = "heuristic_default"


def _view_state_summary(view_state: Optional[ConversationViewState]) -> Dict[str, Any]:
    if view_state is None:
        return {
            "has_visible_answer_manifest": False,
            "manifest_visible_count": 0,
            "has_active_focus": False,
            "has_child_anchor": False,
            "subject_index_count": 0,
            "recent_mention_count": 0,
            "last_turn_kind": None,
            "last_answer_publishability": None,
            "last_followup_rights": None,
        }
    snapshot = getattr(view_state, "visible_answer_manifest", None)
    active_scope = getattr(view_state, "active_scope", None)
    last_query_contract = dict(getattr(view_state, "last_query_contract", {}) or {})
    subject_index = getattr(view_state, "subject_index", {}) or {}
    recent_mentions = get_recent_mentions(view_state)
    return {
        "has_visible_answer_manifest": snapshot is not None,
        "manifest_visible_count": int(getattr(snapshot, "visible_count", 0) or 0),
        "has_active_focus": getattr(active_scope, "focus", None) is not None,
        "has_child_anchor": getattr(active_scope, "child_anchor", None) is not None,
        "subject_index_count": len(subject_index),
        "recent_mention_count": len(recent_mentions),
        "last_turn_kind": str(last_query_contract.get("turn_kind") or "").strip().lower() or None,
        "last_answer_publishability": str(last_query_contract.get("answer_publishability") or "").strip().lower() or None,
        "last_followup_rights": str(last_query_contract.get("followup_rights") or "").strip().lower() or None,
    }


def _has_previous_state(summary: Dict[str, Any]) -> bool:
    return any(
        [
            bool(summary.get("has_visible_answer_manifest")),
            bool(summary.get("has_active_focus")),
            bool(summary.get("has_child_anchor")),
            int(summary.get("subject_index_count") or 0) > 0,
            int(summary.get("recent_mention_count") or 0) > 0,
        ]
    )


def _has_subject_refinement_cue(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    if parse_source_reference(text) is not None or parse_ordinal_reference(text) is not None:
        return False
    if parse_relative_reference(text) is not None or is_referential_followup(text):
        return False
    return bool(_YEAR_OR_RANGE_RE.search(text) or any(cue in text for cue in _SUBJECT_REFINEMENT_CUES))


def _heuristic_turn_trigger(
    *,
    question: str,
    has_explicit_seed: bool,
    explicit_seed_kind: Optional[str] = None,
    summary: Dict[str, Any],
) -> TurnTriggerResult:
    if has_explicit_seed:
        reason = "explicit_named_subject_seed" if explicit_seed_kind == "named_subject" else "explicit_seed"
        return TurnTriggerResult(
            turn_intent="fresh",
            reference_style="none",
            confidence=1.0,
            reason=reason,
        )

    if not _has_previous_state(summary):
        return TurnTriggerResult(
            turn_intent="fresh",
            reference_style="none",
            confidence=0.96,
            reason="no_previous_state",
        )

    if parse_source_reference(question) is not None:
        return TurnTriggerResult(
            turn_intent="followup",
            reference_style="source_reference",
            confidence=0.98,
            reason="source_reference_token",
        )

    if parse_ordinal_reference(question) is not None or parse_relative_reference(question) is not None:
        return TurnTriggerResult(
            turn_intent="followup",
            reference_style="ordinal",
            confidence=0.96,
            reason="ordinal_token",
        )

    if is_referential_followup(question):
        return TurnTriggerResult(
            turn_intent="ambiguous",
            reference_style="deictic",
            confidence=0.62,
            reason="referential_cue",
        )

    if _has_subject_refinement_cue(question):
        return TurnTriggerResult(
            turn_intent="followup",
            reference_style="refinement",
            confidence=0.9,
            reason="subject_refinement",
        )

    return TurnTriggerResult(
        turn_intent="fresh",
        reference_style="none",
        confidence=0.7,
        reason="default_fresh",
    )


async def run_turn_trigger(
    *,
    question: str,
    view_state: Optional[ConversationViewState],
    has_explicit_seed: bool,
    explicit_seed_kind: Optional[str] = None,
    request_id: Optional[str],
    conversation_id: str,
) -> TurnTriggerResult:
    summary = _view_state_summary(view_state)
    heuristic = _heuristic_turn_trigger(
        question=question,
        has_explicit_seed=has_explicit_seed,
        explicit_seed_kind=explicit_seed_kind,
        summary=summary,
    )

    if has_explicit_seed or not _has_previous_state(summary) or heuristic.reference_style == "refinement":
        log_event(
            "TURN.TRIGGER",
            request_id=request_id,
            conversation_id=conversation_id,
            source="heuristic",
            turn_intent=heuristic.turn_intent,
            reference_style=heuristic.reference_style,
            confidence=round(float(heuristic.confidence), 3),
            reason=heuristic.reason,
        )
        return heuristic

    try:
        llm = build_llm(model_name="solar_vllm_0")
        parser = PydanticOutputParser(pydantic_object=TurnTriggerResult)
        system_prompt = await load_prompt_file(planner_prompt_path("turn_trigger_v1.md"))
        prompt = ChatPromptTemplate.from_messages(
            [
                SystemMessage(content=system_prompt),
                (
                    "human",
                    "{format_instructions}\n<user_query>{question}</user_query>\n<view_state_summary>{view_state_summary}</view_state_summary>\n<heuristic_hint>{heuristic_hint}</heuristic_hint>",
                ),
            ]
        )
        trigger_llm = llm.bind(
            reasoning_effort="low",
            include_reasoning=False,
            disable_thinking=PLANNER_DISABLE_THINKING,
            temperature=PLANNER_TEMPERATURE,
            top_p=1.0,
            max_tokens=120,
        )
        chain = prompt | trigger_llm | sanitize_llm_json | parser
        result = await chain.ainvoke(
            {
                "format_instructions": parser.get_format_instructions(),
                "question": question,
                "view_state_summary": json.dumps(summary, ensure_ascii=False),
                "heuristic_hint": json.dumps(heuristic.model_dump(), ensure_ascii=False),
            }
        )

        if float(result.confidence or 0.0) < 0.35:
            fallback = heuristic.model_copy(update={"reason": "low_confidence_fallback"})
            log_event(
                "TURN.TRIGGER",
                request_id=request_id,
                conversation_id=conversation_id,
                source="heuristic_fallback",
                turn_intent=fallback.turn_intent,
                reference_style=fallback.reference_style,
                confidence=round(float(fallback.confidence), 3),
                reason=fallback.reason,
            )
            return fallback

        log_event(
            "TURN.TRIGGER",
            request_id=request_id,
            conversation_id=conversation_id,
            source="llm",
            turn_intent=result.turn_intent,
            reference_style=result.reference_style,
            confidence=round(float(result.confidence), 3),
            reason=result.reason,
        )
        return result
    except Exception as exc:
        fallback = heuristic.model_copy(update={"reason": f"llm_error_fallback:{type(exc).__name__}"})
        log_event(
            "TURN.TRIGGER",
            request_id=request_id,
            conversation_id=conversation_id,
            source="heuristic_fallback",
            turn_intent=fallback.turn_intent,
            reference_style=fallback.reference_style,
            confidence=round(float(fallback.confidence), 3),
            reason=fallback.reason,
        )
        return fallback
