from __future__ import annotations

import base64
import gzip
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from apps.conversation.view_state import get_active_subject_entity
from apps.platform.settings import (
    RAW_PAYLOAD_COMPRESSION_CODEC,
    RAW_PAYLOAD_RECENT_ANCHOR_LIMIT,
    RAW_PAYLOAD_SCHEMA_VERSION,
)
from apps.platform.storage import KVStore


def _raw_payload_store_key(conversation_id: str) -> str:
    return f"conversation:v2:{conversation_id}:raw_payload_store"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _normalize_text(value)
        if text:
            return text
    return ""


def _build_anchor_key(
    *,
    collection: str,
    doc_id: str,
    pjt_id: str = "",
    pjt_no: str = "",
    rst_id: str = "",
) -> str:
    if pjt_id:
        return f"project:{pjt_id}"
    if rst_id:
        return f"result:{rst_id}"
    if pjt_no:
        return f"project_no:{pjt_no}"
    if collection and doc_id:
        return f"{collection}:{doc_id}"
    if doc_id:
        return f"doc:{doc_id}"
    return ""


def build_anchor_key_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    meta_basic = payload.get("meta_basic") if isinstance(payload.get("meta_basic"), dict) else {}
    meta_detail = payload.get("meta_detail") if isinstance(payload.get("meta_detail"), dict) else {}
    return _build_anchor_key(
        collection=_first_text(payload.get("_collection"), payload.get("collection")),
        doc_id=_first_text(payload.get("doc_id"), payload.get("id")),
        pjt_id=_first_text(payload.get("pjt_id"), meta_basic.get("pjt_id"), meta_detail.get("pjt_id")),
        pjt_no=_first_text(payload.get("pjt_no"), meta_basic.get("pjt_no"), meta_detail.get("pjt_no")),
        rst_id=_first_text(payload.get("rst_id"), meta_basic.get("rst_id"), meta_detail.get("rst_id")),
    )


def build_anchor_key_from_view_entity(entity: Any) -> str:
    if entity is None:
        return ""
    return _build_anchor_key(
        collection=_first_text(getattr(entity, "col", None)),
        doc_id=_first_text(getattr(entity, "doc_id", None)),
        pjt_id=_first_text(getattr(entity, "pjt_id", None)),
        pjt_no=_first_text(getattr(entity, "pjt_no", None)),
        rst_id=_first_text(getattr(entity, "rst_id", None)),
    )


def build_turn_id(*, conversation_id: str, loaded_history: List[Any], question: str) -> str:
    human_turn_index = 1 + sum(1 for item in (loaded_history or []) if isinstance(item, HumanMessage))
    history_tail = [
        {
            "type": item.__class__.__name__,
            "content": _normalize_text(getattr(item, "content", ""))[:200],
        }
        for item in list(loaded_history or [])[-6:]
    ]
    fingerprint = {
        "conversation_id": _normalize_text(conversation_id),
        "human_turn_index": int(human_turn_index),
        "question": _normalize_text(question),
        "history_tail": history_tail,
    }
    digest = hashlib.sha1(json.dumps(fingerprint, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:12]
    return f"turn:{human_turn_index}:{digest}"


def _compress_payload(payload: Dict[str, Any]) -> tuple[str, int, str]:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw)
    return (
        base64.b64encode(compressed).decode("ascii"),
        len(raw),
        hashlib.sha256(raw).hexdigest(),
    )


def _expiry_timestamp(history_ttl_seconds: int) -> str:
    ttl_seconds = max(1, int(history_ttl_seconds))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    return expires_at.isoformat()


def decompress_raw_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    blob = _normalize_text(record.get("raw_payload_compressed_b64"))
    if not blob:
        return {}
    try:
        compressed = base64.b64decode(blob.encode("ascii"))
        payload = gzip.decompress(compressed).decode("utf-8")
        value = json.loads(payload)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def load_raw_payload_memory(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"schema_version": RAW_PAYLOAD_SCHEMA_VERSION, "compression_codec": RAW_PAYLOAD_COMPRESSION_CODEC, "records": []}
    records = [dict(item) for item in list(payload.get("records") or []) if isinstance(item, dict)]
    return {
        "schema_version": _first_text(payload.get("schema_version")) or RAW_PAYLOAD_SCHEMA_VERSION,
        "compression_codec": _first_text(payload.get("compression_codec")) or RAW_PAYLOAD_COMPRESSION_CODEC,
        "records": records,
    }


async def load_raw_payload_memory_from_store(
    conversation_id: str,
    *,
    kv_store: Optional[KVStore],
    logger: Any,
    truncate_text: Any,
) -> Dict[str, Any]:
    raw = await kv_store.get(_raw_payload_store_key(conversation_id)) if kv_store else None
    if not raw:
        return load_raw_payload_memory(None)
    try:
        return load_raw_payload_memory(json.loads(raw))
    except json.JSONDecodeError:
        logger.warning("JSON decode failed for raw payload memory: {}", truncate_text(raw))
        return load_raw_payload_memory(None)


async def save_raw_payload_memory(
    *,
    kv_store: Optional[KVStore],
    conversation_id: str,
    raw_payload_memory: Dict[str, Any],
    history_ttl_seconds: int,
) -> bool:
    if kv_store is None:
        return False
    normalized = load_raw_payload_memory(raw_payload_memory)
    expires_at = _expiry_timestamp(history_ttl_seconds)
    normalized["records"] = [
        {
            **dict(item),
            "expires_at": expires_at,
        }
        for item in list(normalized.get("records") or [])
        if isinstance(item, dict)
    ]
    await kv_store.set(
        _raw_payload_store_key(conversation_id),
        json.dumps(normalized, ensure_ascii=False),
        ex=history_ttl_seconds,
    )
    return True


def sync_active_anchor_record(raw_payload_memory: Dict[str, Any], view_state: Any) -> Dict[str, Any]:
    memory = load_raw_payload_memory(raw_payload_memory)
    active_anchor_key = build_anchor_key_from_view_entity(get_active_subject_entity(view_state))
    records = []
    for record in list(memory.get("records") or []):
        next_record = dict(record)
        next_record["active"] = bool(active_anchor_key and _normalize_text(record.get("anchor_key")) == active_anchor_key)
        records.append(next_record)
    memory["records"] = _prune_records(records)
    return memory


def upsert_raw_payload_records(
    raw_payload_memory: Dict[str, Any],
    *,
    conversation_id: str,
    turn_id: str,
    entity_type: str,
    payloads: List[Dict[str, Any]],
    activate_first: bool = False,
) -> Dict[str, Any]:
    memory = load_raw_payload_memory(raw_payload_memory)
    by_anchor: Dict[str, Dict[str, Any]] = {
        _normalize_text(item.get("anchor_key")): dict(item)
        for item in list(memory.get("records") or [])
        if _normalize_text(item.get("anchor_key"))
    }
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    for index, payload in enumerate(payloads):
        if not isinstance(payload, dict):
            continue
        anchor_key = build_anchor_key_from_payload(payload)
        if not anchor_key:
            continue
        compressed, serialized_size, payload_hash = _compress_payload(payload)
        meta_basic = payload.get("meta_basic") if isinstance(payload.get("meta_basic"), dict) else {}
        meta_detail = payload.get("meta_detail") if isinstance(payload.get("meta_detail"), dict) else {}
        by_anchor[anchor_key] = {
            "conversation_id": _normalize_text(conversation_id),
            "turn_id": _normalize_text(turn_id),
            "anchor_key": anchor_key,
            "entity_type": _first_text(entity_type, payload.get("source_type"), "project"),
            "collection": _first_text(payload.get("_collection"), payload.get("collection")),
            "doc_id": _first_text(payload.get("doc_id"), payload.get("id")),
            "pjt_id": _first_text(payload.get("pjt_id"), meta_basic.get("pjt_id"), meta_detail.get("pjt_id")),
            "pjt_no": _first_text(payload.get("pjt_no"), meta_basic.get("pjt_no"), meta_detail.get("pjt_no")),
            "rst_id": _first_text(payload.get("rst_id"), meta_basic.get("rst_id"), meta_detail.get("rst_id")),
            "payload_hash": payload_hash,
            "schema_version": RAW_PAYLOAD_SCHEMA_VERSION,
            "serialized_size": int(serialized_size),
            "compression_codec": RAW_PAYLOAD_COMPRESSION_CODEC,
            "raw_payload_compressed_b64": compressed,
            "expires_at": None,
            "updated_at": now,
            "active": bool(activate_first and index == 0),
        }
    memory["records"] = _prune_records(list(by_anchor.values()))
    return memory


def get_anchor_record(raw_payload_memory: Dict[str, Any], *, anchor_key: str) -> Optional[Dict[str, Any]]:
    wanted = _normalize_text(anchor_key)
    if not wanted:
        return None
    for record in list(load_raw_payload_memory(raw_payload_memory).get("records") or []):
        if _normalize_text(record.get("anchor_key")) == wanted:
            return dict(record)
    return None


def _prune_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)
    live_records: List[Dict[str, Any]] = []
    for item in list(records or []):
        if not isinstance(item, dict):
            continue
        expires_raw = _normalize_text(item.get("expires_at"))
        if expires_raw:
            try:
                expires_at = datetime.fromisoformat(expires_raw)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at < now:
                    continue
            except Exception:
                pass
        live_records.append(dict(item))

    active_records = [dict(item) for item in live_records if bool(item.get("active"))]
    inactive_records = [dict(item) for item in live_records if not bool(item.get("active"))]
    active_records.sort(key=lambda item: _normalize_text(item.get("updated_at")), reverse=True)
    inactive_records.sort(key=lambda item: _normalize_text(item.get("updated_at")), reverse=True)
    kept: List[Dict[str, Any]] = []
    if active_records:
        kept.append(active_records[0])
    inactive_limit = max(0, int(RAW_PAYLOAD_RECENT_ANCHOR_LIMIT))
    kept.extend(inactive_records[:inactive_limit])
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for record in kept:
        anchor_key = _normalize_text(record.get("anchor_key"))
        if not anchor_key or anchor_key in seen:
            continue
        seen.add(anchor_key)
        deduped.append(record)
    return deduped
