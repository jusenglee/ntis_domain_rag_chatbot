"""Conversation-memory helpers isolated from the server entrypoint."""

from __future__ import annotations

import html
import json
from typing import Any, Dict, List, Optional

from loguru import logger
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from apps.conversation.session_memory import (
    EmptyContext,
    SessionMemory,
    build_session_memory,
    empty_session_memory,
    load_session_memory,
    view_state_from_current_context,
)
from apps.conversation.view_state import ConversationViewState
from apps.platform.storage import KVStore


def _conversation_store_key(conversation_id: str, suffix: str) -> str:
    return f"conversation:v2:{conversation_id}:{suffix}"


def _session_memory_key(conversation_id: str) -> str:
    return f"conversation:v3:{conversation_id}:session_memory"


def _legacy_conversation_store_keys(conversation_id: str) -> tuple[str, ...]:
    return tuple(
        _conversation_store_key(conversation_id, suffix)
        for suffix in ("history", "last_canonical_evidence", "last_render_profile", "view_state")
    )


def _strip_html_like_markup(text: str) -> str:
    decoded = html.unescape(str(text or ""))
    if "<" not in decoded or ">" not in decoded:
        return decoded

    out: list[str] = []
    inside_tag = False
    for ch in decoded:
        if ch == "<":
            inside_tag = True
            if out and out[-1] != " ":
                out.append(" ")
            continue
        if ch == ">" and inside_tag:
            inside_tag = False
            if out and out[-1] != " ":
                out.append(" ")
            continue
        if not inside_tag:
            out.append(ch)
    return "".join(out)


def _sanitize_history_content(content: Any, *, role: str) -> str:
    text = str(content or "")
    if not text or role != "ai":
        return text

    stripped = _strip_html_like_markup(text)
    normalized = " ".join(stripped.split())
    return normalized or text.strip()


def safe_json_loads(raw: Optional[str], *, logger: Any, truncate_text: Any) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("JSON decode failed for redis payload: {}", truncate_text(raw))
        return None


def serialize_history(messages: List[BaseMessage]) -> List[Dict[str, str]]:
    serialized: List[Dict[str, str]] = []
    for msg in messages:
        role = "human" if isinstance(msg, HumanMessage) else "ai"
        content = _sanitize_history_content(getattr(msg, "content", ""), role=role)
        if content:
            serialized.append({"type": role, "content": content})
    return serialized


def deserialize_history(payload: Any) -> List[BaseMessage]:
    if not isinstance(payload, list):
        return []
    history: List[BaseMessage] = []
    for msg in payload:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("type") or "ai").strip().lower() or "ai"
        content = _sanitize_history_content(msg.get("content"), role=role)
        if not content:
            continue
        if role == "human":
            history.append(HumanMessage(content=content))
        else:
            history.append(AIMessage(content=content))
    return history


async def load_conversation_memory_from_store(
    conversation_id: str,
    *,
    kv_store: Optional[KVStore],
    logger: Any,
    truncate_text: Any,
) -> tuple[List[BaseMessage], List[Dict[str, Any]], Dict[str, Any], ConversationViewState, SessionMemory]:
    raw_session = await kv_store.get(_session_memory_key(conversation_id)) if kv_store else None
    session_payload = safe_json_loads(raw_session, logger=logger, truncate_text=truncate_text)
    session_memory = load_session_memory(session_payload)
    if raw_session and session_payload and session_memory.current_context.context_type == "empty":
        logger.warning("invalid v3 session payload; starting empty session (cid={cid})", cid=conversation_id)
        session_memory = empty_session_memory()

    loaded_history = deserialize_history(session_memory.history_log)
    canonical_evidence = [item for item in (session_memory.canonical_evidence or []) if isinstance(item, dict)]
    render_profile = dict(session_memory.render_profile or {})
    view_state = view_state_from_current_context(session_memory)

    return loaded_history, canonical_evidence, render_profile, view_state, session_memory


async def save_conversation_memory(
    *,
    kv_store: Optional[KVStore],
    conversation_id: str,
    history: List[BaseMessage],
    canonical_evidence: Any,
    render_profile: Any,
    view_state: Any,
    history_ttl_seconds: int,
    selected_answer_meta: Optional[Dict[str, Any]] = None,
    intent_payload: Any = None,
    next_current_context: Any = None,
    turn_journal_tail: Optional[List[Dict[str, Any]]] = None,
    logger_obj: Any = None,
) -> bool:
    if kv_store is None:
        return False

    normalized_view_state = view_state if isinstance(view_state, ConversationViewState) else ConversationViewState()
    session_memory = build_session_memory(
        history_log=serialize_history(history),
        canonical_evidence=canonical_evidence,
        render_profile=render_profile,
        view_state=normalized_view_state,
        selected_answer_meta=selected_answer_meta,
        intent_payload=intent_payload,
        current_context=next_current_context,
        turn_journal_tail=turn_journal_tail,
    )
    await kv_store.set(
        _session_memory_key(conversation_id),
        json.dumps(session_memory.model_dump(), ensure_ascii=False),
        ex=history_ttl_seconds,
    )

    log = logger_obj or logger
    for legacy_key in _legacy_conversation_store_keys(conversation_id):
        try:
            await kv_store.delete(legacy_key)
        except Exception as exc:
            log.warning("[memory] failed to delete legacy v2 key {}: {}", legacy_key, exc)

    return True


def build_save_history_payload(
    state: Any,
    *,
    max_history_turns: int,
) -> Dict[str, Any]:
    ai_turn = (getattr(state, "messages", None) or [])[-1:]
    full_history = (getattr(state, "chat_history", None) or []) + ai_turn
    trimmed_history = full_history[-max_history_turns:]
    turn_journal_tail = [
        item
        for item in (
            getattr(state, "merge_debug", None),
            getattr(state, "retrieval_runtime_meta", None),
        )
        if isinstance(item, dict) and item
    ]

    return {
        "conversation_id": getattr(state, "conversation_id", ""),
        "history": trimmed_history,
        "canonical_evidence": getattr(state, "canonical_evidence", None),
        "render_profile": getattr(state, "render_profile", None),
        "view_state": getattr(state, "view_state", None),
        "selected_answer_meta": getattr(state, "selected_answer_meta", None),
        "intent_payload": getattr(state, "intent_payload", None),
        "next_current_context": getattr(state, "next_current_context", None) or EmptyContext(),
        "turn_journal_tail": turn_journal_tail,
        "request_started_at": getattr(state, "request_started_at", None),
    }
