from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from apps.conversation.turn_trigger import TurnTriggerResult
from apps.conversation.view_state import (
    ConversationViewState,
    get_active_child_anchor,
    get_active_focus_entity,
)


ExecutionPath = Literal["reuse_manifest", "reuse_anchor", "fresh_retrieval", "clarification"]
FollowupRights = Literal["ordinal_allowed", "source_allowed", "none"]


class TurnPolicyResult(BaseModel):
    execution_path: ExecutionPath = "fresh_retrieval"
    followup_type_hint: str = "fresh_search"
    followup_rights: FollowupRights = "none"
    allow_manifest_reuse: bool = False
    skip_followup_resolution: bool = True
    blocked_reason: Optional[str] = None
    clarification_payload: Dict[str, Any] = Field(default_factory=dict)


def _normalized_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _has_anchor_context(view_state: Optional[ConversationViewState]) -> bool:
    if view_state is None:
        return False
    if get_active_child_anchor(view_state) is not None or get_active_focus_entity(view_state) is not None:
        return True
    return bool(getattr(view_state, "subject_index", {}) or {})


def _has_manifest(view_state: Optional[ConversationViewState]) -> bool:
    return view_state is not None and getattr(view_state, "visible_answer_manifest", None) is not None


def _policy_clarification_payload(*, reason: str) -> Dict[str, Any]:
    message = (
        "직전 답변은 번호나 출처 참조를 바로 해석할 수 있는 확정 목록이 아닙니다. 제목이나 대상명을 다시 지정해 주세요."
        if reason == "non_publishable_previous_turn"
        else "이전 대화의 무엇을 가리키는지 분명하지 않습니다. 번호나 제목, 대상명을 다시 지정해 주세요."
    )
    return {
        "clarification_type": "followup_policy_block",
        "reason": reason,
        "message": message,
    }


def resolve_turn_policy(
    *,
    turn_trigger: TurnTriggerResult | Dict[str, Any],
    view_state: Optional[ConversationViewState],
    has_explicit_seed: bool,
) -> TurnPolicyResult:
    trigger = (
        turn_trigger
        if isinstance(turn_trigger, TurnTriggerResult)
        else TurnTriggerResult.model_validate(turn_trigger or {})
    )
    last_query_contract = dict(getattr(view_state, "last_query_contract", {}) or {}) if view_state is not None else {}
    previous_publishability = _normalized_text(last_query_contract.get("answer_publishability")) or "not_applicable"
    previous_followup_rights = _normalized_text(last_query_contract.get("followup_rights")) or "none"
    has_manifest = _has_manifest(view_state)
    has_anchor_context = _has_anchor_context(view_state)

    if has_explicit_seed:
        return TurnPolicyResult(
            execution_path="fresh_retrieval",
            followup_type_hint="fresh_search",
            skip_followup_resolution=True,
            blocked_reason="explicit_seed_bypass",
        )

    if trigger.turn_intent == "fresh":
        return TurnPolicyResult(
            execution_path="fresh_retrieval",
            followup_type_hint="fresh_search",
            skip_followup_resolution=True,
            blocked_reason="trigger_fresh",
        )

    if trigger.turn_intent == "ambiguous":
        if has_manifest or has_anchor_context:
            return TurnPolicyResult(
                execution_path="clarification",
                followup_type_hint="ambiguous_followup",
                blocked_reason="ambiguous_followup",
                clarification_payload=_policy_clarification_payload(reason="ambiguous_followup"),
            )
        return TurnPolicyResult(
            execution_path="fresh_retrieval",
            followup_type_hint="fresh_search",
            skip_followup_resolution=True,
            blocked_reason="ambiguous_without_context",
        )

    if trigger.reference_style in {"ordinal", "source_reference"}:
        if previous_publishability == "publishable" and has_manifest and previous_followup_rights != "none":
            return TurnPolicyResult(
                execution_path="reuse_manifest",
                followup_type_hint="reference_followup",
                followup_rights=(
                    "source_allowed"
                    if previous_followup_rights == "source_allowed"
                    else "ordinal_allowed"
                ),
                allow_manifest_reuse=True,
                skip_followup_resolution=False,
            )
        return TurnPolicyResult(
            execution_path="clarification",
            followup_type_hint="ambiguous_followup",
            blocked_reason=(
                "non_publishable_previous_turn"
                if previous_publishability != "publishable"
                else "missing_previous_manifest"
            ),
            clarification_payload=_policy_clarification_payload(
                reason=(
                    "non_publishable_previous_turn"
                    if previous_publishability != "publishable"
                    else "missing_previous_manifest"
                )
            ),
        )

    if trigger.reference_style in {"named_subject", "deictic", "refinement"}:
        if has_anchor_context:
            return TurnPolicyResult(
                execution_path="reuse_anchor",
                followup_type_hint="reference_followup",
                skip_followup_resolution=False,
                blocked_reason="manifest_not_required",
            )
        if previous_publishability == "publishable" and has_manifest and previous_followup_rights != "none":
            return TurnPolicyResult(
                execution_path="reuse_manifest",
                followup_type_hint="reference_followup",
                followup_rights=(
                    "source_allowed"
                    if previous_followup_rights == "source_allowed"
                    else "ordinal_allowed"
                ),
                allow_manifest_reuse=True,
                skip_followup_resolution=False,
            )
        return TurnPolicyResult(
            execution_path="clarification",
            followup_type_hint="ambiguous_followup",
            blocked_reason="missing_anchor_context",
            clarification_payload=_policy_clarification_payload(reason="missing_anchor_context"),
        )

    return TurnPolicyResult(
        execution_path="fresh_retrieval",
        followup_type_hint="fresh_search",
        skip_followup_resolution=True,
        blocked_reason="default_fresh",
    )
