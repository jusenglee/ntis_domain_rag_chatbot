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
from apps.conversation.view_state import (
    ConversationViewState,
    DisplaySnapshot,
    FocusEntity,
)


class ConversationStateCard(BaseModel):
    """LLM-readable projection of the official conversation truth."""

    current_context_type: str = "empty"
    current_subject_kind: Optional[str] = None
    current_subject_name: Optional[str] = None
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


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _clean_ids_map(value: Any) -> Dict[str, List[str]]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for raw_key, raw_values in value.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        values = raw_values if isinstance(raw_values, (list, tuple, set)) else [raw_values]
        cleaned: List[str] = []
        seen: set[str] = set()
        for item in values:
            text = _first_text(item)
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        if cleaned:
            out[key] = cleaned
    return out


def _summarize_snapshot(snapshot: Optional[DisplaySnapshot], *, max_items: int = 5) -> Optional[str]:
    if not isinstance(snapshot, DisplaySnapshot):
        return None
    parts = [
        f"kind={snapshot.context_kind}",
        f"visible={snapshot.visible_count}",
        f"raw={snapshot.raw_count}",
    ]
    item_bits: List[str] = []
    for item in list(snapshot.items or [])[:max_items]:
        title = _first_text(getattr(item, "title_text", None), getattr(item, "doc_id", None), "item")
        rank = getattr(item, "display_rank", None)
        kind = _first_text(getattr(item, "entity_kind", None), snapshot.context_kind)
        if rank is not None:
            item_bits.append(f"{rank}. {title} ({kind})")
        else:
            item_bits.append(f"{title} ({kind})")
    if item_bits:
        parts.append("items=" + "; ".join(item_bits))
    return " | ".join(parts)


def _summarize_anchor(anchor: Optional[FocusEntity]) -> Optional[str]:
    if not isinstance(anchor, FocusEntity):
        return None
    pieces = [
        f"kind={_first_text(anchor.kind, 'unknown')}",
        f"name={_first_text(anchor.title_text, anchor.doc_id, anchor.pjt_id, anchor.rst_id, 'unknown')}",
    ]
    for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no"):
        value = _first_text(getattr(anchor, key, None))
        if value:
            pieces.append(f"{key}={value}")
    return " | ".join(pieces)


def _last_user_task(selected_answer_meta: Optional[Dict[str, Any]]) -> Optional[str]:
    meta = dict(selected_answer_meta or {})
    return _first_text(meta.get("answer_kind"), meta.get("answer_source"), meta.get("selected_answer_kind"))


def build_conversation_state_card_model(
    *,
    session_memory: Optional[SessionMemory],
    view_state: Optional[ConversationViewState] = None,
    selected_answer_meta: Optional[Dict[str, Any]] = None,
) -> ConversationStateCard:
    """Build the structured state card model from SessionMemory.current_context."""

    session = session_memory if isinstance(session_memory, SessionMemory) else empty_session_memory()
    context = session.current_context
    meta = dict(selected_answer_meta or {})
    last_task = _last_user_task(meta)
    answer_publishability = _first_text(meta.get("answer_publishability"), meta.get("publication_status"))

    if isinstance(context, SubjectQueryContext):
        manifest_summary = _summarize_snapshot(context.result_manifest)
        retained_status = _first_text(getattr(context, "publication_status", None), answer_publishability)
        return ConversationStateCard(
            current_context_type=context.context_type,
            current_subject_kind=context.subject_kind,
            current_subject_name=context.subject_name,
            current_subject_ids_map=_clean_ids_map(context.subject_ids_map),
            refinement_allowed=bool(context.followup_rights.refinement_allowed),
            result_kind=context.result_kind,
            last_user_task=last_task,
            last_result_summary=manifest_summary,
            last_publication_status=retained_status or "answer_published",
            manifest_summary=manifest_summary,
        )

    if isinstance(context, ClarificationContext):
        return ConversationStateCard(
            current_context_type=context.context_type,
            refinement_allowed=False,
            last_user_task=last_task,
            last_publication_status="clarification_pending",
            unresolved_question=context.unresolved_question,
            unresolved_constraints=dict(context.unresolved_constraints or {}),
            warnings=[context.reason] if context.reason else [],
        )

    if isinstance(context, PublishedManifestContext):
        manifest_summary = _summarize_snapshot(context.result_manifest)
        return ConversationStateCard(
            current_context_type=context.context_type,
            refinement_allowed=False,
            result_kind=context.result_kind,
            last_user_task=last_task,
            last_result_summary=manifest_summary,
            last_publication_status=answer_publishability or "answer_published",
            manifest_summary=manifest_summary,
        )

    if isinstance(context, DetailAnchorContext):
        anchor_summary = _summarize_anchor(context.anchor)
        return ConversationStateCard(
            current_context_type=context.context_type,
            refinement_allowed=False,
            result_kind=getattr(context.anchor, "kind", None),
            last_user_task=last_task,
            last_result_summary=anchor_summary,
            last_publication_status=answer_publishability or "answer_published",
            anchor_summary=anchor_summary,
        )

    warnings: List[str] = []
    if isinstance(view_state, ConversationViewState):
        focus = getattr(getattr(view_state, "active_scope", None), "focus", None)
        if isinstance(focus, FocusEntity):
            warnings.append("view_state_focus_available_but_not_official_truth")
    if not isinstance(context, EmptyContext):
        warnings.append("unknown_current_context_type")
    return ConversationStateCard(
        current_context_type=getattr(context, "context_type", "empty"),
        last_user_task=last_task,
        last_publication_status=answer_publishability,
        warnings=warnings,
    )


def render_conversation_state_card(card: ConversationStateCard) -> str:
    """Render a compact text card for the dialogue agent prompt."""

    lines = ["[Conversation State Card]"]
    lines.append(f"current_context_type: {card.current_context_type}")
    if card.current_subject_name:
        lines.append(f"current subject: {card.current_subject_name} ({card.current_subject_kind or 'unknown'})")
    else:
        lines.append("current subject: none confirmed")
    lines.append(f"refinement_allowed: {'yes' if card.refinement_allowed else 'no'}")
    if card.result_kind:
        lines.append(f"result_kind: {card.result_kind}")
    if card.current_subject_ids_map:
        id_parts = []
        for key in sorted(card.current_subject_ids_map):
            values = ", ".join(card.current_subject_ids_map.get(key) or [])
            id_parts.append(f"{key}=[{values}]")
        lines.append("subject_ids: " + "; ".join(id_parts))
    if card.last_user_task:
        lines.append(f"last_user_task: {card.last_user_task}")
    if card.last_publication_status:
        lines.append(f"last_publication_status: {card.last_publication_status}")
    if card.last_result_summary:
        lines.append(f"last_result_summary: {card.last_result_summary}")
    if card.unresolved_question:
        lines.append(f"unresolved_question: {card.unresolved_question}")
    if card.unresolved_constraints:
        constraint_parts = [f"{key}={card.unresolved_constraints[key]}" for key in sorted(card.unresolved_constraints)]
        lines.append("unresolved_constraints: " + "; ".join(constraint_parts))
    if card.manifest_summary and card.manifest_summary != card.last_result_summary:
        lines.append(f"manifest_summary: {card.manifest_summary}")
    if card.anchor_summary and card.anchor_summary != card.last_result_summary:
        lines.append(f"anchor_summary: {card.anchor_summary}")
    if card.warnings:
        lines.append("warnings: " + "; ".join(str(item) for item in card.warnings if str(item).strip()))
    return "\n".join(lines)


def build_conversation_state_card(
    *,
    session_memory: Optional[SessionMemory],
    view_state: Optional[ConversationViewState] = None,
    selected_answer_meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Build and render the LLM-facing conversation state card."""

    return render_conversation_state_card(
        build_conversation_state_card_model(
            session_memory=session_memory,
            view_state=view_state,
            selected_answer_meta=selected_answer_meta,
        )
    )
