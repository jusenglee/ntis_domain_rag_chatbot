from __future__ import annotations



import re
from typing import Any, Dict, Optional



from pydantic import BaseModel, Field



from apps.conversation.clarification_prose import compose_clarification_message
from apps.conversation.followup_anchor import (

    focus_entity_from_mention,
    is_referential_followup,

    parse_ordinal_reference,

    parse_source_reference,
    resolve_from_recent_mentions,

    resolve_named_child_anchor_from_focus,

)

from apps.conversation.view_state import (

    ActiveScope,

    ChildEntityRef,

    ConversationViewState,

    FocusEntity,

    get_active_focus_entity,

    get_recent_mentions,

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


_RECENT_MENTION_KIND_HINTS = (
    ("과제", "project"),
    ("프로젝트", "project"),
    ("성과", "perf"),
    ("논문", "perf"),
    ("특허", "perf"),
    ("보고서", "perf"),
)
_DETAIL_HINTS = ("상세", "자세히", "자세한", "디테일")
_YEAR_PATTERN = re.compile(r"((?:19|20)\d{2})\s*년")


def _recent_mention_kind_hint(question: str) -> Optional[str]:
    text = str(question or "").strip()
    for token, kind in _RECENT_MENTION_KIND_HINTS:
        if token in text:
            return kind
    return None


def _extract_recent_mention_year(question: str, normalized_intent: Any) -> Optional[str]:
    years = getattr(normalized_intent, "years", None)
    if years is None and isinstance(normalized_intent, dict):
        years = normalized_intent.get("years")
    if isinstance(years, (list, tuple, set)):
        for raw_year in years:
            text = str(raw_year or "").strip()
            if text:
                return text
    text = str(years or "").strip()
    if text:
        return text
    match = _YEAR_PATTERN.search(str(question or "").strip())
    return match.group(1) if match else None


def _is_detail_project_question(question: str, normalized_intent: Any) -> bool:
    action = getattr(normalized_intent, "action", None)
    if action is None and isinstance(normalized_intent, dict):
        action = normalized_intent.get("action")
    if str(action or "").strip().lower() == "detail":
        return True
    text = str(question or "").strip()
    return bool(text and any(token in text for token in _DETAIL_HINTS))


def _recent_anchor_is_allowed(anchor: FocusEntity, *, question: str, normalized_intent: Any) -> bool:
    if str(getattr(anchor, "kind", "") or "").strip().lower() != "project":
        return True
    if not _is_detail_project_question(question, normalized_intent):
        return True
    if getattr(anchor, "pjt_id", None):
        return True
    return False


def _compact_recent_mention_candidate(mention: Any) -> Dict[str, Any]:
    return {
        "title": getattr(mention, "title_text", None),
        "entity_kind": getattr(mention, "entity_kind", None),
        "year": getattr(mention, "year", None),
        "lead_org": getattr(mention, "lead_org", None),
    }


def _resolve_recent_mention_fallback(
    *,
    question: str,
    normalized_intent: Any,
    recent_mentions: list[Any],
) -> tuple[Optional[FocusEntity], list[dict[str, Any]], Optional[str]]:
    if not recent_mentions:
        return None, [], None

    target_kind = _recent_mention_kind_hint(question)
    filtered_mentions = [
        mention for mention in recent_mentions
        if not target_kind or str(getattr(mention, "entity_kind", "") or "").strip().lower() == target_kind
    ]
    if not filtered_mentions:
        filtered_mentions = list(recent_mentions)

    target_year = _extract_recent_mention_year(question, normalized_intent)
    if target_year:
        project_mentions = [
            mention for mention in filtered_mentions
            if str(getattr(mention, "entity_kind", "") or "").strip().lower() == "project"
        ]
        if project_mentions:
            latest_group_key = next(
                (
                    str(getattr(mention, "pjt_no", "") or "").strip()
                    for mention in reversed(project_mentions)
                    if str(getattr(mention, "pjt_no", "") or "").strip()
                ),
                "",
            )
            same_group_year = [
                mention for mention in project_mentions
                if latest_group_key
                and str(getattr(mention, "pjt_no", "") or "").strip() == latest_group_key
                and str(getattr(mention, "year", "") or "").strip() == target_year
            ]
            year_candidates = same_group_year or [
                mention for mention in project_mentions
                if str(getattr(mention, "year", "") or "").strip() == target_year
            ]
            if len(year_candidates) == 1:
                anchor = focus_entity_from_mention(year_candidates[0])
                if _recent_anchor_is_allowed(anchor, question=question, normalized_intent=normalized_intent):
                    return anchor, [], "recent_mention_year"
                return None, [_compact_recent_mention_candidate(year_candidates[0])], "detail_requires_instance_project_id"
            if len(year_candidates) > 1:
                return None, [_compact_recent_mention_candidate(mention) for mention in year_candidates[:5]], "recent_mention_year_ambiguity"

    mention_anchor = resolve_from_recent_mentions(
        question=question,
        recent_mentions=filtered_mentions,
    )
    if mention_anchor is not None:
        if _recent_anchor_is_allowed(mention_anchor, question=question, normalized_intent=normalized_intent):
            return mention_anchor, [], "recent_mention_relative"
        return None, [_compact_recent_mention_candidate(filtered_mentions[-1])], "detail_requires_instance_project_id"

    if len(filtered_mentions) == 1 and is_referential_followup(question):
        anchor = focus_entity_from_mention(filtered_mentions[0])
        if _recent_anchor_is_allowed(anchor, question=question, normalized_intent=normalized_intent):
            return anchor, [], "recent_mention_single_candidate"
        return None, [_compact_recent_mention_candidate(filtered_mentions[0])], "detail_requires_instance_project_id"

    if len(filtered_mentions) > 1 and is_referential_followup(question):
        return None, [_compact_recent_mention_candidate(mention) for mention in filtered_mentions[:5]], "recent_mention_ambiguity"

    return None, [], None


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


def _strategy_execution_path(strategy_meta: Optional[Dict[str, Any]]) -> str:

    if not isinstance(strategy_meta, dict):

        return ""

    turn_policy = strategy_meta.get("turn_policy") or {}

    if isinstance(turn_policy, dict):

        return str(turn_policy.get("execution_path") or "").strip().lower()

    return ""


def _strategy_has_selected_candidate(strategy_meta: Optional[Dict[str, Any]]) -> bool:

    if not isinstance(strategy_meta, dict):

        return False

    turn_policy = strategy_meta.get("turn_policy") or {}

    if isinstance(turn_policy, dict) and list(turn_policy.get("selected_candidate_ids") or []):

        return True

    turn_interpretation = strategy_meta.get("turn_interpretation") or {}

    if isinstance(turn_interpretation, dict) and list(turn_interpretation.get("selected_candidate_ids") or []):

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

    question: str,

    clarification_type: str,

    reason: str,

    latest_snapshot: Optional[Any],

    candidates: Optional[list[dict[str, Any]]] = None,

    focus_entity: Optional[FocusEntity] = None,

    ctx: Optional[Dict[str, Any]] = None,

) -> Dict[str, Any]:

    view_state_summary = {
        "has_visible_answer_manifest": latest_snapshot is not None,
        "manifest_visible_count": int(getattr(latest_snapshot, "visible_count", 0) or 0),
        "display_view_id": getattr(latest_snapshot, "view_id", None),
        "focus_kind": getattr(focus_entity, "kind", None),
        "focus_source": getattr(focus_entity, "source", None),
        "focus_title": getattr(focus_entity, "title_text", None),
    }
    message = compose_clarification_message(
        question=question,
        blocked_reason=reason,
        suggestions=list(candidates or []),
        focus_entity=focus_entity,
        view_state_summary=view_state_summary,
        ctx={
            "clarification_type": clarification_type,
            **dict(ctx or {}),
        },
    )

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

    has_active_scope_target = _has_active_scope_target(view_state)

    recent_mentions = get_recent_mentions(view_state)
    strategy_execution_path = _strategy_execution_path(strategy_meta)
    has_selected_candidate = _strategy_has_selected_candidate(strategy_meta)



    if _has_reset_cue(question):

        return ScopeDecision(followup_type="scope_reset", reset_requested=True)



    if _has_explicit_ids(normalized_intent_base):

        return ScopeDecision(followup_type="fresh_search")

    child_reference = _inspect_named_child_reference(question, scope_focus_entity)

    if child_reference["ambiguous"]:

        first_kind = None

        if child_reference["candidates"]:

            first_kind = str((child_reference["candidates"][0] or {}).get("context_kind") or "").strip().lower() or None

        return ScopeDecision(

            followup_type="ambiguous_followup",

            needs_clarification=True,

            clarification_payload=_build_clarification_payload(

                question=question,

                clarification_type="child_entity_ambiguity",

                reason="child_entity_ambiguity",

                latest_snapshot=latest_snapshot,

                candidates=child_reference["candidates"],

                focus_entity=scope_focus_entity,

                ctx={"subject": _CHILD_SUBJECT.get(first_kind or "people", "대상"), "subject_kind": first_kind or "people"},

            ),

        )

    if child_reference["missing_id"]:

        first_kind = None

        if child_reference["candidates"]:

            first_kind = str((child_reference["candidates"][0] or {}).get("context_kind") or "").strip().lower() or None

        return ScopeDecision(

            followup_type="ambiguous_followup",

            needs_clarification=True,

            clarification_payload=_build_clarification_payload(

                question=question,

                clarification_type="child_entity_missing_id",

                reason="child_entity_missing_id",

                latest_snapshot=latest_snapshot,

                candidates=child_reference["candidates"],

                focus_entity=scope_focus_entity,

                ctx={"subject": _CHILD_SUBJECT.get(first_kind or "people", "대상"), "subject_kind": first_kind or "people"},

            ),

        )



    if child_reference["matched"]:

        return ScopeDecision(

            followup_type="child_entity_followup",

            resolved_anchor=resolve_named_child_anchor_from_focus(

                question=question,

                focus_entity=scope_focus_entity,

            ),

        )



    if _has_refinement_cue(question, normalized_intent_base):

        refinement_filters = _extract_refinement_filters(normalized_intent_base)

        if not has_active_scope_target and not _has_independent_scope_axis(normalized_intent_base):
            recent_anchor, recent_candidates, recent_reason = _resolve_recent_mention_fallback(
                question=question,
                normalized_intent=normalized_intent_base,
                recent_mentions=recent_mentions,
            )
            if recent_anchor is not None:

                return ScopeDecision(

                    followup_type="refinement_followup",

                    resolved_anchor=recent_anchor,

                    refinement_filters=refinement_filters,

                )
            if recent_candidates:

                return ScopeDecision(

                    followup_type="ambiguous_followup",

                    needs_clarification=True,

                    clarification_payload=_build_clarification_payload(

                        question=question,

                        clarification_type="refinement_target_missing",

                        reason=recent_reason or "refinement_target_missing",

                        latest_snapshot=latest_snapshot,

                        candidates=recent_candidates,

                        focus_entity=scope_focus_entity,

                        ctx={"status": "unresolved", "reference_kind": "refinement"},

                    ),

                )
            return ScopeDecision(

                followup_type="ambiguous_followup",

                needs_clarification=True,

                clarification_payload=_build_clarification_payload(

                    question=question,

                    clarification_type="refinement_target_missing",

                    reason="refinement_target_missing",

                    latest_snapshot=latest_snapshot,

                    candidates=[],

                    focus_entity=scope_focus_entity,

                    ctx={"reference_kind": "refinement"},

                ),

            )

        if has_active_scope_target:

            return ScopeDecision(

                followup_type="refinement_followup",

                refinement_filters=refinement_filters,

            )

    if strategy_execution_path in {"reuse_manifest", "reuse_anchor"} or has_selected_candidate:

        return ScopeDecision(followup_type="reference_followup")



    has_reference_cue = bool(

        parse_source_reference(question) is not None

        or parse_ordinal_reference(question) is not None

        or is_referential_followup(question)

    )

    if has_reference_cue:

        if latest_snapshot is None and scope_focus_entity is None:
            recent_anchor, recent_candidates, recent_reason = _resolve_recent_mention_fallback(
                question=question,
                normalized_intent=normalized_intent_base,
                recent_mentions=recent_mentions,
            )
            if recent_anchor is not None:
                return ScopeDecision(

                    followup_type="reference_followup",

                    resolved_anchor=recent_anchor,

                )
            if recent_candidates:
                clarification_type = (
                    "reference_missing_context"
                    if recent_reason == "detail_requires_instance_project_id"
                    else "reference_ambiguity"
                )

                return ScopeDecision(

                    followup_type="ambiguous_followup",

                    needs_clarification=True,

                    clarification_payload=_build_clarification_payload(

                        question=question,

                        clarification_type=clarification_type,

                        reason=recent_reason or "reference_ambiguity",

                        latest_snapshot=latest_snapshot,

                        candidates=recent_candidates,

                        focus_entity=scope_focus_entity,

                        ctx={"status": "unresolved", "reference_kind": "recent_mention"},

                    ),

                )

            return ScopeDecision(

                followup_type="ambiguous_followup",

                needs_clarification=True,

                clarification_payload=_build_clarification_payload(

                    question=question,

                    clarification_type="reference_ambiguity",

                    reason="reference_missing_context",

                    latest_snapshot=latest_snapshot,

                    candidates=[],

                    focus_entity=scope_focus_entity,

                    ctx={"reference_kind": "reference"},

                ),

            )

        return ScopeDecision(

            followup_type="ambiguous_followup",

            needs_clarification=True,

            clarification_payload=_build_clarification_payload(

                question=question,

                clarification_type="reference_ambiguity",

                reason="unresolved_reference",

                latest_snapshot=latest_snapshot,

                candidates=[],

                focus_entity=scope_focus_entity,

                ctx={"reference_kind": "reference"},

            ),

        )



    return ScopeDecision(followup_type="fresh_search")
