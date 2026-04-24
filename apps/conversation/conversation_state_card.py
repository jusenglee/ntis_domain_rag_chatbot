from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from apps.conversation.session_memory import (
    ClarificationContext,
    DetailAnchorContext,
    EmptyContext,
    PublishedManifestContext,
    SessionMemory,
    SubjectQueryContext,
    empty_session_memory,
)
from apps.conversation.view_state import ConversationViewState, DisplaySnapshot


class ConversationStateCard(BaseModel):
    """LLM-readable projection of the official conversation memory."""

    current_context_type: str = "empty"
    current_subject_kind: Optional[str] = None
    current_subject_name: Optional[str] = None
    current_subject_identity_status: Optional[str] = None
    current_subject_ids_map: Dict[str, List[str]] = Field(default_factory=dict)
    refinement_allowed: bool = False
    result_kind: Optional[str] = None
    last_user_task: Optional[str] = None
    last_result_summary: Optional[str] = None
    last_publication_status: Optional[str] = None
    unresolved_question: Optional[str] = None
    unresolved_constraints: Dict[str, Any] = Field(default_factory=dict)
    manifest_summary: Optional[str] = None
    anchor_summary: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


def _manifest_summary(snapshot: Optional[DisplaySnapshot]) -> Optional[str]:
    if not isinstance(snapshot, DisplaySnapshot):
        return None
    titles: list[str] = []
    for item in list(snapshot.items or [])[:3]:
        title = str(getattr(item, "title_text", "") or "").strip()
        if title:
            titles.append(title)
    if not titles:
        return f"{snapshot.visible_count} visible {snapshot.context_kind} items"
    return f"{snapshot.visible_count} visible {snapshot.context_kind} items: " + "; ".join(titles)


def _anchor_summary(anchor: Any) -> Optional[str]:
    if anchor is None:
        return None
    title = str(getattr(anchor, "title_text", "") or "").strip()
    kind = str(getattr(anchor, "kind", "") or "").strip()
    key = (
        getattr(anchor, "pjt_id", None)
        or getattr(anchor, "pjt_no", None)
        or getattr(anchor, "rst_id", None)
        or getattr(anchor, "person_no", None)
        or getattr(anchor, "org_id", None)
    )
    parts = [part for part in (title, kind, str(key or "").strip()) if part]
    return " / ".join(parts) if parts else None


def _selected_publication_status(selected_answer_meta: Optional[Dict[str, Any]]) -> Optional[str]:
    meta = dict(selected_answer_meta or {})
    return (
        str(meta.get("publication_status") or "").strip()
        or str(meta.get("answer_publishability") or "").strip()
        or None
    )


def build_conversation_state_card_model(
    *,
    session_memory: Optional[SessionMemory],
    view_state: Optional[ConversationViewState] = None,
    selected_answer_meta: Optional[Dict[str, Any]] = None,
) -> ConversationStateCard:
    session = session_memory if isinstance(session_memory, SessionMemory) else empty_session_memory()
    context = session.current_context
    publication_status = _selected_publication_status(selected_answer_meta)

    if isinstance(context, SubjectQueryContext):
        return ConversationStateCard(
            current_context_type=context.context_type,
            current_subject_kind=context.subject_kind,
            current_subject_name=context.subject_name,
            current_subject_identity_status=context.identity_status,
            current_subject_ids_map=dict(context.subject_ids_map or {}),
            refinement_allowed=bool(context.followup_rights.refinement_allowed),
            result_kind=context.result_kind,
            last_publication_status=publication_status or context.publication_status,
            manifest_summary=_manifest_summary(context.result_manifest),
        )

    if isinstance(context, ClarificationContext):
        return ConversationStateCard(
            current_context_type=context.context_type,
            refinement_allowed=False,
            last_publication_status=publication_status,
            unresolved_question=context.unresolved_question,
            unresolved_constraints=dict(context.unresolved_constraints or {}),
            warnings=[str(context.reason)] if context.reason else [],
        )

    if isinstance(context, PublishedManifestContext):
        return ConversationStateCard(
            current_context_type=context.context_type,
            refinement_allowed=False,
            result_kind=context.result_kind,
            last_publication_status=publication_status,
            manifest_summary=_manifest_summary(context.result_manifest),
        )

    if isinstance(context, DetailAnchorContext):
        return ConversationStateCard(
            current_context_type=context.context_type,
            refinement_allowed=False,
            last_publication_status=publication_status,
            anchor_summary=_anchor_summary(context.anchor),
        )

    if isinstance(context, EmptyContext):
        return ConversationStateCard(
            current_context_type=context.context_type,
            refinement_allowed=False,
            last_publication_status=publication_status,
        )

    return ConversationStateCard(last_publication_status=publication_status)


def _format_constraints(constraints: Dict[str, Any]) -> str:
    parts = []
    for key in sorted(constraints):
        value = constraints[key]
        if value not in (None, "", [], {}):
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def render_conversation_state_card(card: ConversationStateCard) -> str:
    lines = ["[Conversation State Card]"]
    lines.append(f"current_context_type: {card.current_context_type}")

    if card.current_subject_name:
        lines.append(f"current subject: {card.current_subject_name} ({card.current_subject_kind or 'unknown'})")
    else:
        lines.append("current subject: none confirmed")
    if card.current_subject_identity_status:
        lines.append(f"current subject identity: {card.current_subject_identity_status}")

    lines.append(f"refinement_allowed: {'yes' if card.refinement_allowed else 'no'}")

    if card.current_subject_ids_map:
        id_parts = []
        for key in sorted(card.current_subject_ids_map):
            values = [str(value) for value in card.current_subject_ids_map.get(key, []) if value]
            if values:
                id_parts.append(f"{key}={','.join(values)}")
        if id_parts:
            lines.append(f"current_subject_ids_map: {'; '.join(id_parts)}")

    if card.result_kind:
        lines.append(f"result_kind: {card.result_kind}")
    if card.last_publication_status:
        lines.append(f"last_publication_status: {card.last_publication_status}")
    if card.manifest_summary:
        lines.append(f"manifest_summary: {card.manifest_summary}")
    if card.anchor_summary:
        lines.append(f"anchor_summary: {card.anchor_summary}")
    if card.unresolved_question:
        lines.append(f"unresolved_question: {card.unresolved_question}")
    if card.unresolved_constraints:
        lines.append(f"unresolved_constraints: {_format_constraints(card.unresolved_constraints)}")
    if card.warnings:
        lines.append("warnings: " + "; ".join(str(item) for item in card.warnings if item))

    return "\n".join(lines)


def build_conversation_state_card(
    *,
    session_memory: Optional[SessionMemory],
    view_state: Optional[ConversationViewState] = None,
    selected_answer_meta: Optional[Dict[str, Any]] = None,
) -> str:
    return render_conversation_state_card(
        build_conversation_state_card_model(
            session_memory=session_memory,
            view_state=view_state,
            selected_answer_meta=selected_answer_meta,
        )
    )
