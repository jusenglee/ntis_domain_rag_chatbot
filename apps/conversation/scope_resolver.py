from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from apps.conversation.followup_anchor import (
    is_child_anchor_source,
    is_referential_followup,
    parse_ordinal_reference,
    parse_source_reference,
    resolve_followup_anchor,
)
from apps.conversation.view_state import (
    ActiveScope,
    ChildEntityRef,
    ConversationViewState,
    FocusEntity,
    get_active_child_anchor,
    get_active_focus_entity,
)

_RESET_CUES = (
    "전체 기준으로",
    "전체 기준",
    "이전 거 말고",
    "이전 거 무시",
    "방금 거 무시",
    "이전 조건 무시",
)
_REFINEMENT_HINTS = (
    "만",
    "것만",
    "논문만",
    "답변만",
    "보고서만",
    "최근",
    "상위",
)
_REFINEMENT_FIELDS = (
    "years",
    "year_from",
    "year_to",
    "people_terms",
    "org_terms",
    "lead_org_terms",
    "participant_org_terms",
    "people_affiliation_org_terms",
    "perf_types",
    "title",
    "keywords",
)
_CHILD_SUBJECT = {
    "people": "연구자",
    "org": "기관",
    "perf": "성과",
}


class ScopeDecision(BaseModel):
    followup_type: str = "fresh_search"
    resolved_anchor: Optional[FocusEntity] = None
    refinement_filters: Dict[str, Any] = Field(default_factory=dict)
    reset_requested: bool = False
    needs_clarification: bool = False
    clarification_payload: Optional[Dict[str, Any]] = None


def _has_reset_cue(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    return any(cue in text for cue in _RESET_CUES)


def _get_ids_map(normalized_intent: Any) -> Dict[str, Any]:
    ids_map = getattr(normalized_intent, "ids_map", None)
    if ids_map is None and isinstance(normalized_intent, dict):
        ids_map = normalized_intent.get("ids_map")
    return ids_map if isinstance(ids_map, dict) else {}


def _has_explicit_ids(normalized_intent: Any) -> bool:
    for values in _get_ids_map(normalized_intent).values():
        if isinstance(values, (list, tuple, set)):
            if any(str(value or "").strip() for value in values):
                return True
            continue
        if str(values or "").strip():
            return True
    return False


def _get_active_scope(view_state: Optional[ConversationViewState]) -> ActiveScope:
    if view_state is None:
        return ActiveScope()
    active_scope = getattr(view_state, "active_scope", None)
    if isinstance(active_scope, ActiveScope):
        return active_scope
    try:
        return ActiveScope.model_validate(active_scope or {})
    except Exception:
        return ActiveScope()


def _latest_snapshot(view_state: Optional[ConversationViewState]) -> Optional[Any]:
    return getattr(view_state, "visible_answer_manifest", None) if view_state is not None else None


def _scope_focus_entity(view_state: Optional[ConversationViewState]) -> Optional[FocusEntity]:
    return get_active_focus_entity(view_state)


def _active_anchor_entity(view_state: Optional[ConversationViewState]) -> Optional[FocusEntity]:
    return get_active_child_anchor(view_state)


def _get_subject_index(view_state: Optional[ConversationViewState]) -> Any:
    """view_state에서 subject_index를 추출한다. None이면 빈 dict를 반환한다."""
    if view_state is None:
        return {}
    return getattr(view_state, "subject_index", {}) or {}


def _extract_refinement_filters(normalized_intent: Any) -> Dict[str, Any]:
    filters: Dict[str, Any] = {}
    for field in _REFINEMENT_FIELDS:
        value = getattr(normalized_intent, field, None)
        if value is None and isinstance(normalized_intent, dict):
            value = normalized_intent.get(field)
        if isinstance(value, (list, tuple, set)):
            items = [str(item or "").strip() for item in value if str(item or "").strip()]
            if items:
                filters[field] = items
        else:
            text = str(value or "").strip()
            if text:
                filters[field] = text
    display_limit = getattr(normalized_intent, "planner_limit", None)
    if display_limit is None and isinstance(normalized_intent, dict):
        display_limit = normalized_intent.get("planner_limit")
    if display_limit not in (None, ""):
        filters["planner_limit"] = display_limit
    return filters


def _has_active_scope_target(view_state: Optional[ConversationViewState]) -> bool:
    active_scope = _get_active_scope(view_state)
    return (
        active_scope.result_set is not None
        or active_scope.focus is not None
        or active_scope.child_anchor is not None
    )


def _has_refinement_cue(question: str, normalized_intent: Any) -> bool:
    refinement_filters = _extract_refinement_filters(normalized_intent)
    if refinement_filters:
        return True

    text = str(question or "").strip()
    return bool(text and any(token in text for token in _REFINEMENT_HINTS))


def _has_independent_scope_axis(normalized_intent: Any) -> bool:
    for field in (
        "people_terms",
        "org_terms",
        "lead_org_terms",
        "participant_org_terms",
        "people_affiliation_org_terms",
        "perf_types",
        "title",
        "keywords",
    ):
        value = getattr(normalized_intent, field, None)
        if value is None and isinstance(normalized_intent, dict):
            value = normalized_intent.get(field)
        if isinstance(value, (list, tuple, set)):
            if any(str(item or "").strip() for item in value):
                return True
            continue
        if str(value or "").strip():
            return True
    return False


def _compact_child_ref(ref: ChildEntityRef) -> Dict[str, Any]:
    return {
        "title": str(getattr(ref, "display_name", "") or "").strip() or None,
        "context_kind": str(getattr(ref, "kind", "") or "").strip().lower() or None,
        "role": str(getattr(ref, "role", "") or "").strip() or None,
        "affiliation": str(getattr(ref, "affiliation", "") or "").strip() or None,
        "parent_relation": str(getattr(ref, "parent_relation", "") or "").strip() or None,
    }


def _inspect_named_child_reference(question: str, focus_entity: Optional[FocusEntity]) -> Dict[str, Any]:
    if focus_entity is None:
        return {"matched": False, "ambiguous": False, "missing_id": False, "candidates": []}
    if str(focus_entity.kind or "").strip().lower() != "project":
        return {"matched": False, "ambiguous": False, "missing_id": False, "candidates": []}

    text = str(question or "").strip()
    if not text:
        return {"matched": False, "ambiguous": False, "missing_id": False, "candidates": []}

    matched_refs: list[ChildEntityRef] = []
    matched_seed_refs: list[ChildEntityRef] = []
    for ref in list(getattr(focus_entity, "child_refs", []) or []):
        display_name = str(getattr(ref, "display_name", "") or "").strip()
        if not display_name or display_name not in text:
            continue
        matched_refs.append(ref)
        ids_map = getattr(ref, "ids_map", {}) or {}
        if isinstance(ids_map, dict) and any(
            str(value or "").strip()
            for values in ids_map.values()
            for value in (values if isinstance(values, list) else [values])
        ):
            matched_seed_refs.append(ref)

    if not matched_refs:
        return {"matched": False, "ambiguous": False, "missing_id": False, "candidates": []}
    if len(matched_refs) > 1 or len(matched_seed_refs) > 1:
        return {
            "matched": True,
            "ambiguous": True,
            "missing_id": False,
            "candidates": [_compact_child_ref(ref) for ref in matched_refs[:5]],
        }
    if not matched_seed_refs:
        return {
            "matched": True,
            "ambiguous": False,
            "missing_id": True,
            "candidates": [_compact_child_ref(ref) for ref in matched_refs[:5]],
        }
    return {
        "matched": True,
        "ambiguous": False,
        "missing_id": False,
        "candidates": [_compact_child_ref(ref) for ref in matched_refs[:5]],
    }


def _build_clarification_payload(
    *,
    clarification_type: str,
    reason: str,
    message: str,
    latest_snapshot: Optional[Any],
    candidates: Optional[list[dict[str, Any]]] = None,
    focus_entity: Optional[FocusEntity] = None,
) -> Dict[str, Any]:
    return {
        "clarification_type": clarification_type,
        "reason": reason,
        "message": message,
        "candidates": list(candidates or []),
        "resume_token": {
            "clarification_type": clarification_type,
            "reason": reason,
            "display_view_id": getattr(latest_snapshot, "view_id", None),
            "focus_kind": getattr(focus_entity, "kind", None),
            "focus_key": getattr(focus_entity, "pjt_id", None)
            or getattr(focus_entity, "pjt_no", None)
            or getattr(focus_entity, "rst_id", None)
            or getattr(focus_entity, "person_no", None)
            or getattr(focus_entity, "org_id", None)
            or getattr(focus_entity, "org_code", None)
            or getattr(focus_entity, "biz_no", None),
        },
    }


def resolve_scope_decision(
    *,
    question: str,
    view_state: Optional[ConversationViewState],
    normalized_intent_base: Any,
    strategy_meta: Optional[Dict[str, Any]] = None,
) -> ScopeDecision:
    latest_snapshot = _latest_snapshot(view_state)
    scope_focus_entity = _scope_focus_entity(view_state)
    latest_anchor_entity = _active_anchor_entity(view_state)
    has_active_scope_target = _has_active_scope_target(view_state)
    subject_index = _get_subject_index(view_state)

    if _has_reset_cue(question):
        return ScopeDecision(followup_type="scope_reset", reset_requested=True)

    if _has_explicit_ids(normalized_intent_base):
        return ScopeDecision(followup_type="fresh_search")

    anchor = resolve_followup_anchor(
        question=question,
        normalized_intent=normalized_intent_base,
        display_snapshot=latest_snapshot,
        focus_entity=latest_anchor_entity,
        scope_focus_entity=scope_focus_entity,
        subject_index=subject_index,
    )
    if anchor is not None:
        # subject_index í´ì ê²°ê³¼ê° ambiguousì¸ ê²½ì° clarificationì¼ë¡ ì²ë¦¬íë¤.
        if str(getattr(anchor, "source", "") or "").strip() == "ambiguity_subject_index":
            return ScopeDecision(
                followup_type="ambiguous_followup",
                needs_clarification=True,
                clarification_payload=_build_clarification_payload(
                    clarification_type="subject_index_ambiguity",
                    reason="subject_index_ambiguity",
                    message='ì\x9d´ì\xa0\x84 ë\x8c\x80í\x99\x94ì\x97\x90ì\x84\x9c ì\x96¸ê¸\x89ë\x90\x9c ë\x8c\x80ì\x83\x81ì\x9d´ ì\x97¬ë\x9f¿ì\x9e\x85ë\x8b\x88ë\x8b¤. ì\x96´ë\x96¤ ë\x8c\x80ì\x83\x81ì\x9d\x84 ê°\x80ë¦¬í\x82¤ë\x8a\x94ì§\x80 ë\x8b¤ì\x8b\x9c ì§\x80ì\xa0\x95í\x95´ ì£¼ì\x84¸ì\x9a\x94.',
                    latest_snapshot=latest_snapshot,
                    candidates=[],
                    focus_entity=scope_focus_entity,
                ),
            )
        followup_type = "child_entity_followup" if is_child_anchor_source(getattr(anchor, "source", None)) else "reference_followup"
        return ScopeDecision(
            followup_type=followup_type,
            resolved_anchor=anchor,
        )

    child_reference = _inspect_named_child_reference(question, scope_focus_entity)
    if child_reference["ambiguous"]:
        first_kind = None
        if child_reference["candidates"]:
            first_kind = str((child_reference["candidates"][0] or {}).get("context_kind") or "").strip().lower() or None
        subject = _CHILD_SUBJECT.get(first_kind or "people", "대상")
        return ScopeDecision(
            followup_type="ambiguous_followup",
            needs_clarification=True,
            clarification_payload=_build_clarification_payload(
                clarification_type="child_entity_ambiguity",
                reason="child_entity_ambiguity",
                message=f"현재 상세 안에서 어떤 {subject}를 가리키는지 다시 지정해 주세요.",
                latest_snapshot=latest_snapshot,
                candidates=child_reference["candidates"],
                focus_entity=scope_focus_entity,
            ),
        )
    if child_reference["missing_id"]:
        first_kind = None
        if child_reference["candidates"]:
            first_kind = str((child_reference["candidates"][0] or {}).get("context_kind") or "").strip().lower() or None
        subject = _CHILD_SUBJECT.get(first_kind or "people", "대상")
        return ScopeDecision(
            followup_type="ambiguous_followup",
            needs_clarification=True,
            clarification_payload=_build_clarification_payload(
                clarification_type="child_entity_missing_id",
                reason="child_entity_missing_id",
                message=f"현재 상세 안에서 언급된 {subject}는 찾았지만, 정확한 식별자를 확인할 수 없습니다. 다른 기준으로 다시 지정해 주세요.",
                latest_snapshot=latest_snapshot,
                candidates=child_reference["candidates"],
                focus_entity=scope_focus_entity,
            ),
        )

    if _has_refinement_cue(question, normalized_intent_base):
        refinement_filters = _extract_refinement_filters(normalized_intent_base)
        if not has_active_scope_target and not _has_independent_scope_axis(normalized_intent_base):
            return ScopeDecision(
                followup_type="ambiguous_followup",
                needs_clarification=True,
                clarification_payload=_build_clarification_payload(
                    clarification_type="refinement_target_missing",
                    reason="refinement_target_missing",
                    message="무엇을 기준으로 좁힐지 먼저 목록이나 상세 대상을 정해 주세요.",
                    latest_snapshot=latest_snapshot,
                    candidates=[],
                    focus_entity=scope_focus_entity,
                ),
            )
        if has_active_scope_target:
            return ScopeDecision(
                followup_type="refinement_followup",
                refinement_filters=refinement_filters,
            )

    has_reference_cue = bool(
        parse_source_reference(question) is not None
        or parse_ordinal_reference(question) is not None
        or is_referential_followup(question)
    )
    if has_reference_cue:
        if latest_snapshot is None and scope_focus_entity is None:
            return ScopeDecision(
                followup_type="ambiguous_followup",
                needs_clarification=True,
                clarification_payload=_build_clarification_payload(
                    clarification_type="reference_ambiguity",
                    reason="reference_missing_context",
                    message="이전 결과 목록이나 상세 맥락이 없어 무엇을 가리키는지 판단하기 어렵습니다. 먼저 목록을 확인해 주세요.",
                    latest_snapshot=latest_snapshot,
                    candidates=[],
                    focus_entity=scope_focus_entity,
                ),
            )
        return ScopeDecision(
            followup_type="ambiguous_followup",
            needs_clarification=True,
            clarification_payload=_build_clarification_payload(
                clarification_type="reference_ambiguity",
                reason="unresolved_reference",
                message="이전 결과 중 어떤 항목을 뜻하는지 다시 지정해 주세요.",
                latest_snapshot=latest_snapshot,
                candidates=[],
                focus_entity=scope_focus_entity,
            ),
        )

    return ScopeDecision(followup_type="fresh_search")
