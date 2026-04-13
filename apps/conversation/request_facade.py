from __future__ import annotations

import os

from dataclasses import is_dataclass, replace
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage
else:
    BaseMessage = Any

from apps.conversation.anchor_constraint_compiler import apply_anchor_lock as _apply_anchor_lock, apply_resolved_anchor_seed as _apply_resolved_anchor_seed
from apps.conversation.context_router import ContextRouterDecision, route_context
from apps.conversation.followup_anchor import anchor_to_seed_map, is_child_anchor_source, parse_display_limit, parse_ordinal_reference, parse_source_reference, resolve_followup_anchor
from apps.conversation.scope_resolver import resolve_scope_decision
from apps.conversation.followup_resolution import resolve_reference_context_followup
from apps.conversation.turn_policy import TurnPolicyResult, resolve_turn_policy
from apps.conversation.turn_trigger import TurnTriggerResult, run_turn_trigger
from apps.platform.settings import MAX_TOP_K_SIZE
from apps.api.runtime_helpers import log_event
from apps.planner.planner_defaults import (
    PLANNER_STAGE1_PROMPT_VERSION,
    PLANNER_STAGE15_PROMPT_VERSION,
    PLANNER_STAGE2_PROMPT_VERSION,
)
from apps.planner.planner_service import apply_question_analysis_v3
from apps.planner.query_analysis import run_question_analysis
from apps.planner.query_intent import (
    _cheap_precheck,
    classify_query as classify_query_intent,
    extract_perf_types,
    extract_title_terms,
    extract_years,
)
from apps.platform.pipeline_steps import normalize_intent
from apps.platform.schemas import IntentPayloadV3
from apps.conversation.view_state import (
    ConversationViewState,
    clear_view_state_scope,
    get_active_child_anchor,
    get_active_focus_entity,
    get_recent_mentions,
    set_visible_answer_manifest,
)


_DISPLAY_LIMIT_SENTINEL = 10**9
_DEFAULT_RETRIEVAL_LIMIT = 20
_LIST_LIKE_OUTPUT_TYPES = {"list", "relation", "comparison", "series", "stats"}
_BROAD_HISTORY_CUES = (
    "다른 활동",
    "활동 이력",
    "활동이력",
    "활동 내역",
    "활동내역",
    "참여이력",
    "프로필",
    "소속",
    "현황",
    "이력",
    "업적",
)
_LIST_AXIS_CUES = (
    "목록",
    "리스트",
    "보여줘",
    "보여 줘",
    "알려줘",
    "알려 줘",
    "찾아줘",
    "찾아 줘",
    "있는지",
    "있어",
)
_SUBJECT_AXIS_CUES = (
    "연구자",
    "연구원",
    "사람",
    "기관",
    "회사",
    "조직",
    "성과",
    "논문",
    "특허",
    "보고서",
    "참여과제",
    "참여 과제",
    "참여성과",
    "참여 성과",
)




def _first_text(*values: Any) -> str:

    for value in values:

        text = str(value or "").strip()

        if text:

            return text

    return ""





def _resolve_followup_owner_lock(followup_resolution: dict[str, Any]) -> tuple[str | None, str | None]:
    if not isinstance(followup_resolution, dict):
        return None, None

    status = str(followup_resolution.get("followup_resolution_status") or "").strip().lower()
    if status != "resolved":
        return None, None

    selected_prev_item = dict(followup_resolution.get("selected_prev_item") or {})
    focus_entity = dict(followup_resolution.get("focus_entity") or {})
    context_kind = _first_text(

        selected_prev_item.get("context_kind"),

        focus_entity.get("kind"),

        followup_resolution.get("selected_prev_context_kind"),

    ).lower()

    if context_kind == "perf":
        return "perf", "followup_context_perf"
    if context_kind == "project":
        return "project", "followup_context_project"
    return None, None


def _apply_followup_context_lock(normalized_intent: Any, followup_resolution: dict[str, Any]) -> Any:
    locked_owner_kind, lock_reason = _resolve_followup_owner_lock(followup_resolution)
    if not locked_owner_kind:
        return normalized_intent

    if isinstance(normalized_intent, dict):
        patched = dict(normalized_intent)
        patched["context_owner_lock"] = locked_owner_kind
        patched["context_owner_lock_reason"] = lock_reason
        return patched

    if is_dataclass(normalized_intent):
        updates: dict[str, Any] = {}
        if hasattr(normalized_intent, "context_owner_lock"):
            updates["context_owner_lock"] = locked_owner_kind
        if hasattr(normalized_intent, "context_owner_lock_reason"):
            updates["context_owner_lock_reason"] = lock_reason
        return replace(normalized_intent, **updates) if updates else normalized_intent

    if hasattr(normalized_intent, "context_owner_lock") or hasattr(normalized_intent, "context_owner_lock_reason"):
        try:
            if hasattr(normalized_intent, "context_owner_lock"):
                setattr(normalized_intent, "context_owner_lock", locked_owner_kind)
            if hasattr(normalized_intent, "context_owner_lock_reason"):
                setattr(normalized_intent, "context_owner_lock_reason", lock_reason)
        except Exception:
            pass
    return normalized_intent




def has_explicit_precheck_signals(precheck: dict[str, Any]) -> bool:

    ids_map = (precheck or {}).get("ids_map")

    if not isinstance(ids_map, dict):

        return False

    for values in ids_map.values():

        if isinstance(values, (list, tuple, set)):

            if any(str(value).strip() for value in values):

                return True

            continue

        if str(values or "").strip():

            return True

    return False





def _has_ids_map_values(ids_map: Any) -> bool:
    if not isinstance(ids_map, dict):
        return False
    for values in ids_map.values():

        if isinstance(values, (list, tuple, set)):

            if any(str(value).strip() for value in values):

                return True

            continue
        if str(values or "").strip():
            return True
    return False




def _get_field(source: Any, key: str, default: Any = None) -> Any:

    if source is None:

        return default

    if isinstance(source, dict):

        return source.get(key, default)

    return getattr(source, key, default)





def _replace_fields(source: Any, **updates: Any) -> Any:
    if source is None:

        return None

    if isinstance(source, dict):

        patched = dict(source)

        patched.update(updates)

        return patched

    if is_dataclass(source):

        return replace(source, **updates)

    for key, value in updates.items():

        try:

            setattr(source, key, value)

        except Exception:

            pass

    return source


def _clear_active_view_scope(view_state: ConversationViewState) -> ConversationViewState:
    return clear_view_state_scope(view_state)


def _merge_unique_terms(values: Any, *terms: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    iterable = values if isinstance(values, (list, tuple, set)) else [values]
    for value in iterable:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
    for term in terms:
        text = str(term or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
    return merged


def _has_subject_axis_request(question: str, normalized_intent: Any) -> bool:
    for field in (
        "people_terms",
        "org_terms",
        "lead_org_terms",
        "participant_org_terms",
        "people_affiliation_org_terms",
        "perf_types",
    ):
        values = _get_field(normalized_intent, field, None) or []
        if any(str(value or "").strip() for value in (values if isinstance(values, (list, tuple, set)) else [values])):
            return True
    if str(_get_field(normalized_intent, "org_role", "") or "").strip():
        return True
    text = str(question or "").strip()
    return bool(text and any(token in text for token in _SUBJECT_AXIS_CUES))


def _has_broad_history_or_list_cue(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    return any(token in text for token in [*_BROAD_HISTORY_CUES, *_LIST_AXIS_CUES])


def _resolve_followup_subject_hint(anchor: Any = None, *, followup_resolution: Optional[Dict[str, Any]] = None) -> tuple[str | None, str | None]:
    anchor_kind = str(getattr(anchor, "kind", "") or "").strip().lower()
    anchor_title = str(getattr(anchor, "title_text", "") or "").strip()
    if anchor_kind and anchor_title:
        return anchor_kind, anchor_title

    resolution = dict(followup_resolution or {})
    selected_prev_item = dict(resolution.get("selected_prev_item") or {})
    focus_entity = dict(resolution.get("focus_entity") or {})
    kind = _first_text(
        selected_prev_item.get("context_kind"),
        focus_entity.get("kind"),
    ).lower()
    if not kind:
        return None, None
    title = _first_text(
        selected_prev_item.get("person_name"),
        selected_prev_item.get("title"),
        focus_entity.get("title_text"),
    )
    return kind, title or None


def _apply_followup_subject_hint(
    normalized_intent: Any,
    *,
    anchor: Any = None,
    followup_resolution: Optional[Dict[str, Any]] = None,
) -> Any:
    kind, title = _resolve_followup_subject_hint(anchor, followup_resolution=followup_resolution)
    if not kind or not title:
        return normalized_intent

    if kind == "people":
        people_terms = _merge_unique_terms(_get_field(normalized_intent, "people_terms", []) or [], title)
        return _replace_fields(normalized_intent, people_terms=people_terms)
    if kind == "org":
        org_terms = _merge_unique_terms(_get_field(normalized_intent, "org_terms", []) or [], title)
        return _replace_fields(normalized_intent, org_terms=org_terms)
    if kind == "perf":
        title_terms = _merge_unique_terms(_get_field(normalized_intent, "title", []) or [], title)
        return _replace_fields(normalized_intent, title=title_terms)
    return normalized_intent


def _should_force_planner_for_explicit_seed(
    *,
    question: str,
    normalized_intent: Any,
    anchor: Any = None,
    followup_resolution: Optional[Dict[str, Any]] = None,
) -> bool:
    if is_child_anchor_source(getattr(anchor, "source", None)):
        return True
    followup_kind = str((followup_resolution or {}).get("followup_reference_kind") or "").strip().lower()
    if followup_kind == "child_entity":
        return True
    if _has_subject_axis_request(question, normalized_intent):
        return True
    if _has_broad_history_or_list_cue(question):
        return True
    return False




def _normalize_org_role_hint(normalized_intent: Any, question: str) -> Optional[str]:

    org_role = str(_get_field(normalized_intent, "org_role", "") or "").strip().lower()

    if org_role in {"performer", "performing"}:

        return "lead"

    if org_role:

        return org_role

    text = str(question or "").strip()

    if any(token in text for token in ("참여기관", "협력기관")):

        return "participant"

    if any(token in text for token in ("수행기관", "주관기관")):

        return "lead"

    if any(token in text for token in ("소속기관", "소속")):

        return "affiliation"

    return None





def _has_project_anchor_seed(normalized_intent: Any) -> bool:

    ids_map = _get_field(normalized_intent, "ids_map", {}) or {}

    if not isinstance(ids_map, dict):

        return False

    return bool(ids_map.get("pjt_id") or ids_map.get("pjt_no"))





def _has_role_scoped_org_request(normalized_intent: Any, question: str) -> bool:

    if _normalize_org_role_hint(normalized_intent, question):

        return True

    for key in ("lead_org_terms", "participant_org_terms", "people_affiliation_org_terms"):

        values = _get_field(normalized_intent, key, None) or []

        if any(str(value or "").strip() for value in values):

            return True

    return False





def _resolve_child_detail_anchor(

    anchor: Any,

    *,

    followup_resolution: Optional[Dict[str, Any]] = None,

) -> tuple[str | None, str | None]:

    resolution = dict(followup_resolution or {})

    status = str(resolution.get("followup_resolution_status") or "").strip().lower()

    if status != "resolved":

        return None, None

    anchor_kind = str(getattr(anchor, "kind", "") or "").strip().lower()

    anchor_source = str(getattr(anchor, "source", "") or "").strip().lower()

    if is_child_anchor_source(anchor_source) and anchor_kind in {"people", "org", "perf"}:

        return anchor_kind, anchor_source or None

    followup_kind = str(resolution.get("followup_reference_kind") or "").strip().lower()

    if followup_kind != "child_entity":

        return None, None

    focus_entity = dict(resolution.get("focus_entity") or {})

    selected_prev_item = dict(resolution.get("selected_prev_item") or {})

    resolved_kind = _first_text(

        focus_entity.get("kind"),

        selected_prev_item.get("context_kind"),

    ).lower()

    if resolved_kind not in {"people", "org", "perf"}:

        return None, None

    resolved_source = _first_text(

        resolution.get("anchor_source"),

        resolution.get("seed_source"),

    ).lower()

    return resolved_kind, resolved_source or None



def _coerce_child_anchor_detail_followup(

    normalized_intent: Any,

    question_analysis: Any,

    *,

    anchor: Any = None,

    question: str,

    followup_resolution: Dict[str, Any],

    request_id: Optional[str],

    conversation_id: str,

) -> tuple[Any, Any]:

    anchor_kind, anchor_source = _resolve_child_detail_anchor(

        anchor,

        followup_resolution=followup_resolution,

    )

    if not anchor_kind:

        return normalized_intent, question_analysis

    intent_needs_update = (

        str(_get_field(normalized_intent, "base_route", "") or "").strip().lower() != anchor_kind

        or str(_get_field(normalized_intent, "action", "") or "").strip().lower() != "detail"

        or str(_get_field(normalized_intent, "output_type", "") or "").strip().lower() != "detail"

    )

    if intent_needs_update:

        normalized_intent = _replace_fields(

            normalized_intent,

            base_route=anchor_kind,

            action="detail",

            output_type="detail",

        )

    qa_needs_update = bool(

        question_analysis is not None

        and (

            str(_get_field(question_analysis, "head", "") or "").strip().lower() != anchor_kind

            or str(_get_field(question_analysis, "action", "") or "").strip().lower() != "detail"

            or str(_get_field(question_analysis, "output_type", "") or "").strip().lower() != "detail"

            or _coerce_positive_int(_get_field(question_analysis, "limit", None)) != 1

            or _coerce_positive_int(_get_field(question_analysis, "display_limit", None)) != 1

        )

    )

    if qa_needs_update:

        question_analysis = _replace_fields(

            question_analysis,

            head=anchor_kind,

            action="detail",

            output_type="detail",

            limit=1,

            display_limit=1,

            retrieval_query=str(question or "").strip() or _get_field(question_analysis, "retrieval_query", None),

        )

    if intent_needs_update or qa_needs_update:

        log_event(

            "FOLLOWUP.CHILD_DETAIL.COERCED",

            request_id=request_id,

            conversation_id=conversation_id,

            anchor_kind=anchor_kind,

            anchor_source=anchor_source,

            followup_reference_kind=(followup_resolution or {}).get("followup_reference_kind"),

        )

    return normalized_intent, question_analysis



def _coerce_project_anchor_role_followup(

    normalized_intent: Any,

    question_analysis: Any,

    *,

    question: str,

    followup_resolution: Dict[str, Any],

    request_id: Optional[str],

    conversation_id: str,

) -> tuple[Any, Any]:

    status = str((followup_resolution or {}).get("followup_resolution_status") or "").strip().lower()

    if status != "resolved":

        return normalized_intent, question_analysis

    if not _has_project_anchor_seed(normalized_intent):

        return normalized_intent, question_analysis

    if not _has_role_scoped_org_request(normalized_intent, question):

        return normalized_intent, question_analysis



    role_hint = _normalize_org_role_hint(normalized_intent, question)

    org_terms = list(_get_field(normalized_intent, "org_terms", []) or [])

    lead_terms = list(_get_field(normalized_intent, "lead_org_terms", []) or [])

    participant_terms = list(_get_field(normalized_intent, "participant_org_terms", []) or [])

    affiliation_terms = list(_get_field(normalized_intent, "people_affiliation_org_terms", []) or [])



    intent_updates = {

        "base_route": "project",

        "action": "detail",

        "output_type": "detail",

    }

    if role_hint:

        intent_updates["org_role"] = role_hint

        if role_hint == "lead" and not lead_terms and org_terms:

            intent_updates["lead_org_terms"] = list(org_terms)

        elif role_hint == "participant" and not participant_terms and org_terms:

            intent_updates["participant_org_terms"] = list(org_terms)

        elif role_hint == "affiliation" and not affiliation_terms and org_terms:

            intent_updates["people_affiliation_org_terms"] = list(org_terms)



    normalized_intent = _replace_fields(normalized_intent, **intent_updates)



    if question_analysis is not None:

        qa_updates = {

            "head": "project",

            "action": "detail",

            "output_type": "detail",

            "limit": 1,

            "display_limit": 1,

            "retrieval_query": str(question or "").strip() or _get_field(question_analysis, "retrieval_query", None),

        }

        question_analysis = _replace_fields(question_analysis, **qa_updates)



    log_event(

        "FOLLOWUP.ROLE_DETAIL.COERCED",

        request_id=request_id,

        conversation_id=conversation_id,

        role_kind=role_hint,

        anchor_kind="project",

        has_pjt_id_seed=int(bool((_get_field(normalized_intent, "ids_map", {}) or {}).get("pjt_id"))),

        has_pjt_no_seed=int(bool((_get_field(normalized_intent, "ids_map", {}) or {}).get("pjt_no"))),

    )

    return normalized_intent, question_analysis





def _build_followup_resolution_from_anchor(anchor: Any, snapshot: Any, question: str) -> Dict[str, Any]:
    if anchor is None:

        return {

            "followup_resolution_status": "none",

            "selected_prev_item": None,

            "seed_map": {},

            "seed_source": None,

            "explicit_followup": False,

            "followup_reference_kind": None,

            "requested_token": None,

            "requested_index": None,

            "available_count": len(getattr(snapshot, "items", []) or []),

            "anchor_source": None,

            "focus_entity": None,

        }

    selected_prev_item = None
    if anchor is not None and any(
        [
            anchor.display_rank is not None,
            anchor.pjt_id,
            anchor.pjt_no,
            anchor.rst_id,
            anchor.person_no,
            anchor.org_id,
            anchor.org_code,
            anchor.biz_no,
            anchor.doi,
            anchor.issn,
            anchor.doc_id,
            anchor.title_text,
        ]
    ):
        selected_prev_item = {
            "index": anchor.display_rank,
            "pjt_id": anchor.pjt_id,
            "pjt_no": anchor.pjt_no,
            "rst_id": anchor.rst_id,
            "person_no": anchor.person_no,

            "org_id": anchor.org_id,

            "org_code": anchor.org_code,

            "biz_no": anchor.biz_no,

            "doi": anchor.doi,

            "issn": anchor.issn,
            "doc_id": anchor.doc_id,
            "doc_type": anchor.doc_type,
            "title": anchor.title_text,
            "person_name": anchor.title_text if str(getattr(anchor, "kind", "") or "").strip().lower() == "people" else None,
            "context_kind": anchor.kind,
            "view_id": anchor.view_id,
        }
    source_reference = parse_source_reference(question)
    ordinal_reference = parse_ordinal_reference(question)
    reference_kind = (
        "child_entity"
        if is_child_anchor_source(getattr(anchor, "source", None))
        else
        "source_reference"
        if anchor.source == "display_snapshot" and source_reference is not None
        else "deictic"
        if anchor.source == "display_snapshot" and ordinal_reference is None
        else "ordinal"
        if anchor.source == "display_snapshot"
        else "focus"
        if anchor.source != "explicit_id"
        else "explicit_id"
    )
    return {
        "followup_resolution_status": "resolved",
        "selected_prev_item": selected_prev_item,
        "seed_map": anchor_to_seed_map(anchor),

        "seed_source": anchor.source,

        "explicit_followup": anchor.source != "explicit_id",

        "followup_reference_kind": reference_kind,

        "requested_token": None,

        "requested_index": anchor.display_rank,

        "available_count": len(getattr(snapshot, "items", []) or []),

        "anchor_source": anchor.source,

        "focus_entity": anchor.model_dump() if hasattr(anchor, "model_dump") else None,

    }


def _build_turn_contract(
    *,
    normalized_intent: Any,
    question_analysis: Any,
    count_validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    count_validation = dict(count_validation or {})
    action = str(getattr(question_analysis, "action", "") or "").strip().lower()
    output_type = str(getattr(question_analysis, "output_type", "") or "").strip().lower()
    if not action and isinstance(normalized_intent, dict):
        action = str(normalized_intent.get("action") or "").strip().lower()
    elif not action:
        action = str(getattr(normalized_intent, "action", "") or "").strip().lower()
    if not output_type and isinstance(normalized_intent, dict):
        output_type = str(normalized_intent.get("output_type") or "").strip().lower()
    elif not output_type:
        output_type = str(getattr(normalized_intent, "output_type", "") or "").strip().lower()
    turn_kind = output_type or action or "summary"
    planner_explicit_count = _coerce_positive_int(count_validation.get("planner_explicit_count"))
    explicit_count_requested = planner_explicit_count is not None
    requested_count = _coerce_positive_int(getattr(question_analysis, "display_limit", None))
    if requested_count is None:
        requested_count = _coerce_positive_int(getattr(question_analysis, "limit", None))
    if output_type in {"relation", "comparison", "series"}:
        count_contract = "exact"
    elif action == "list" or output_type == "list":
        count_contract = "exact" if explicit_count_requested else "partial_ok"
    else:
        count_contract = "n/a"
    if count_contract == "exact" and turn_kind in _LIST_LIKE_OUTPUT_TYPES:
        answer_publishability_policy = "publishable_if_supported"
        followup_rights = "source_allowed"
    elif count_contract == "partial_ok":
        answer_publishability_policy = "withhold_on_partial"
        followup_rights = "none"
    else:
        answer_publishability_policy = "never_publish"
        followup_rights = "none"
    return {
        "turn_kind": turn_kind,
        "count_contract": count_contract,
        "requested_count": requested_count,
        "explicit_count_requested": bool(explicit_count_requested),
        "answer_publishability_policy": answer_publishability_policy,
        "followup_rights": followup_rights,
        "planner_count_source": str(count_validation.get("planner_count_source") or "").strip().lower() or None,
        "planner_explicit_count": planner_explicit_count,
        "validation_status": str(count_validation.get("validation_status") or "valid").strip().lower() or "valid",
        "invalid_reason": str(count_validation.get("invalid_reason") or "").strip().lower() or None,
    }


def _build_policy_clarification_resolution(
    *,
    policy: TurnPolicyResult | Dict[str, Any],
    trigger: TurnTriggerResult | Dict[str, Any],
    snapshot: Any,
    question: str,
) -> Dict[str, Any]:
    policy_payload = policy if isinstance(policy, dict) else policy.model_dump()
    trigger_payload = trigger if isinstance(trigger, dict) else trigger.model_dump()
    clarification_payload = dict(policy_payload.get("clarification_payload") or {})
    requested_index = parse_source_reference(question)
    if requested_index is None:
        requested_index = parse_ordinal_reference(question)
    reference_style = str(trigger_payload.get("reference_style") or "").strip().lower()
    followup_reference_kind = "source_reference" if reference_style == "source_reference" else "entity_reference"
    return {
        "followup_resolution_status": "clarification_required",
        "selected_prev_item": None,
        "seed_map": {},
        "seed_source": None,
        "explicit_followup": True,
        "followup_reference_kind": followup_reference_kind,
        "requested_token": question,
        "requested_index": requested_index,
        "available_count": len(getattr(snapshot, "items", []) or []),
        "anchor_source": None,
        "focus_entity": None,
        "clarification_type": clarification_payload.get("clarification_type") or "followup_policy_block",
        "clarification_reason": clarification_payload.get("reason") or policy_payload.get("blocked_reason"),
        "clarification_payload": clarification_payload,
    }





def _build_strategy_meta(
    normalized_intent: Any,
    question_analysis: Any,
    *,
    followup_resolution: Optional[Dict[str, Any]] = None,
    turn_id: Optional[str] = None,
    turn_trigger: Optional[Dict[str, Any]] = None,
    turn_contract: Optional[Dict[str, Any]] = None,
    turn_policy: Optional[Dict[str, Any]] = None,
    count_validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    if isinstance(normalized_intent, dict):

        ids_map = normalized_intent.get("ids_map") or {}

        candidate_keys = normalized_intent.get("candidate_keys") or {}

        project_key_policy = normalized_intent.get("project_key_policy")

        join_resolution_policy = normalized_intent.get("join_resolution_policy")

        join_key_mode = normalized_intent.get("join_key_mode")

        is_exact_key_query = bool(normalized_intent.get("is_exact_key_query", False))

    else:

        ids_map = getattr(normalized_intent, "ids_map", None) or {}

        candidate_keys = getattr(normalized_intent, "candidate_keys", None) or {}

        project_key_policy = getattr(normalized_intent, "project_key_policy", None)

        join_resolution_policy = getattr(normalized_intent, "join_resolution_policy", None)

        join_key_mode = getattr(normalized_intent, "join_key_mode", None)

        is_exact_key_query = bool(getattr(normalized_intent, "is_exact_key_query", False))

    followup_resolution = dict(followup_resolution or {})
    turn_trigger = dict(turn_trigger or {})
    turn_contract = dict(turn_contract or {})
    turn_policy = dict(turn_policy or {})
    count_validation = dict(count_validation or {})

    selected_prev_item = dict(followup_resolution.get("selected_prev_item") or {})

    focus_entity = dict(followup_resolution.get("focus_entity") or {})

    return {

        "strategy_version": str(getattr(question_analysis, "strategy_version", "v3") or "v3"),

        "candidate_keys": dict(candidate_keys),

        "project_key_policy": project_key_policy,

        "anchor_locked": bool(str(project_key_policy or "").strip().lower() in {"anchor_locked_pjt_id", "anchor_locked_pjt_no"}),

        "anchor_locked_key_kind": "pjt_id" if str(project_key_policy or "").strip().lower() == "anchor_locked_pjt_id" else ("pjt_no" if str(project_key_policy or "").strip().lower() == "anchor_locked_pjt_no" else None),

        "join_resolution_policy": join_resolution_policy,

        "join_key_mode": join_key_mode,

        "ids_map": dict(ids_map),

        "has_project_candidate_key": bool((candidate_keys.get("project_key") or [])),

        "has_perf_candidate_key": bool((candidate_keys.get("perf_key") or [])),

        "is_exact_key_query": is_exact_key_query,

        "candidate_project_key_count": len(candidate_keys.get("project_key") or []),

        "candidate_perf_key_count": len(candidate_keys.get("perf_key") or []),

        "selected_prev_item": selected_prev_item or None,

        "selected_prev_context_kind": selected_prev_item.get("context_kind") if selected_prev_item else None,

        "selected_prev_index": selected_prev_item.get("index") if selected_prev_item else None,

        "selected_prev_pjt_id": selected_prev_item.get("pjt_id") if selected_prev_item else None,

        "selected_prev_pjt_no": selected_prev_item.get("pjt_no") if selected_prev_item else None,

        "selected_prev_rst_id": selected_prev_item.get("rst_id") if selected_prev_item else None,

        "selected_prev_person_no": selected_prev_item.get("person_no") if selected_prev_item else None,

        "selected_prev_org_id": selected_prev_item.get("org_id") if selected_prev_item else None,

        "followup_resolution_status": followup_resolution.get("followup_resolution_status") or "none",

        "followup_reference_kind": followup_resolution.get("followup_reference_kind"),

        "explicit_followup": bool(followup_resolution.get("explicit_followup")),

        "requested_token": followup_resolution.get("requested_token"),

        "requested_index": followup_resolution.get("requested_index"),
        "available_count": followup_resolution.get("available_count"),
        "seed_source": followup_resolution.get("seed_source"),
        "anchor_source": followup_resolution.get("anchor_source"),
        "anchor_reference_kind": followup_resolution.get("followup_reference_kind"),
        "clarification_type": followup_resolution.get("clarification_type"),
        "clarification_reason": followup_resolution.get("clarification_reason"),
        "clarification_payload": dict(followup_resolution.get("clarification_payload") or {}),
        "focus_entity_key": focus_entity.get("pjt_id") or focus_entity.get("pjt_no") or focus_entity.get("rst_id") or focus_entity.get("person_no") or focus_entity.get("org_id") or focus_entity.get("org_code") or focus_entity.get("biz_no") or focus_entity.get("doi") or focus_entity.get("issn") or focus_entity.get("doc_id"),
        "focus_entity": focus_entity or None,
        "candidate_items": list(followup_resolution.get("candidate_items") or []),
        "display_view_id": selected_prev_item.get("view_id") if selected_prev_item else focus_entity.get("view_id"),

        "display_rank": focus_entity.get("display_rank"),

        "turn_id": str(turn_id or "").strip() or None,
        "turn_trigger": turn_trigger,
        "turn_trigger_intent": turn_trigger.get("turn_intent"),
        "turn_reference_style": turn_trigger.get("reference_style"),
        "turn_contract": turn_contract,
        "turn_kind": turn_contract.get("turn_kind"),
        "count_contract": turn_contract.get("count_contract"),
        "requested_count": turn_contract.get("requested_count"),
        "explicit_count_requested": bool(turn_contract.get("explicit_count_requested")),
        "planner_count_source": turn_contract.get("planner_count_source") or count_validation.get("planner_count_source"),
        "planner_explicit_count": turn_contract.get("planner_explicit_count"),
        "count_contract_validation_status": turn_contract.get("validation_status") or count_validation.get("validation_status") or "valid",
        "count_contract_invalid_reason": turn_contract.get("invalid_reason") or count_validation.get("invalid_reason"),
        "count_contract_clarification_payload": dict(count_validation.get("clarification_payload") or {}),
        "turn_policy": turn_policy,
        "turn_execution_path": turn_policy.get("execution_path"),
        "turn_policy_blocked_reason": turn_policy.get("blocked_reason"),

    }





def _build_intent_payload_object(
    normalized_intent: Any,
    question_analysis: Any,
    *,
    followup_resolution: Optional[Dict[str, Any]] = None,
    turn_id: Optional[str] = None,
    turn_trigger: Optional[Dict[str, Any]] = None,
    turn_contract: Optional[Dict[str, Any]] = None,
    turn_policy: Optional[Dict[str, Any]] = None,
    count_validation: Optional[Dict[str, Any]] = None,
) -> Any:

    strategy_meta = _build_strategy_meta(
        normalized_intent,
        question_analysis,
        followup_resolution=followup_resolution,
        turn_id=turn_id,
        turn_trigger=turn_trigger,
        turn_contract=turn_contract,
        turn_policy=turn_policy,
        count_validation=count_validation,
    )

    try:

        return IntentPayloadV3(

            intent_payload_version="v3",

            normalized_intent=normalized_intent,

            question_analysis=question_analysis,

            strategy_meta=strategy_meta,

        )

    except TypeError:

        payload = IntentPayloadV3(normalized_intent=normalized_intent)

        if hasattr(payload, "intent_payload_version"):

            setattr(payload, "intent_payload_version", "v3")

        if hasattr(payload, "question_analysis"):

            setattr(payload, "question_analysis", question_analysis)

        if hasattr(payload, "strategy_meta"):

            setattr(payload, "strategy_meta", strategy_meta)

        return payload





def _coerce_positive_int(value: Any) -> Optional[int]:

    try:

        number = int(value)

    except Exception:

        return None

    return number if number >= 1 else None





def _derive_planner_count_source(*, action: str, output_type: str, has_explicit_count: bool) -> str:
    if output_type == "detail" or action == "detail":
        return "detail_forced"
    if has_explicit_count and (action == "list" or output_type in _LIST_LIKE_OUTPUT_TYPES):
        return "explicit_count"
    return "planner_default"


def _build_count_contract_clarification_payload(
    *,
    invalid_reason: str,
    planner_limit: Optional[int],
    planner_display_limit: Optional[int],
    planner_explicit_count: Optional[int],
) -> Dict[str, Any]:
    reason = str(invalid_reason or "").strip().lower()
    if reason == "explicit_count_mismatch":
        message = "질문에서 요청한 개수와 검색 계획의 표시 개수가 맞지 않아 답변을 진행하지 않습니다. 대상이나 개수를 다시 지정해 주세요."
    elif reason == "detail_count_contract_violation":
        message = "상세 조회는 한 대상을 기준으로만 진행할 수 있는데 검색 계획의 개수 계약이 맞지 않아 답변을 보류합니다. 대상을 하나로 지정해 다시 질문해 주세요."
    else:
        message = "요청한 표시 개수를 안정적으로 확인하지 못해 답변을 진행하지 않습니다. 대상이나 개수를 더 구체적으로 지정해 다시 질문해 주세요."
    return {
        "status": "clarification_required",
        "clarification_type": "planner_count_contract",
        "reason": reason,
        "message": message,
        "planner_limit": planner_limit,
        "planner_display_limit": planner_display_limit,
        "planner_explicit_count": planner_explicit_count,
    }


def _resolve_question_analysis_count(question_analysis: Any, *, question: str, request_id: Optional[str], conversation_id: str) -> Dict[str, Any]:

    """Validate the planner-assembled count contract without rewriting it."""

    if question_analysis is None:

        return {
            "validation_status": "invalid",
            "invalid_reason": "missing_question_analysis",
            "planner_limit": None,
            "planner_display_limit": None,
            "planner_explicit_count": None,
            "planner_count_source": "planner_default",
            "clarification_payload": _build_count_contract_clarification_payload(
                invalid_reason="missing_question_analysis",
                planner_limit=None,
                planner_display_limit=None,
                planner_explicit_count=None,
            ),
        }



    action = str(getattr(question_analysis, "action", "") or "").strip().lower()

    output_type = str(getattr(question_analysis, "output_type", "") or "").strip().lower()

    is_list_like = action == "list" or output_type in _LIST_LIKE_OUTPUT_TYPES

    explicit_count = parse_display_limit(question, default=_DISPLAY_LIMIT_SENTINEL)

    has_explicit_count = explicit_count != _DISPLAY_LIMIT_SENTINEL



    planner_limit_raw = getattr(question_analysis, "limit", None)

    planner_display_limit_raw = getattr(question_analysis, "display_limit", None)

    planner_limit = _coerce_positive_int(planner_limit_raw)

    planner_display_limit = _coerce_positive_int(planner_display_limit_raw)

    planner_count_source = _derive_planner_count_source(
        action=action,
        output_type=output_type,
        has_explicit_count=has_explicit_count,
    )
    invalid_reason: Optional[str] = None
    if planner_limit is None:
        invalid_reason = "missing_limit"
    elif planner_display_limit is None:
        invalid_reason = "missing_display_limit"
    elif planner_limit > MAX_TOP_K_SIZE or planner_display_limit > MAX_TOP_K_SIZE:
        invalid_reason = "max_top_k_exceeded"
    elif planner_display_limit > planner_limit:
        invalid_reason = "display_gt_limit"
    elif output_type == "detail" or action == "detail":
        if planner_limit != 1 or planner_display_limit != 1:
            invalid_reason = "detail_count_contract_violation"
    elif is_list_like and has_explicit_count and planner_display_limit != explicit_count:
        invalid_reason = "explicit_count_mismatch"
    validation_status = "invalid" if invalid_reason else "valid"
    clarification_payload = (
        _build_count_contract_clarification_payload(
            invalid_reason=invalid_reason,
            planner_limit=planner_limit,
            planner_display_limit=planner_display_limit,
            planner_explicit_count=explicit_count if has_explicit_count else None,
        )
        if invalid_reason
        else None
    )
    log_event(
        "PLANNER.COUNT_CONTRACT",
        request_id=request_id,
        conversation_id=conversation_id,
        source="planner_invalid" if invalid_reason else "planner_validated",
        action=action or None,
        output_type=output_type or None,
        planner_limit=planner_limit_raw,
        planner_display_limit=planner_display_limit_raw,
        planner_explicit_count=None if not has_explicit_count else explicit_count,
        planner_count_source=planner_count_source,
        validation_status=validation_status,
        invalid_reason=invalid_reason,
    )
    return {
        "validation_status": validation_status,
        "invalid_reason": invalid_reason,
        "planner_limit": planner_limit,
        "planner_display_limit": planner_display_limit,
        "planner_explicit_count": explicit_count if has_explicit_count else None,
        "planner_count_source": planner_count_source,
        "clarification_payload": clarification_payload,
    }





def _build_explicit_only_hint(question: str) -> Dict[str, Any]:
    return {
        "years": extract_years(question),
        "perf_types": extract_perf_types(question),
        "title_terms": extract_title_terms(question),
    }


async def build_intent_payload(
    *,
    question: str,
    conversation_id: str,
    chat_history: List[BaseMessage],
    prev_context: List[Dict[str, Any]],
    request_id: Optional[str],
    turn_id: Optional[str],
    canonical_evidence: Optional[List[Dict[str, Any]]] = None,
    view_state: Optional[ConversationViewState] = None,
) -> tuple[Any, Any]:
    precheck = _cheap_precheck(question)

    explicit_only_hint = _build_explicit_only_hint(question)

    kws: List[str] = []

    raw_intent = classify_query_intent(question, kws, hint=explicit_only_hint)

    normalized_intent_base = normalize_intent(

        raw_intent,

        query=question,

        keywords=kws,

        hint_years=list(explicit_only_hint.get("years", [])),

        hint_perf_types=list(explicit_only_hint.get("perf_types", [])),

        hint_title_terms=list(explicit_only_hint.get("title_terms", [])),

    )
    active_view_state = view_state or ConversationViewState()
    question_analysis = None
    planner_failed = 0
    base_route = str((normalized_intent_base.get("base_route") if isinstance(normalized_intent_base, dict) else getattr(normalized_intent_base, "base_route", None)) or "project").strip().lower() or "project"
    base_ids_map = (normalized_intent_base.get("ids_map") if isinstance(normalized_intent_base, dict) else getattr(normalized_intent_base, "ids_map", None)) or {}
    has_explicit_seed = has_explicit_precheck_signals(precheck) or _has_ids_map_values(base_ids_map)
    source_reference_requested = parse_source_reference(question) is not None
    ordinal_reference_requested = parse_ordinal_reference(question) is not None
    previous_turn_contract = dict(getattr(active_view_state, "last_query_contract", {}) or {})
    turn_trigger_result = run_turn_trigger(
        question=question,
        view_state=active_view_state,
        has_explicit_seed=has_explicit_seed,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    turn_trigger = await turn_trigger_result if isawaitable(turn_trigger_result) else turn_trigger_result
    turn_policy = resolve_turn_policy(
        turn_trigger=turn_trigger,
        view_state=active_view_state,
        has_explicit_seed=has_explicit_seed,
    )
    log_event(
        "TURN.POLICY",
        request_id=request_id,
        conversation_id=conversation_id,
        turn_intent=turn_trigger.turn_intent,
        reference_style=turn_trigger.reference_style,
        execution_path=turn_policy.execution_path,
        blocked_reason=turn_policy.blocked_reason,
        allow_manifest_reuse=int(turn_policy.allow_manifest_reuse),
        skip_followup_resolution=int(turn_policy.skip_followup_resolution),
        previous_answer_publishability=previous_turn_contract.get("answer_publishability"),
        previous_followup_rights=previous_turn_contract.get("followup_rights"),
    )
    resolution_view_state = active_view_state
    if turn_policy.execution_path == "reuse_anchor":
        resolution_view_state = set_visible_answer_manifest(active_view_state, snapshot=None)
    elif turn_policy.execution_path in {"fresh_retrieval", "clarification"}:
        resolution_view_state = _clear_active_view_scope(active_view_state)
    visible_answer_manifest = getattr(resolution_view_state, "visible_answer_manifest", None)
    subject_index = getattr(resolution_view_state, "subject_index", {}) or {}
    scope_focus_entity = get_active_focus_entity(resolution_view_state)
    active_anchor_entity = get_active_child_anchor(resolution_view_state)
    followup_snapshot = visible_answer_manifest
    scope_decision = resolve_scope_decision(
        question=question,
        view_state=resolution_view_state,
        normalized_intent_base=normalized_intent_base,
        strategy_meta={
            "turn_policy": turn_policy.model_dump(),
            "turn_trigger": turn_trigger.model_dump(),
            "previous_turn_contract": previous_turn_contract,
        },
    )
    log_event(
        "SCOPE.DECISION",
        request_id=request_id,
        conversation_id=conversation_id,
        followup_type=scope_decision.followup_type,
        turn_execution_path=turn_policy.execution_path,
        reset_requested=int(bool(scope_decision.reset_requested)),
        needs_clarification=int(bool(scope_decision.needs_clarification)),
        resolved_anchor_source=getattr(scope_decision.resolved_anchor, "source", None),
        resolved_anchor_kind=getattr(scope_decision.resolved_anchor, "kind", None),
        clarification_type=((scope_decision.clarification_payload or {}).get("clarification_type") if scope_decision.clarification_payload else None),
        clarification_reason=((scope_decision.clarification_payload or {}).get("reason") if scope_decision.clarification_payload else None),
    )
    if scope_decision.reset_requested:
        active_view_state = _clear_active_view_scope(active_view_state)
        resolution_view_state = active_view_state
        visible_answer_manifest = None
        followup_snapshot = None
        subject_index = getattr(resolution_view_state, "subject_index", {}) or {}
        scope_focus_entity = None
        active_anchor_entity = None
        log_event(
            "ANCHOR.ESCAPED",
            request_id=request_id,
            conversation_id=conversation_id,
            reason="scope_reset",
        )
    if scope_decision.followup_type == "refinement_followup":
        log_event(
            "REFINEMENT.APPLIED",
            request_id=request_id,
            conversation_id=conversation_id,
            filters=scope_decision.refinement_filters,
        )
    anchor = None if turn_policy.skip_followup_resolution else scope_decision.resolved_anchor
    if turn_policy.execution_path == "clarification":
        followup_resolution = _build_policy_clarification_resolution(
            policy=turn_policy,
            trigger=turn_trigger,
            snapshot=followup_snapshot,
            question=question,
        )
    else:
        followup_resolution = _build_followup_resolution_from_anchor(anchor, followup_snapshot, question)
    if not turn_policy.skip_followup_resolution and not has_explicit_seed and source_reference_requested:
        followup_resolution = resolve_reference_context_followup(
            question=question,
            canonical_evidence=list(canonical_evidence or []),
            prev_context=prev_context,
            default_context_kind=base_route,
        )
        if str(followup_resolution.get("followup_resolution_status") or "").strip().lower() not in {"missing_context", "none"}:
            anchor = None
        else:
            if anchor is None:
                anchor = resolve_followup_anchor(
                    question=question,
                    normalized_intent=normalized_intent_base,
                    display_snapshot=followup_snapshot,
                    focus_entity=active_anchor_entity,
                    scope_focus_entity=scope_focus_entity,
                    subject_index=subject_index,
                    recent_mentions=get_recent_mentions(resolution_view_state),
                )
            if str(getattr(anchor, "source", "") or "").strip().lower() == "ambiguity_subject_index":
                followup_resolution = {
                    "followup_resolution_status": "clarification_required",
                    "selected_prev_item": None,
                    "seed_map": {},
                    "seed_source": None,
                    "explicit_followup": True,
                    "followup_reference_kind": "named_subject",
                    "requested_token": None,
                    "requested_index": None,
                    "available_count": len(getattr(followup_snapshot, "items", []) or []),
                    "anchor_source": "ambiguity_subject_index",
                    "focus_entity": None,
                    "clarification_type": "reference_ambiguity",
                    "clarification_reason": "multiple_named_subject_candidates",
                    "clarification_payload": {
                        "clarification_type": "reference_ambiguity",
                        "reason": "multiple_named_subject_candidates",
                    },
                }
                anchor = None
            elif anchor is not None:
                followup_resolution = _build_followup_resolution_from_anchor(anchor, followup_snapshot, question)
    elif not turn_policy.skip_followup_resolution and not has_explicit_seed and anchor is None:
        anchor = resolve_followup_anchor(
            question=question,
            normalized_intent=normalized_intent_base,
            display_snapshot=followup_snapshot,
            focus_entity=active_anchor_entity,
            scope_focus_entity=scope_focus_entity,
            subject_index=subject_index,
            recent_mentions=get_recent_mentions(resolution_view_state),
        )
        if str(getattr(anchor, "source", "") or "").strip().lower() == "ambiguity_subject_index":
            followup_resolution = {
                "followup_resolution_status": "clarification_required",
                "selected_prev_item": None,
                "seed_map": {},
                "seed_source": None,
                "explicit_followup": True,
                "followup_reference_kind": "named_subject",
                "requested_token": None,
                "requested_index": None,
                "available_count": len(getattr(followup_snapshot, "items", []) or []),
                "anchor_source": "ambiguity_subject_index",
                "focus_entity": None,
                "clarification_type": "reference_ambiguity",
                "clarification_reason": "multiple_named_subject_candidates",
                "clarification_payload": {
                    "clarification_type": "reference_ambiguity",
                    "reason": "multiple_named_subject_candidates",
                },
            }
            anchor = None
        else:
            followup_resolution = _build_followup_resolution_from_anchor(anchor, followup_snapshot, question)
        if anchor is None and str(followup_resolution.get("followup_resolution_status") or "").strip().lower() != "clarification_required":
            followup_resolution = resolve_reference_context_followup(
                question=question,
                canonical_evidence=list(canonical_evidence or []),
                prev_context=prev_context,
                default_context_kind=base_route,
            )
    # --- context_router fallback: clarification 직전에 recent_mentions 기반 복원 시도 ---
    context_router_decision: Optional[ContextRouterDecision] = None
    _recent_mentions = get_recent_mentions(resolution_view_state)
    if (
        anchor is None
        and scope_decision.needs_clarification
        and _recent_mentions
        and not has_explicit_seed
        and not turn_policy.skip_followup_resolution
        and str(followup_resolution.get("followup_resolution_status") or "").strip().lower() in {"none", "missing_context"}
    ):
        context_router_decision = route_context(
            question=question,
            recent_mentions=_recent_mentions,
            active_scope_summary={
                "scope_kind": getattr(getattr(resolution_view_state, "active_scope", None), "scope_kind", None),
                "has_focus": scope_focus_entity is not None,
                "has_snapshot": visible_answer_manifest is not None,
            },
        )
        if context_router_decision.status == "resolved" and context_router_decision.selected_candidate_index is not None:
            selected_mention = _recent_mentions[context_router_decision.selected_candidate_index]
            # FocusEntity로 변환하여 anchor 확정 — _apply_resolved_anchor_seed만 수행
            from apps.conversation.followup_anchor import focus_entity_from_mention
            anchor = focus_entity_from_mention(selected_mention)
            followup_resolution = _build_followup_resolution_from_anchor(anchor, followup_snapshot, question)
            log_event(
                "CONTEXT_ROUTER.RESOLVED",
                request_id=request_id,
                conversation_id=conversation_id,
                source=context_router_decision.source,
                confidence=context_router_decision.confidence,
                reason=context_router_decision.reason,
                selected_index=context_router_decision.selected_candidate_index,
                entity_kind=selected_mention.entity_kind,
                pjt_id=selected_mention.pjt_id,
                pjt_no=selected_mention.pjt_no,
                rewritten_query_hint=context_router_decision.rewritten_query_hint,
                recent_mention_count=len(_recent_mentions),
                clarification_avoided=True,
            )
        else:
            log_event(
                "CONTEXT_ROUTER.FALLBACK",
                request_id=request_id,
                conversation_id=conversation_id,
                status=context_router_decision.status,
                source=context_router_decision.source,
                reason=context_router_decision.reason,
                recent_mention_count=len(_recent_mentions),
                clarification_avoided=False,
            )

    if (
        anchor is None
        and scope_decision.needs_clarification
        and str(followup_resolution.get("followup_resolution_status") or "").strip().lower() in {"none", "missing_context"}
    ):
        clarification_payload = dict(scope_decision.clarification_payload or {})
        followup_resolution = {
            "followup_resolution_status": "clarification_required",
            "selected_prev_item": None,
            "seed_map": {},
            "seed_source": None,
            "explicit_followup": True,
            "followup_reference_kind": "child_entity" if scope_decision.followup_type == "child_entity_followup" else scope_decision.followup_type,
            "requested_token": None,
            "requested_index": None,
            "available_count": len(getattr(followup_snapshot, "items", []) or []),
            "anchor_source": None,
            "focus_entity": None,
            "candidate_items": list(clarification_payload.get("candidates") or []),
            "clarification_type": clarification_payload.get("clarification_type"),
            "clarification_reason": clarification_payload.get("reason"),
            "clarification_payload": clarification_payload,
        }
    if (
        anchor is None
        and not has_explicit_seed
        and str(followup_resolution.get("followup_resolution_status") or "").strip().lower() != "resolved"
        and scope_decision.needs_clarification
    ):
        log_event(
            "ANCHOR.AMBIGUOUS",
            request_id=request_id,
            conversation_id=conversation_id,
            followup_type=scope_decision.followup_type,
            reason=((scope_decision.clarification_payload or {}).get("reason") if scope_decision.clarification_payload else None),
        )

    if anchor is not None:
        normalized_intent_base = _apply_resolved_anchor_seed(normalized_intent_base, anchor)
        normalized_intent_base = _apply_followup_context_lock(normalized_intent_base, followup_resolution)
        normalized_intent_base = _apply_followup_subject_hint(
            normalized_intent_base,
            anchor=anchor,
            followup_resolution=followup_resolution,
        )
        log_event(
            "FOLLOWUP.ANCHOR.RESOLVED",
            request_id=request_id,
            conversation_id=conversation_id,
            source=anchor.source,

            view_id=getattr(anchor, "view_id", None),

            requested_ordinal=parse_ordinal_reference(question),

            resolved_display_rank=getattr(anchor, "display_rank", None),

            pjt_id=getattr(anchor, "pjt_id", None),

            pjt_no=getattr(anchor, "pjt_no", None),

            rst_id=getattr(anchor, "rst_id", None),

            person_no=getattr(anchor, "person_no", None),
            org_id=getattr(anchor, "org_id", None),
        )
        if str(getattr(anchor, "source", "") or "").strip().lower() == "detail_participant_match":
            log_event(
                "FOLLOWUP.PARTICIPANT_ANCHOR.RESOLVED",
                request_id=request_id,
                conversation_id=conversation_id,
                source=anchor.source,
                person_no=getattr(anchor, "person_no", None),
                person_name=getattr(anchor, "title_text", None),
                source_project_pjt_id=getattr(anchor, "pjt_id", None),
                source_project_pjt_no=getattr(anchor, "pjt_no", None),
            )
        normalized_intent_base, question_analysis = _coerce_project_anchor_role_followup(
            normalized_intent_base,
            question_analysis,
            question=question,
            followup_resolution=followup_resolution,
            request_id=request_id,
            conversation_id=conversation_id,
        )
        normalized_intent_base, question_analysis = _coerce_child_anchor_detail_followup(
            normalized_intent_base,
            question_analysis,
            anchor=anchor,
            question=question,
            followup_resolution=followup_resolution,
            request_id=request_id,
            conversation_id=conversation_id,
        )
    elif not has_explicit_seed and str(followup_resolution.get("followup_resolution_status") or "") == "resolved":
        normalized_intent_base = _apply_anchor_lock(normalized_intent_base, followup_resolution.get("seed_map") or {})
        normalized_intent_base = _apply_followup_context_lock(normalized_intent_base, followup_resolution)
        normalized_intent_base = _apply_followup_subject_hint(
            normalized_intent_base,
            followup_resolution=followup_resolution,
        )
        normalized_intent_base, question_analysis = _coerce_project_anchor_role_followup(
            normalized_intent_base,
            question_analysis,
            question=question,
            followup_resolution=followup_resolution,
            request_id=request_id,
            conversation_id=conversation_id,
        )
        normalized_intent_base, question_analysis = _coerce_child_anchor_detail_followup(
            normalized_intent_base,
            question_analysis,
            question=question,
            followup_resolution=followup_resolution,
            request_id=request_id,
            conversation_id=conversation_id,
        )
    force_planner_for_explicit_seed = bool(
        has_explicit_seed
        and _should_force_planner_for_explicit_seed(
            question=question,
            normalized_intent=normalized_intent_base,
            anchor=anchor,
            followup_resolution=followup_resolution,
        )
    )
    if force_planner_for_explicit_seed:
        log_event(
            "PLANNER.EXPLICIT_SEED.OVERRIDE",
            request_id=request_id,
            conversation_id=conversation_id,
            reason="subject_axis_or_broad_history",
            followup_reference_kind=followup_resolution.get("followup_reference_kind"),
            anchor_source=getattr(anchor, "source", None),
            has_subject_axis_request=int(_has_subject_axis_request(question, normalized_intent_base)),
            has_broad_history_or_list_cue=int(_has_broad_history_or_list_cue(question)),
        )
    if not has_explicit_seed or force_planner_for_explicit_seed:
        question_analysis = await run_question_analysis(
            question=question,
            conversation_id=conversation_id,
            chat_history=chat_history,

            prev_context=prev_context,

            canonical_evidence=list(canonical_evidence or []),

            view_state=resolution_view_state,

            request_id=request_id,

            normalized_intent_base=normalized_intent_base,

        )

        planner_failed = int(float(getattr(question_analysis, "confidence", 0.0) or 0.0) <= 0.0)

    if question_analysis is not None:

        normalized_intent_base, question_analysis = _coerce_project_anchor_role_followup(

            normalized_intent_base,

            question_analysis,

            question=question,

            followup_resolution=followup_resolution,


            request_id=request_id,

            conversation_id=conversation_id,

        )
        normalized_intent_base, question_analysis = _coerce_child_anchor_detail_followup(

            normalized_intent_base,

            question_analysis,

            anchor=anchor,

            question=question,

            followup_resolution=followup_resolution,


            request_id=request_id,

            conversation_id=conversation_id,

        )

        count_validation = _resolve_question_analysis_count(

            question_analysis,

            question=question,


            request_id=request_id,

            conversation_id=conversation_id,

        )
    else:
        count_validation = {}

    normalized_intent, planner_applied = apply_question_analysis_v3(
        normalized_intent_base,
        question_analysis,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    # Planner merge is the only final writer of route truth.
    # Post-merge facade code may preserve anchor ids, but must not relock base_route/target_cols.
    normalized_intent = (
        _apply_resolved_anchor_seed(normalized_intent, anchor)
        if anchor is not None
        else _apply_anchor_lock(
            normalized_intent,
            followup_resolution.get("seed_map") or {},
        )
    )
    normalized_intent = _apply_followup_context_lock(normalized_intent, followup_resolution)
    normalized_intent = _apply_followup_subject_hint(
        normalized_intent,
        anchor=anchor,
        followup_resolution=followup_resolution,
    )
    normalized_intent, question_analysis = _coerce_project_anchor_role_followup(
        normalized_intent,
        question_analysis,
        question=question,
        followup_resolution=followup_resolution,


        request_id=request_id,

        conversation_id=conversation_id,

    )
    normalized_intent, question_analysis = _coerce_child_anchor_detail_followup(
        normalized_intent,
        question_analysis,
        anchor=anchor,
        question=question,
        followup_resolution=followup_resolution,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    turn_contract = _build_turn_contract(
        normalized_intent=normalized_intent,
        question_analysis=question_analysis,
        count_validation=count_validation,
    )

    log_event(

        "PLANNER.PIPELINE",

        request_id=request_id,

        conversation_id=conversation_id,

        step="intent_build",

        status="success",

        planner_applied=int(planner_applied),

        planner_failed=int(planner_failed),

        planner_stagewise_enabled=int(True),
        planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
        planner_stage15_prompt_version=PLANNER_STAGE15_PROMPT_VERSION,
        planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
        schema_fields=["intent_payload_version", "normalized_intent", "question_analysis", "strategy_meta"],
    )
    # context_router 메타데이터를 followup_resolution에 추가 (strategy_meta에 전파)
    if context_router_decision is not None:
        followup_resolution["context_router_status"] = context_router_decision.status
        followup_resolution["context_router_source"] = context_router_decision.source
        followup_resolution["context_router_confidence"] = context_router_decision.confidence
        followup_resolution["recent_mention_count"] = len(_recent_mentions)
        followup_resolution["rewritten_query_hint"] = context_router_decision.rewritten_query_hint

    return (
        _build_intent_payload_object(
            normalized_intent,
            question_analysis,
            followup_resolution=followup_resolution,
            turn_id=turn_id,
            turn_trigger=turn_trigger.model_dump(),
            turn_contract=turn_contract,
            turn_policy=turn_policy.model_dump(),
            count_validation=count_validation,
        ),
        question_analysis,
    )
