"""Conversation-memory helpers isolated from the server entrypoint.

The functions here convert Redis payloads to runtime objects and back so persistence policy
is testable without importing the full FastAPI server.
"""

from __future__ import annotations

import html
import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from apps.conversation.view_state import ConversationViewState, load_view_state
from apps.platform.storage import KVStore


def _conversation_store_key(conversation_id: str, suffix: str) -> str:
    return f"conversation:v2:{conversation_id}:{suffix}"


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
        if ch == ">":
            if inside_tag:
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
        logger.warning("JSON decode failed for redis payload: %s", truncate_text(raw))
        return None


def serialize_history(messages: List[BaseMessage]) -> List[Dict[str, str]]:
    serialized: List[Dict[str, str]] = []
    for msg in messages:
        role = "human" if isinstance(msg, HumanMessage) else "ai"
        content = _sanitize_history_content(getattr(msg, "content", ""), role=role)
        if not content:
            continue
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
) -> tuple[List[BaseMessage], List[Dict[str, Any]], Dict[str, Any], ConversationViewState]:
    loaded_history: List[BaseMessage] = []
    canonical_evidence: List[Dict[str, Any]] = []
    render_profile: Dict[str, Any] = {}
    view_state = ConversationViewState()

    raw_hist = await kv_store.get(_conversation_store_key(conversation_id, "history")) if kv_store else None
    hist_list = safe_json_loads(raw_hist, logger=logger, truncate_text=truncate_text)
    loaded_history = deserialize_history(hist_list)

    raw_canonical = await kv_store.get(_conversation_store_key(conversation_id, "last_canonical_evidence")) if kv_store else None
    canonical_payload = safe_json_loads(raw_canonical, logger=logger, truncate_text=truncate_text)
    if isinstance(canonical_payload, list):
        canonical_evidence = [item for item in canonical_payload if isinstance(item, dict)]

    raw_profile = await kv_store.get(_conversation_store_key(conversation_id, "last_render_profile")) if kv_store else None
    profile_payload = safe_json_loads(raw_profile, logger=logger, truncate_text=truncate_text)
    if isinstance(profile_payload, dict):
        render_profile = profile_payload

    raw_view_state = await kv_store.get(_conversation_store_key(conversation_id, "view_state")) if kv_store else None
    view_state_payload = safe_json_loads(raw_view_state, logger=logger, truncate_text=truncate_text)
    view_state = load_view_state(view_state_payload)

    return loaded_history, canonical_evidence, render_profile, view_state


async def save_conversation_memory(
    *,
    kv_store: Optional[KVStore],
    conversation_id: str,
    history: List[BaseMessage],
    canonical_evidence: Any,
    render_profile: Any,
    view_state: Any,
    history_ttl_seconds: int,
) -> bool:
    if kv_store is None:
        return False

    serialized_hist = serialize_history(history)
    await kv_store.set(
        _conversation_store_key(conversation_id, "history"),
        json.dumps(serialized_hist, ensure_ascii=False),
        ex=history_ttl_seconds,
    )

    canonical_key = _conversation_store_key(conversation_id, "last_canonical_evidence")
    if canonical_evidence is None:
        pass
    elif canonical_evidence:
        await kv_store.set(
            canonical_key,
            json.dumps(canonical_evidence, ensure_ascii=False),
            ex=history_ttl_seconds,
        )
    else:
        await kv_store.delete(canonical_key)

    render_profile_key = _conversation_store_key(conversation_id, "last_render_profile")
    if render_profile is None:
        pass
    elif render_profile:
        await kv_store.set(
            render_profile_key,
            json.dumps(render_profile, ensure_ascii=False),
            ex=history_ttl_seconds,
        )
    else:
        await kv_store.delete(render_profile_key)

    if view_state is not None:
        payload = view_state.model_dump() if hasattr(view_state, "model_dump") else dict(view_state)
        await kv_store.set(
            _conversation_store_key(conversation_id, "view_state"),
            json.dumps(payload, ensure_ascii=False),
            ex=history_ttl_seconds,
        )

    return True


def build_save_history_payload(
    state: Any,
    *,
    max_history_turns: int,
) -> Dict[str, Any]:
    ai_turn = (getattr(state, "messages", None) or [])[-1:]
    full_history = (getattr(state, "chat_history", None) or []) + ai_turn
    trimmed_history = full_history[-max_history_turns:]

    return {
        "conversation_id": getattr(state, "conversation_id", ""),
        "history": trimmed_history,
        "canonical_evidence": getattr(state, "canonical_evidence", None),
        "render_profile": getattr(state, "render_profile", None),
        "view_state": getattr(state, "view_state", None),
        "request_started_at": getattr(state, "request_started_at", None),
    }
