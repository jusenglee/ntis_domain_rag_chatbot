"""Conversation-memory helpers isolated from the server entrypoint.\n\nThe functions here convert Redis payloads to runtime objects and back so persistence policy\nis testable without importing the full FastAPI server.\n"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from apps.core.storage import KVStore


def safe_json_loads(raw: Optional[str], *, logger: Any, truncate_text: Any) -> Any:

    """Redis에서 읽은 문자열을 안전하게 JSON으로 복원한다.

    파싱에 실패하면 경고만 남기고 None을 돌려, 오래된 메모리 형식이나 손상된 값이 전체 요청을 깨지 않게 한다.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("JSON decode failed for redis payload: %s", truncate_text(raw))
        return None


def serialize_history(messages: List[BaseMessage]) -> List[Dict[str, str]]:
    """LangChain message 목록을 Redis 저장용 얕은 dict 목록으로 변환한다.

    여기서는 role과 content만 보존해 대화 메모리 계약을 단순한 2필드 형태로 고정한다.
    """
    serialized: List[Dict[str, str]] = []
    for msg in messages:
        role = "human" if isinstance(msg, HumanMessage) else "ai"
        serialized.append({"type": role, "content": msg.content})
    return serialized


def deserialize_history(payload: Any) -> List[BaseMessage]:
    """Redis에 저장된 history payload를 LangChain message 객체로 되돌린다.

    알 수 없는 항목은 건너뛰고 human 외 role은 AIMessage로 간주해, 과거 저장 포맷과의 호환성을 넓게 유지한다.
    """
    if not isinstance(payload, list):
        return []
    history: List[BaseMessage] = []
    for msg in payload:
        if not isinstance(msg, dict):
            continue
        role = msg.get("type")
        content = msg.get("content")
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
) -> tuple[List[BaseMessage], List[Dict[str, Any]], Dict[str, Any]]:

    """대화 메모리 저장소에서 history와 마지막 canonical snapshot을 읽어 온다.

    반환 계약은 (history, canonical_evidence, render_profile) 3-tuple이며,
    renderer가 아닌 canonical snapshot만 메모리 source of truth로 취급한다.
    """
    loaded_history: List[BaseMessage] = []
    canonical_evidence: List[Dict[str, Any]] = []
    render_profile: Dict[str, Any] = {}

    raw_hist = await kv_store.get(f"conversation:{conversation_id}:history") if kv_store else None
    hist_list = safe_json_loads(raw_hist, logger=logger, truncate_text=truncate_text)
    loaded_history = deserialize_history(hist_list)

    raw_canonical = await kv_store.get(f"conversation:{conversation_id}:last_canonical_evidence") if kv_store else None
    canonical_payload = safe_json_loads(raw_canonical, logger=logger, truncate_text=truncate_text)
    if isinstance(canonical_payload, list):
        canonical_evidence = [item for item in canonical_payload if isinstance(item, dict)]

    raw_profile = await kv_store.get(f"conversation:{conversation_id}:last_render_profile") if kv_store else None
    profile_payload = safe_json_loads(raw_profile, logger=logger, truncate_text=truncate_text)
    if isinstance(profile_payload, dict):
        render_profile = profile_payload

    return loaded_history, canonical_evidence, render_profile


async def save_conversation_memory(
    *,
    kv_store: Optional[KVStore],
    conversation_id: str,
    history: List[BaseMessage],
    canonical_evidence: Any,
    render_profile: Any,
    history_ttl_seconds: int,
) -> bool:

    """현재 대화 상태를 Redis 메모리 저장소에 기록한다.

    history와 함께 마지막 canonical_evidence, render_profile만 저장해 다음 요청이 raw payload dump 없이도 동일한 문맥을 복원하게 한다.
    """
    if kv_store is None:
        return False

    serialized_hist = serialize_history(history)
    await kv_store.set(
        f"conversation:{conversation_id}:history",
        json.dumps(serialized_hist, ensure_ascii=False),
        ex=history_ttl_seconds,
    )


    if canonical_evidence:
        await kv_store.set(
            f"conversation:{conversation_id}:last_canonical_evidence",
            json.dumps(canonical_evidence, ensure_ascii=False),
            ex=history_ttl_seconds,
        )

    if render_profile:
        await kv_store.set(
            f"conversation:{conversation_id}:last_render_profile",
            json.dumps(render_profile, ensure_ascii=False),
            ex=history_ttl_seconds,
        )


    return True


def build_save_history_payload(
    state: Any,
    *,
    max_history_turns: int,
) -> Dict[str, Any]:

    """workflow state에서 저장에 필요한 최소 메모리 payload를 추출한다.

    AI 마지막 턴을 history에 합친 뒤 turn 수를 자르고, canonical snapshot 관련 필드만 별도로 싣는다.
    """
    ai_turn = (getattr(state, "messages", None) or [])[-1:]
    full_history = (getattr(state, "chat_history", None) or []) + ai_turn
    trimmed_history = full_history[-max_history_turns:]

    return {
        "conversation_id": getattr(state, "conversation_id", ""),
        "history": trimmed_history,
        "canonical_evidence": getattr(state, "canonical_evidence", None),
        "render_profile": getattr(state, "render_profile", None),
        "request_started_at": getattr(state, "request_started_at", None),
    }
