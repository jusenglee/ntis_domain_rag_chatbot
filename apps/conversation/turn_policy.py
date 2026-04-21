from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from apps.conversation.turn_interpreter import (
    TurnCandidate,
    TurnInterpretationResult,
    build_clarification_payload,
)
from apps.conversation.turn_trigger import TurnTriggerResult
from apps.conversation.view_state import (
    ConversationViewState,
    get_active_child_anchor,
    get_active_focus_entity,
)


ExecutionPath = Literal["reuse_manifest", "reuse_anchor", "fresh_retrieval", "clarification"]
FollowupRights = Literal["ordinal_allowed", "source_allowed", "none"]

_INTERPRETATION_CONFIDENCE_THRESHOLD = 0.35


class TurnPolicyResult(BaseModel):
    execution_path: ExecutionPath = "fresh_retrieval"
    followup_type_hint: str = "fresh_search"
    followup_rights: FollowupRights = "none"
    allow_manifest_reuse: bool = False
    skip_followup_resolution: bool = True
    blocked_reason: Optional[str] = None
    clarification_payload: Dict[str, Any] = Field(default_factory=dict)
    selected_candidate_ids: list[str] = Field(default_factory=list)
    policy_source: str = "validator"


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


def _clarification_result(
    *,
    reason: str,
    trigger: TurnTriggerResult,
    interpretation: Optional[TurnInterpretationResult],
    candidates: list[TurnCandidate],
) -> TurnPolicyResult:
    return TurnPolicyResult(
        execution_path="clarification",
        followup_type_hint="ambiguous_followup",
        skip_followup_resolution=True,
        blocked_reason=reason,
        clarification_payload=build_clarification_payload(
            question=interpretation.rewritten_user_intent or "",
            candidates=candidates,
            interpretation=interpretation,
            blocked_reason=reason,
        ),
        selected_candidate_ids=list((interpretation.selected_candidate_ids if interpretation else []) or []),
        policy_source="validator_block",
    )


def resolve_turn_policy(
    *,
    turn_trigger: TurnTriggerResult | Dict[str, Any],
    interpretation: Optional[TurnInterpretationResult],
    view_state: Optional[ConversationViewState],
    has_explicit_seed: bool,
    explicit_seed_kind: Optional[str] = None,
    candidates: Optional[list[TurnCandidate]] = None,
) -> TurnPolicyResult:
    trigger = (
        turn_trigger
        if isinstance(turn_trigger, TurnTriggerResult)
        else TurnTriggerResult.model_validate(turn_trigger or {})
    )
    interpretation_model = (
        interpretation
        if interpretation is None or isinstance(interpretation, TurnInterpretationResult)
        else TurnInterpretationResult.model_validate(interpretation or {})
    )
    candidate_list = list(candidates or [])
    candidate_map = {candidate.candidate_id: candidate for candidate in candidate_list}

    last_query_contract = dict(getattr(view_state, "last_query_contract", {}) or {}) if view_state is not None else {}
    previous_publishability = _normalized_text(last_query_contract.get("answer_publishability")) or "not_applicable"
    previous_followup_rights = _normalized_text(last_query_contract.get("followup_rights")) or "none"
    has_manifest = _has_manifest(view_state)
    has_anchor_context = _has_anchor_context(view_state)

    if has_explicit_seed:
        blocked_reason = "explicit_named_subject_seed_bypass" if explicit_seed_kind == "named_subject" else "explicit_seed_bypass"
        return TurnPolicyResult(
            execution_path="fresh_retrieval",
            followup_type_hint="fresh_search",
            skip_followup_resolution=True,
            blocked_reason=blocked_reason,
            policy_source="explicit_seed",
        )

    if trigger.reference_style == "refinement" or (
        interpretation_model is not None
        and str(interpretation_model.reason or "").strip().lower() == "subject_refresh"
    ):
        return TurnPolicyResult(
            execution_path="fresh_retrieval",
            followup_type_hint="subject_refinement",
            skip_followup_resolution=True,
            blocked_reason="subject_refresh",
            selected_candidate_ids=list((interpretation_model.selected_candidate_ids if interpretation_model else []) or []),
            policy_source="subject_refresh",
        )

    if trigger.turn_intent == "fresh":
        return TurnPolicyResult(
            execution_path="fresh_retrieval",
            followup_type_hint="fresh_search",
            skip_followup_resolution=True,
            blocked_reason="trigger_fresh",
            policy_source="trigger",
        )

    if interpretation_model is None:
        return TurnPolicyResult(
            execution_path="clarification",
            followup_type_hint="ambiguous_followup",
            skip_followup_resolution=True,
            blocked_reason="missing_interpretation",
            clarification_payload=build_clarification_payload(
                question="",
                candidates=candidate_list,
                interpretation=None,
                blocked_reason="missing_interpretation",
            ),
            policy_source="validator_block",
        )

    selected_candidate_ids = list(interpretation_model.selected_candidate_ids or [])
    selected_candidates = [candidate_map[candidate_id] for candidate_id in selected_candidate_ids if candidate_id in candidate_map]
    if len(selected_candidates) != len(selected_candidate_ids):
        return _clarification_result(
            reason="unknown_candidate_id",
            trigger=trigger,
            interpretation=interpretation_model,
            candidates=candidate_list,
        )

    if (
        interpretation_model.target_entity_kind
        and selected_candidates
        and any(candidate.entity_kind != interpretation_model.target_entity_kind for candidate in selected_candidates)
    ):
        return _clarification_result(
            reason="entity_kind_conflict",
            trigger=trigger,
            interpretation=interpretation_model,
            candidates=candidate_list,
        )

    if (
        float(interpretation_model.confidence or 0.0) < _INTERPRETATION_CONFIDENCE_THRESHOLD
        and interpretation_model.chosen_action != "clarification"
    ):
        return _clarification_result(
            reason="low_confidence_interpretation",
            trigger=trigger,
            interpretation=interpretation_model,
            candidates=candidate_list,
        )

    if interpretation_model.chosen_action == "fresh_retrieval":
        return TurnPolicyResult(
            execution_path="fresh_retrieval",
            followup_type_hint="fresh_search",
            skip_followup_resolution=True,
            blocked_reason=interpretation_model.reason,
            selected_candidate_ids=selected_candidate_ids,
            policy_source="validated_interpretation",
        )

    if interpretation_model.chosen_action == "clarification":
        return _clarification_result(
            reason=interpretation_model.ambiguity_reason or interpretation_model.reason or "ambiguous_followup",
            trigger=trigger,
            interpretation=interpretation_model,
            candidates=candidate_list,
        )

    if trigger.reference_style in {"ordinal", "source_reference"}:
        if previous_publishability != "publishable":
            return _clarification_result(
                reason="non_publishable_previous_turn",
                trigger=trigger,
                interpretation=interpretation_model,
                candidates=candidate_list,
            )
        if not has_manifest:
            return _clarification_result(
                reason="missing_previous_manifest",
                trigger=trigger,
                interpretation=interpretation_model,
                candidates=candidate_list,
            )
        if previous_followup_rights == "none":
            return _clarification_result(
                reason="followup_rights_blocked",
                trigger=trigger,
                interpretation=interpretation_model,
                candidates=candidate_list,
            )
        if selected_candidates and any(candidate.source != "manifest_item" for candidate in selected_candidates):
            return _clarification_result(
                reason="reference_style_conflict",
                trigger=trigger,
                interpretation=interpretation_model,
                candidates=candidate_list,
            )
        return TurnPolicyResult(
            execution_path="reuse_manifest",
            followup_type_hint="reference_followup",
            followup_rights="source_allowed" if previous_followup_rights == "source_allowed" else "ordinal_allowed",
            allow_manifest_reuse=True,
            skip_followup_resolution=False,
            selected_candidate_ids=selected_candidate_ids,
            policy_source="validated_interpretation",
        )

    if interpretation_model.chosen_action == "reuse_manifest":
        if previous_publishability != "publishable":
            return _clarification_result(
                reason="non_publishable_previous_turn",
                trigger=trigger,
                interpretation=interpretation_model,
                candidates=candidate_list,
            )
        if not has_manifest:
            return _clarification_result(
                reason="missing_previous_manifest",
                trigger=trigger,
                interpretation=interpretation_model,
                candidates=candidate_list,
            )
        if selected_candidates and any(candidate.source != "manifest_item" for candidate in selected_candidates):
            return _clarification_result(
                reason="manifest_candidate_required",
                trigger=trigger,
                interpretation=interpretation_model,
                candidates=candidate_list,
            )
        return TurnPolicyResult(
            execution_path="reuse_manifest",
            followup_type_hint="reference_followup",
            followup_rights="source_allowed" if previous_followup_rights == "source_allowed" else "ordinal_allowed",
            allow_manifest_reuse=True,
            skip_followup_resolution=False,
            selected_candidate_ids=selected_candidate_ids,
            policy_source="validated_interpretation",
        )

    if interpretation_model.chosen_action == "reuse_anchor":
        if selected_candidates and any(candidate.source == "manifest_item" for candidate in selected_candidates):
            return _clarification_result(
                reason="anchor_candidate_required",
                trigger=trigger,
                interpretation=interpretation_model,
                candidates=candidate_list,
            )
        if not selected_candidates and not has_anchor_context:
            return _clarification_result(
                reason="missing_anchor_context",
                trigger=trigger,
                interpretation=interpretation_model,
                candidates=candidate_list,
            )
        return TurnPolicyResult(
            execution_path="reuse_anchor",
            followup_type_hint="reference_followup",
            skip_followup_resolution=False,
            blocked_reason="manifest_not_required",
            selected_candidate_ids=selected_candidate_ids,
            policy_source="validated_interpretation",
        )

    return TurnPolicyResult(
        execution_path="fresh_retrieval",
        followup_type_hint="fresh_search",
        skip_followup_resolution=True,
        blocked_reason="default_fresh",
        selected_candidate_ids=selected_candidate_ids,
        policy_source="validator",
    )
