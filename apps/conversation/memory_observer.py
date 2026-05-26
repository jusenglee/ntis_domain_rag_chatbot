"""Conversation memory observability helpers.

ADR-0013 에 따라 raw_payload / conversation memory / context_router 의 관측용
이벤트를 표준화한다. 이 모듈은 라우팅이나 retention 정책 자체를 변경하지 않고
**관측 전용 fire-and-forget helper**만 제공한다.

규칙:
- raw payload 원본 내용은 로그에 절대 싣지 않는다 (size/count/key 메타만).
- emit 실패가 기능 경로를 중단시키지 않도록 best-effort로 처리한다.
- 기존 이벤트 이름 (`LOAD.MEMORY`, `CONTEXT.ROUTER`, `FOLLOWUP.FACT_RESOLVED`)는
  유지하고, 여기서는 신규 이벤트 이름만 사용한다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from apps.api.runtime_helpers import log_event


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def summarize_raw_payload_memory(memory: Any) -> Dict[str, Any]:
    """raw_payload_memory 구조의 카운트/바이트 요약을 만든다.

    내용(raw payload body)은 포함하지 않는다.
    """
    if not isinstance(memory, dict):
        return {
            "records_total": 0,
            "records_active": 0,
            "records_inactive": 0,
            "bytes_total": 0,
            "anchor_keys": [],
            "oldest_updated_at": None,
            "newest_updated_at": None,
        }
    records: List[Dict[str, Any]] = [
        dict(item) for item in list(memory.get("records") or []) if isinstance(item, dict)
    ]
    bytes_total = 0
    anchor_keys: List[str] = []
    active = 0
    inactive = 0
    oldest: Optional[str] = None
    newest: Optional[str] = None
    for record in records:
        size = _safe_int(record.get("serialized_size"), 0)
        bytes_total += max(0, size)
        anchor_key = _as_text(record.get("anchor_key"))
        if anchor_key:
            anchor_keys.append(anchor_key)
        if bool(record.get("active")):
            active += 1
        else:
            inactive += 1
        updated_at = _as_text(record.get("updated_at"))
        if updated_at:
            if oldest is None or updated_at < oldest:
                oldest = updated_at
            if newest is None or updated_at > newest:
                newest = updated_at
    return {
        "records_total": len(records),
        "records_active": active,
        "records_inactive": inactive,
        "bytes_total": bytes_total,
        "anchor_keys": anchor_keys[:16],  # 상한
        "oldest_updated_at": oldest,
        "newest_updated_at": newest,
    }


def log_memory_snapshot(
    *,
    stage: str,
    request_id: Optional[str],
    conversation_id: Optional[str],
    turn_id: Optional[str] = None,
    raw_payload_memory: Any = None,
    history_turns: Optional[int] = None,
    view_state: Any = None,
    note: Optional[str] = None,
    kv_store: Any = None,
) -> None:
    """MEMORY.SNAPSHOT 이벤트를 방출한다 (load/save 시점 호출용)."""
    try:
        summary = summarize_raw_payload_memory(raw_payload_memory)
        recent_mentions_count: Optional[int] = None
        manifest_items: Optional[int] = None
        subject_index_size: Optional[int] = None
        if view_state is not None:
            recent = getattr(view_state, "recent_mentions", None)
            if isinstance(recent, (list, tuple)):
                recent_mentions_count = len(recent)
            manifest = getattr(view_state, "visible_answer_manifest", None)
            items = getattr(manifest, "items", None)
            if isinstance(items, (list, tuple)):
                manifest_items = len(items)
            subject_index = getattr(view_state, "subject_index", None)
            if isinstance(subject_index, dict):
                subject_index_size = len(subject_index)
            elif isinstance(subject_index, (list, tuple)):
                subject_index_size = len(subject_index)
        kv_backend = str(getattr(kv_store, "backend_name", "") or "").strip() or None
        log_event(
            "MEMORY.SNAPSHOT",
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            stage=stage,
            raw_records_total=summary["records_total"],
            raw_records_active=summary["records_active"],
            raw_records_inactive=summary["records_inactive"],
            raw_bytes_total=summary["bytes_total"],
            raw_oldest_updated_at=summary["oldest_updated_at"],
            raw_newest_updated_at=summary["newest_updated_at"],
            raw_anchor_keys=summary["anchor_keys"] or None,
            history_turns=history_turns,
            view_recent_mentions=recent_mentions_count,
            view_manifest_items=manifest_items,
            view_subject_index_size=subject_index_size,
            kv_backend=kv_backend,
            note=note,
        )
    except Exception:
        # 관측 전용이므로 실패를 상위에 전파하지 않는다.
        pass


def log_fact_followup_miss(
    *,
    request_id: Optional[str],
    conversation_id: Optional[str],
    turn_id: Optional[str],
    reason: str,
    anchor_key: Optional[str] = None,
    has_anchor: Optional[bool] = None,
    has_record: Optional[bool] = None,
    base_route: Optional[str] = None,
) -> None:
    """FOLLOWUP.FACT_MISS 이벤트를 방출한다.

    reason 값:
    - no_anchor_key: active entity 또는 anchor_key 미존재
    - no_record: anchor는 있지만 raw_payload record 미존재
    - no_payload: record는 있지만 decompress 실패/빈 payload
    - pattern_unmatched: payload는 있지만 질문 패턴 매칭 실패
    - preconditions_unmet: 호출자 측 조건(explicit_followup/anchor_source 등) 미충족
    """
    try:
        log_event(
            "FOLLOWUP.FACT_MISS",
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            reason=_as_text(reason) or "unknown",
            anchor_key=anchor_key,
            has_anchor=None if has_anchor is None else int(bool(has_anchor)),
            has_record=None if has_record is None else int(bool(has_record)),
            base_route=base_route,
        )
    except Exception:
        pass


# Removed per ADR-0015 Stage 4: `log_context_router_transition` emitted the
# `CONTEXT.ROUTER.FALLBACK` event for the legacy `context_router` heuristic↔LLM
# fallback path. With `context_router` deleted, no caller remains.
