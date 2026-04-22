from __future__ import annotations

import re
from typing import Any, Dict

from langchain_core.messages import AIMessage

from apps.api.contracts.workflow_models import RuleDecision
from apps.api.runtime_helpers import (
    _state_log_summary_fields,
    compute_total_ms_from_start,
    log_event,
    logger,
    truncate_text,
)
from apps.api.streaming.contracts import AnswerArtifact
from apps.conversation.conversation_store import (
    build_save_history_payload,
    load_conversation_memory_from_store,
    save_conversation_memory,
)
from apps.conversation.raw_payload_store import (
    build_turn_id,
    load_raw_payload_memory_from_store,
    save_raw_payload_memory,
    sync_active_anchor_record,
)
from apps.conversation.memory_observer import log_memory_snapshot
from apps.conversation.request_facade import build_intent_payload
from apps.conversation.session_memory import EmptyContext
from apps.evidence.canonical_context import rehydrate_prev_context_from_canonical_evidence
from apps.platform.settings import REDIS_TTL

_MAX_HISTORY_TURNS = 10
_HISTORY_PREVIEW_LIMIT = 100


def is_short_query_exception(raw_query: str) -> bool:
    """Treat identifier-like short queries as valid instead of rejecting them."""

    text = str(raw_query or "").strip()
    if not text:
        return False

    compact = re.sub(r"[\s\-_/]", "", text)
    if re.fullmatch(r"\d{6,}", compact):
        return True
    if re.fullmatch(r"[A-Z]{3,}", compact):
        return True
    return False


async def node_load_memory(state: Any) -> Dict[str, Any]:
    """Restore conversation memory into the workflow-state shape."""

    cid = state.conversation_id
    loaded_history, canonical_evidence, render_profile, view_state, session_memory = await load_conversation_memory_from_store(
        cid,
        kv_store=getattr(state, "kv_store", None),
        logger=logger,
        truncate_text=lambda value, limit=_HISTORY_PREVIEW_LIMIT: truncate_text(value, limit),
    )
    raw_payload_memory = await load_raw_payload_memory_from_store(
        cid,
        kv_store=getattr(state, "kv_store", None),
        logger=logger,
        truncate_text=lambda value, limit=_HISTORY_PREVIEW_LIMIT: truncate_text(value, limit),
    )
    raw_payload_memory = sync_active_anchor_record(raw_payload_memory, view_state)
    effective_prev_context = rehydrate_prev_context_from_canonical_evidence(canonical_evidence)
    current_full_history = loaded_history + [state.messages[-1]]
    turn_id = str(getattr(state, "turn_id", "") or "").strip() or build_turn_id(
        conversation_id=cid,
        loaded_history=loaded_history,
        question=state.messages[-1].content,
    )

    log_event(
        "LOAD.MEMORY",
        request_id=state.request_id,
        conversation_id=cid,
        stage="load_memory",
        history_turns=len(loaded_history),
        prev_context_docs=len(effective_prev_context),
        turn_id=turn_id,
    )
    # ADR-0013: raw_payload_memory / view_state 스냅샷을 관측 이벤트로 기록한다.
    log_memory_snapshot(
        stage="load",
        request_id=state.request_id,
        conversation_id=cid,
        turn_id=turn_id,
        raw_payload_memory=raw_payload_memory,
        history_turns=len(loaded_history),
        view_state=view_state,
    )
    return {
        "question": state.messages[-1].content,
        "chat_history": current_full_history,
        "prev_context": effective_prev_context,
        "canonical_evidence": canonical_evidence,
        "render_profile": render_profile,
        "session_memory": session_memory,
        "view_state": view_state,
        "raw_payload_memory": raw_payload_memory,
        "turn_id": turn_id,
    }


async def node_rule_precheck(state: Any) -> Dict[str, Any]:
    """Handle greetings and underspecified ultra-short queries directly."""

    raw_user_msg = state.messages[-1].content.strip()
    user_msg = raw_user_msg.lower()

    greetings = ["안녕", "hello", "hi", "여보", "반가", "헤이"]
    if any(g in user_msg for g in greetings) and len(user_msg) < 10:
        return {
            "rule_decision": RuleDecision(
                action="direct_answer",
                direct_response="안녕하세요. 무엇을 도와드릴까요?",
                reason="Simple greeting detected",
            )
        }

    if len(raw_user_msg) < 5 and not is_short_query_exception(raw_user_msg):
        return {
            "rule_decision": RuleDecision(
                action="direct_answer",
                direct_response="질문을 조금 더 구체적으로 작성해 주세요.",
                reason="Query too short",
            )
        }

    return {
        "rule_decision": RuleDecision(
            action="proceed",
            reason="Standard query - proceed to analysis",
        )
    }


async def node_analyze_question(state: Any) -> Dict[str, Any]:
    """Build intent payload and planner output when they are not already present."""

    if state.question_analysis and state.intent_payload:
        return {
            "question_analysis": state.question_analysis,
            "intent_payload": state.intent_payload,
        }

    intent_payload, question_analysis = await build_intent_payload(
        question=state.messages[-1].content,
        conversation_id=state.conversation_id,
        chat_history=state.chat_history,
        prev_context=state.prev_context,
        canonical_evidence=getattr(state, "canonical_evidence", None) or [],
        view_state=getattr(state, "view_state", None),
        session_memory=getattr(state, "session_memory", None),
        request_id=getattr(state, "request_id", None),
        turn_id=getattr(state, "turn_id", None),
    )
    return {
        "question_analysis": question_analysis,
        "intent_payload": intent_payload,
    }


async def node_direct_answer(state: Any) -> Dict[str, Any]:
    """Return a direct-answer artifact when the rule precheck short-circuits."""

    response_text = state.rule_decision.direct_response
    artifact = AnswerArtifact(
        text=response_text,
        answer_kind="direct_answer",
        stream_metrics={"content_chars": len(response_text or ""), "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        meta={"answer_source": "direct_answer", "model_key": "direct"},
    )
    return {
        "answer_gemma": response_text,
        "answer_solar": response_text,
        "answer_artifact_gemma": artifact,
        "answer_artifact_solar": artifact,
        "answer_artifact": artifact,
        "final_answer_text": response_text,
        "final_answer_artifact": artifact,
        "selected_answer_meta": artifact.to_meta_dict(),
        "next_current_context": EmptyContext(),
        "merge_debug": {
            "selected_model": "direct",
            "selected_answer_source": "direct_answer",
            "selected_answer_kind": artifact.answer_kind,
        },
        "messages": [AIMessage(content=response_text)],
    }


async def node_save_history(state: Any) -> Dict[str, Any]:
    """Persist conversation history, canonical evidence, render profile, and view state."""

    kv_store = getattr(state, "kv_store", None)
    save_payload = build_save_history_payload(state, max_history_turns=_MAX_HISTORY_TURNS)
    cid = str(save_payload["conversation_id"] or "")
    raw_payload_memory = sync_active_anchor_record(
        getattr(state, "raw_payload_memory", None) or {},
        save_payload["view_state"],
    )

    saved = await save_conversation_memory(
        kv_store=kv_store,
        conversation_id=cid,
        history=save_payload["history"],
        canonical_evidence=save_payload["canonical_evidence"],
        render_profile=save_payload["render_profile"],
        view_state=save_payload["view_state"],
        selected_answer_meta=save_payload.get("selected_answer_meta"),
        intent_payload=save_payload.get("intent_payload"),
        next_current_context=save_payload.get("next_current_context"),
        turn_journal_tail=save_payload.get("turn_journal_tail"),
        history_ttl_seconds=REDIS_TTL,
        logger_obj=logger,
    )
    raw_saved = await save_raw_payload_memory(
        kv_store=kv_store,
        conversation_id=cid,
        raw_payload_memory=raw_payload_memory,
        history_ttl_seconds=REDIS_TTL,
    )
    if not saved:
        logger.debug("[memory] kv_store unavailable: skip history/context save (cid=%s)", cid)
    if not raw_saved:
        logger.debug("[memory] kv_store unavailable: skip raw payload save (cid=%s)", cid)

    # ADR-0013: save 시점 메모리 스냅샷 (prune 이후 상태 가시화)
    log_memory_snapshot(
        stage="save",
        request_id=getattr(state, "request_id", None),
        conversation_id=cid,
        turn_id=str(getattr(state, "turn_id", "") or "") or None,
        raw_payload_memory=raw_payload_memory,
        history_turns=len(save_payload.get("history") or []),
        view_state=save_payload.get("view_state"),
        note=None if (saved and raw_saved) else "kv_store_unavailable",
    )

    total_ms = compute_total_ms_from_start(getattr(state, "request_started_at", None))
    summary_fields = _state_log_summary_fields(state, total_ms=total_ms)
    log_event("REQ.SUMMARY", **summary_fields)
    log_event(
        "REQ.END",
        conversation_id=getattr(state, "conversation_id", None),
        request_id=getattr(state, "request_id", None),
        stage="request_end",
        total_ms=total_ms,
        selected_model=(getattr(state, "merge_debug", None) or {}).get("selected_model"),
    )
    return {}


def node_join_answers(state: Any) -> Dict[str, Any]:
    """Explicit no-op join node kept for graph readability."""

    return {}
