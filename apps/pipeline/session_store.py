"""Pipeline 전용 슬림 세션 저장소.

레거시 apps.conversation.conversation_store가 history_log, view_state, render_profile 등
파이프라인이 사용하지 않는 항목까지 다루므로, 본 모듈은 SessionMemory의 핵심만 저장/복원한다.

저장 키: ``pipeline:v1:{conversation_id}:session``
TTL: 환경 변수 ``PIPELINE_SESSION_TTL_SECONDS`` (기본 7일)
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from loguru import logger

from apps.conversation.session_memory import SessionMemory
from apps.platform.storage import KVStore


_PIPELINE_KEY_PREFIX = "pipeline:v1"
_DEFAULT_TTL_SECONDS = int(os.getenv("PIPELINE_SESSION_TTL_SECONDS", "604800") or 604800)


def _session_key(conversation_id: str) -> str:
    return f"{_PIPELINE_KEY_PREFIX}:{conversation_id}:session"


async def load_pipeline_session(
    *,
    kv_store: Optional[KVStore],
    conversation_id: str,
) -> SessionMemory:
    """KV에서 SessionMemory를 복원. 없거나 손상이면 빈 객체."""
    if kv_store is None or not conversation_id:
        return SessionMemory(conversation_id=conversation_id)

    try:
        raw = await kv_store.get(_session_key(conversation_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[session_store] load failed: cid={conversation_id} err={exc}")
        return SessionMemory(conversation_id=conversation_id)

    if not raw:
        return SessionMemory(conversation_id=conversation_id)

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(f"[session_store] invalid json: cid={conversation_id} err={exc}")
        return SessionMemory(conversation_id=conversation_id)

    try:
        memory = SessionMemory.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[session_store] schema mismatch: cid={conversation_id} err={exc}")
        return SessionMemory(conversation_id=conversation_id)

    return memory


async def save_pipeline_session(
    *,
    kv_store: Optional[KVStore],
    conversation_id: str,
    memory: SessionMemory,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> bool:
    """SessionMemory를 KV에 직렬화 저장."""
    if kv_store is None or not conversation_id:
        return False
    try:
        payload = memory.model_dump()
        await kv_store.set(
            _session_key(conversation_id),
            json.dumps(payload, ensure_ascii=False),
            ex=ttl_seconds,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[session_store] save failed: cid={conversation_id} err={exc}")
        return False
