from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter

from apps.conversation.view_state import (
    ActiveScope,
    ConversationViewState,
    DetailCacheEntry,
    DisplaySnapshot,
    FocusEntity,
    recent_mention_from_display_item,
    recent_mention_from_focus_entity,
)


class FollowupRights(BaseModel):
    ordinal_allowed: bool = False
    source_allowed: bool = False
    refinement_allowed: bool = False


class EmptyContext(BaseModel):
    context_type: Literal["empty"] = "empty"
    followup_rights: FollowupRights = Field(default_factory=FollowupRights)


class PublishedManifestContext(BaseModel):
    context_type: Literal["published_manifest"] = "published_manifest"
    result_kind: str = "project"
    result_manifest: DisplaySnapshot
    followup_rights: FollowupRights = Field(
        default_factory=lambda: FollowupRights(ordinal_allowed=True, source_allowed=True)
    )


class DetailAnchorContext(BaseModel):
    context_type: Literal["detail_anchor"] = "detail_anchor"
    anchor: FocusEntity
    followup_rights: FollowupRights = Field(default_factory=FollowupRights)


class SubjectQueryContext(BaseModel):
    context_type: Literal["subject_query"] = "subject_query"
    subject_kind: str
    subject_name: str
    subject_ids_map: Dict[str, List[str]] = Field(default_factory=dict)
    result_kind: str = "project"
    result_manifest: Optional[DisplaySnapshot] = None
    followup_rights: FollowupRights = Field(
        default_factory=lambda: FollowupRights(refinement_allowed=True)
    )


class ClarificationContext(BaseModel):
    context_type: Literal["clarification"] = "clarification"
    reason: Optional[str] = None
    followup_rights: FollowupRights = Field(default_factory=FollowupRights)


CurrentContext = Annotated[
    Union[
        EmptyContext,
        PublishedManifestContext,
        DetailAnchorContext,
        SubjectQueryContext,
        ClarificationContext,
    ],
    Field(discriminator="context_type"),
]

_CURRENT_CONTEXT_ADAPTER = TypeAdapter(CurrentContext)


class SessionMemory(BaseModel):
    schema_version: int = 3
    history_log: List[Dict[str, str]] = Field(default_factory=list)
    current_context: CurrentContext = Field(default_factory=EmptyContext)
    entity_memory: Dict[str, Any] = Field(default_factory=dict)
    detail_cache: Dict[str, DetailCacheEntry] = Field(default_factory=dict)
    turn_journal_tail: List[Dict[str, Any]] = Field(default_factory=list)
    canonical_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    render_profile: Dict[str, Any] = Field(default_factory=dict)


def empty_session_memory() -> SessionMemory:
    return SessionMemory()


def load_session_memory(payload: Any) -> SessionMemory:
    if isinstance(payload, SessionMemory):
        return payload
    if isinstance(payload, dict):
        try:
            return SessionMemory.model_validate(payload)
        except Exception:
            return empty_session_memory()
    return empty_session_memory()


def load_current_context(payload: Any) -> CurrentContext:
    if payload is None:
        return EmptyContext()
    try:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        return _CURRENT_CONTEXT_ADAPTER.validate_python(payload)
    except Exception:
        return EmptyContext()


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, (list, tuple, set)):
            nested = _first_text(*list(value))
            if nested:
                return nested
            continue
        text = str(value or "").strip()
        if text:
            return text
    return None


def _as_payload(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _normalized_intent_payload(intent_payload: Any) -> Dict[str, Any]:
    normalized_intent = getattr(intent_payload, "normalized_intent", None)
    return _as_payload(normalized_intent)


def _subject_seed_from_intent(intent_payload: Any) -> tuple[Optional[str], Optional[str]]:
    payload = _normalized_intent_payload(intent_payload)
    people = _first_text(payload.get("people_terms"))
    if people:
        return "people", people
    org = _first_text(
        payload.get("org_terms"),
        payload.get("lead_org_terms"),
        payload.get("participant_org_terms"),
        payload.get("people_affiliation_org_terms"),
    )
    if org:
        return "org", org
    return None, None


def _ids_map_from_focus(focus: FocusEntity) -> Dict[str, List[str]]:
    ids: Dict[str, List[str]] = {}
    for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn"):
        value = _first_text(getattr(focus, key, None))
        if value:
            ids[key] = [value]
    return ids


def _rights_for_manifest(followup_rights: str) -> FollowupRights:
    text = str(followup_rights or "").strip().lower()
    return FollowupRights(
        ordinal_allowed=text in {"ordinal_allowed", "source_allowed"},
        source_allowed=text == "source_allowed",
        refinement_allowed=False,
    )


def _followup_rights_label(rights: Optional[FollowupRights]) -> str:
    if not isinstance(rights, FollowupRights):
        return "none"
    if rights.source_allowed:
        return "source_allowed"
    if rights.ordinal_allowed:
        return "ordinal_allowed"
    return "none"


def _snapshot_visible_count(snapshot: Optional[DisplaySnapshot]) -> int:
    if not isinstance(snapshot, DisplaySnapshot):
        return 0
    explicit_count = int(getattr(snapshot, "visible_count", 0) or 0)
    if explicit_count > 0:
        return explicit_count
    return len(list(getattr(snapshot, "items", []) or []))


def current_context_summary(memory: Optional[SessionMemory]) -> Dict[str, Any]:
    session = memory if isinstance(memory, SessionMemory) else empty_session_memory()
    context = session.current_context
    base: Dict[str, Any] = {
        "memory_source": "current_context",
        "current_context_type": getattr(context, "context_type", "empty"),
        "has_visible_answer_manifest": False,
        "manifest_visible_count": 0,
        "has_active_focus": False,
        "has_child_anchor": False,
        "subject_index_count": 0,
        "recent_mention_count": 0,
        "last_turn_kind": None,
        "last_answer_publishability": None,
        "last_followup_rights": None,
        "refinement_allowed": False,
        "subject_kind": None,
        "subject_name": None,
    }

    if isinstance(context, PublishedManifestContext):
        return {
            **base,
            "has_visible_answer_manifest": True,
            "manifest_visible_count": _snapshot_visible_count(context.result_manifest),
            "recent_mention_count": _snapshot_visible_count(context.result_manifest),
            "last_turn_kind": "list",
            "last_answer_publishability": "publishable",
            "last_followup_rights": _followup_rights_label(context.followup_rights),
            "result_kind": context.result_kind,
        }

    if isinstance(context, SubjectQueryContext):
        refinement_allowed = bool(context.followup_rights.refinement_allowed)
        return {
            **base,
            "has_active_focus": True,
            "subject_index_count": 1,
            "recent_mention_count": 1,
            "last_turn_kind": "subject_query",
            "last_answer_publishability": "publishable" if refinement_allowed else "blocked",
            "last_followup_rights": "none",
            "refinement_allowed": refinement_allowed,
            "subject_kind": context.subject_kind,
            "subject_name": context.subject_name,
            "result_kind": context.result_kind,
        }

    if isinstance(context, DetailAnchorContext):
        return {
            **base,
            "has_active_focus": True,
            "recent_mention_count": 1,
            "last_turn_kind": "detail",
            "last_answer_publishability": "publishable",
            "last_followup_rights": "none",
            "anchor_entity_kind": context.anchor.kind,
            "anchor_reuse_allowed": True,
        }

    if isinstance(context, ClarificationContext):
        return {
            **base,
            "last_turn_kind": "clarification",
            "last_answer_publishability": "blocked",
            "last_followup_rights": "none",
            "clarification_reason": context.reason,
        }

    return base


def current_context_followup_contract(memory: Optional[SessionMemory]) -> Dict[str, Any]:
    session = memory if isinstance(memory, SessionMemory) else empty_session_memory()
    context = session.current_context
    context_type = getattr(context, "context_type", "empty")
    contract: Dict[str, Any] = {
        "memory_source": "current_context",
        "context_type": context_type,
        "previous_publishability": "not_applicable",
        "previous_followup_rights": "none",
        "has_manifest": False,
        "has_anchor_context": False,
        "anchor_reuse_allowed": False,
        "refinement_allowed": False,
    }

    if isinstance(context, PublishedManifestContext):
        contract.update(
            {
                "previous_publishability": "publishable",
                "previous_followup_rights": _followup_rights_label(context.followup_rights),
                "has_manifest": True,
            }
        )
    elif isinstance(context, SubjectQueryContext):
        contract.update(
            {
                "previous_publishability": (
                    "publishable" if context.followup_rights.refinement_allowed else "blocked"
                ),
                "previous_followup_rights": "none",
                "refinement_allowed": bool(context.followup_rights.refinement_allowed),
                "subject_kind": context.subject_kind,
                "subject_name": context.subject_name,
            }
        )
    elif isinstance(context, DetailAnchorContext):
        contract.update(
            {
                "previous_publishability": "publishable",
                "previous_followup_rights": "none",
                "has_anchor_context": True,
                "anchor_reuse_allowed": True,
                "anchor_entity_kind": context.anchor.kind,
            }
        )
    elif isinstance(context, ClarificationContext):
        contract.update(
            {
                "previous_publishability": "blocked",
                "previous_followup_rights": "none",
            }
        )

    return contract


def build_current_context(
    *,
    view_state: Optional[ConversationViewState],
    selected_answer_meta: Optional[Dict[str, Any]] = None,
    intent_payload: Any = None,
) -> CurrentContext:
    if view_state is None:
        return EmptyContext()

    answer_meta = dict(selected_answer_meta or {})
    publishability = str(answer_meta.get("answer_publishability") or "").strip().lower()
    visible_snapshot = getattr(view_state, "visible_answer_manifest", None)
    active_scope = getattr(view_state, "active_scope", None)
    active_snapshot = getattr(active_scope, "result_set", None)
    focus = getattr(active_scope, "focus", None) or getattr(active_scope, "child_anchor", None)
    contract = dict(getattr(view_state, "last_query_contract", {}) or {})
    followup_rights = str(contract.get("followup_rights") or "source_allowed").strip().lower()
    subject_kind, subject_name = _subject_seed_from_intent(intent_payload)

    if publishability == "publishable" and subject_kind and subject_name:
        manifest = active_snapshot if isinstance(active_snapshot, DisplaySnapshot) else visible_snapshot
        return SubjectQueryContext(
            subject_kind=subject_kind,
            subject_name=subject_name,
            result_kind=str(getattr(manifest, "context_kind", None) or contract.get("context_kind") or "project"),
            result_manifest=manifest,
            followup_rights=FollowupRights(refinement_allowed=True),
        )

    if publishability == "publishable" and isinstance(visible_snapshot, DisplaySnapshot):
        return PublishedManifestContext(
            result_kind=str(getattr(visible_snapshot, "context_kind", None) or contract.get("context_kind") or "project"),
            result_manifest=visible_snapshot,
            followup_rights=_rights_for_manifest(followup_rights),
        )

    if publishability == "publishable" and isinstance(focus, FocusEntity):
        return DetailAnchorContext(anchor=focus)

    if answer_meta.get("clarification") or answer_meta.get("answer_kind") == "clarification":
        return ClarificationContext(reason=str(answer_meta.get("clarification_reason") or "clarification"))

    return EmptyContext()


def build_session_memory(
    *,
    history_log: List[Dict[str, str]],
    canonical_evidence: Any,
    render_profile: Any,
    view_state: Optional[ConversationViewState],
    selected_answer_meta: Optional[Dict[str, Any]] = None,
    intent_payload: Any = None,
    current_context: Any = None,
    turn_journal_tail: Optional[List[Dict[str, Any]]] = None,
) -> SessionMemory:
    normalized_view_state = view_state if isinstance(view_state, ConversationViewState) else ConversationViewState()
    resolved_current_context = load_current_context(current_context)
    return SessionMemory(
        history_log=list(history_log or []),
        current_context=resolved_current_context,
        entity_memory={},
        detail_cache=dict(getattr(normalized_view_state, "detail_cache", {}) or {}),
        turn_journal_tail=list(turn_journal_tail or [])[-20:],
        canonical_evidence=[item for item in (canonical_evidence or []) if isinstance(item, dict)],
        render_profile=dict(render_profile or {}) if isinstance(render_profile, dict) else {},
    )


def view_state_from_current_context(memory: Optional[SessionMemory]) -> ConversationViewState:
    session = memory if isinstance(memory, SessionMemory) else empty_session_memory()
    context = session.current_context
    view_state = ConversationViewState(detail_cache=dict(session.detail_cache or {}))

    if isinstance(context, PublishedManifestContext):
        mentions = [
            recent_mention_from_display_item(item, source="list_snapshot", turn_index=idx)
            for idx, item in enumerate(context.result_manifest.items[:12])
        ]
        return view_state.model_copy(
            update={
                "visible_answer_manifest": context.result_manifest,
                "active_scope": ActiveScope(result_set=context.result_manifest, scope_kind="list"),
                "active_result_set_kind": context.result_kind,
                "active_result_view_id": context.result_manifest.view_id,
                "recent_mentions": mentions,
                "last_query_contract": {
                    "turn_kind": "list",
                    "context_kind": context.result_kind,
                    "answer_publishability": "publishable",
                    "followup_rights": "source_allowed" if context.followup_rights.source_allowed else "ordinal_allowed",
                },
            }
        )

    if isinstance(context, SubjectQueryContext):
        focus = FocusEntity(
            kind=context.subject_kind,
            source="subject_query_context",
            title_text=context.subject_name,
            person_no=_first_text(context.subject_ids_map.get("person_no")),
            org_id=_first_text(context.subject_ids_map.get("org_id")),
            org_code=_first_text(context.subject_ids_map.get("org_code")),
            biz_no=_first_text(context.subject_ids_map.get("biz_no")),
        )
        mentions = [recent_mention_from_focus_entity(focus, source="detail_focus")]
        return view_state.model_copy(
            update={
                "active_scope": ActiveScope(focus=focus, scope_kind="detail"),
                "recent_mentions": mentions,
                "last_query_contract": {
                    "turn_kind": "subject_query",
                    "context_kind": context.result_kind,
                    "subject_kind": context.subject_kind,
                    "subject_name": context.subject_name,
                    "answer_publishability": "publishable" if context.followup_rights.refinement_allowed else "blocked",
                    "followup_rights": "none",
                    "refinement_allowed": bool(context.followup_rights.refinement_allowed),
                },
            }
        )

    if isinstance(context, DetailAnchorContext):
        return view_state.model_copy(
            update={
                "active_scope": ActiveScope(focus=context.anchor, scope_kind="detail"),
                "recent_mentions": [recent_mention_from_focus_entity(context.anchor, source="detail_focus")],
                "last_query_contract": {
                    "turn_kind": "detail",
                    "context_kind": context.anchor.kind,
                    "answer_publishability": "publishable",
                    "followup_rights": "none",
                },
            }
        )

    return view_state
