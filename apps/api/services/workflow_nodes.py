from __future__ import annotations

import re
from typing import Any, Dict

from langchain_core.messages import AIMessage

from apps.api.services.canonical_context import rehydrate_prev_context_from_canonical_evidence


def is_short_query_exception(raw_query: str) -> bool:
    """짧은 질의라도 id 형태라면 예외로 허용할지 판정한다.
    숫자 시퀀스나 약어 시드에 해당하는 경우에는 길이가 짧아도 direct reject 하지 않는다.
    """
    text = str(raw_query or "").strip()
    if not text:
        return False

    compact = re.sub(r"[\s\-_/]", "", text)
    if re.fullmatch(r"\d{6,}", compact):
        return True
    if re.fullmatch(r"[A-Z]{3,}", compact):
        return True
    return False


async def node_load_memory(
    state: Any,
    *,
    load_conversation_memory_fn: Any,
    log_event: Any,
) -> Dict[str, Any]:
    """conversation memory에서 history·canonical evidence·render profile을 로드하여 workflow state로 환원한다.
    canonical evidence로부터 prev_context를 재수화해 memory 계약과 workflow 입력 shape를 연결한다.
    """
    cid = state.conversation_id
    loaded_history, canonical_evidence, render_profile, view_state = await load_conversation_memory_fn(
        cid,
        kv_store=getattr(state, "kv_store", None),
    )
    effective_prev_context = rehydrate_prev_context_from_canonical_evidence(canonical_evidence)
    current_full_history = loaded_history + [state.messages[-1]]

    log_event(
        "LOAD.MEMORY",
        request_id=state.request_id,
        conversation_id=cid,
        stage="load_memory",
        history_turns=len(loaded_history),
        prev_context_docs=len(effective_prev_context),
    )
    return {
        "question": state.messages[-1].content,
        "chat_history": current_full_history,
        "prev_context": effective_prev_context,
        "canonical_evidence": canonical_evidence,
        "render_profile": render_profile,
        "view_state": view_state,
    }


async def node_rule_precheck(
    state: Any,
    *,
    rule_decision_cls: Any,
    is_short_query_exception_fn: Any = is_short_query_exception,
) -> Dict[str, Any]:
    """간단한 인사나 너무 짧은 질의를 룰 기반으로 먼저 처리한다.
    LLM/planner 호출 전에 즉시 답변할 수 있는 경로를 거르고, id 형태 예외는 따로 허용한다.
    """
    raw_user_msg = state.messages[-1].content.strip()
    user_msg = raw_user_msg.lower()

    greetings = ["안녕", "hello", "hi", "헬로", "반가", "하이"]
    if any(g in user_msg for g in greetings) and len(user_msg) < 10:
        return {
            "rule_decision": rule_decision_cls(
                action="direct_answer",
                direct_response="안녕하세요. 무엇을 도와드릴까요?",
                reason="Simple greeting detected",
            )
        }

    if len(raw_user_msg) < 5 and not is_short_query_exception_fn(raw_user_msg):
        return {
            "rule_decision": rule_decision_cls(
                action="direct_answer",
                direct_response="질문을 조금 더 구체적으로 작성해 주세요.",
                reason="Query too short",
            )
        }

    return {
        "rule_decision": rule_decision_cls(
            action="proceed",
            reason="Standard query - proceed to analysis",
        )
    }


async def node_analyze_question(
    state: Any,
    *,
    build_intent_payload_fn: Any,
) -> Dict[str, Any]:
    """question analysis가 없을 때 intent payload와 planner 결과를 생성한다.
    이미 분석이 끝난 state라면 그대로 재사용해 workflow가 불필요한 분석을 반복하지 않게 한다.
    """
    if state.question_analysis and state.intent_payload:
        return {
            "question_analysis": state.question_analysis,
            "intent_payload": state.intent_payload,
        }

    intent_payload, question_analysis = await build_intent_payload_fn(
        question=state.messages[-1].content,
        conversation_id=state.conversation_id,
        chat_history=state.chat_history,
        prev_context=state.prev_context,
        canonical_evidence=getattr(state, "canonical_evidence", None) or [],
        view_state=getattr(state, "view_state", None),
        request_id=getattr(state, "request_id", None),
    )
    return {
        "question_analysis": question_analysis,
        "intent_payload": intent_payload,
    }


async def node_direct_answer(state: Any) -> Dict[str, Any]:
    """rule precheck에서 즉시 답변이 결정된 경우 같은 문구를 양쪽 모델 채널에 채운다.
    후속 merge node가 특수 처리 없이 같은 입력 shape를 받을 수 있게 하는 workflow 호환 헬퍼다.
    """
    response_text = state.rule_decision.direct_response
    return {
        "answer_gemma": response_text,
        "answer_solar": response_text,
        "messages": [AIMessage(content=response_text)],
    }


async def node_save_history(
    state: Any,
    *,
    build_save_history_payload_fn: Any,
    save_conversation_memory_fn: Any,
    logger: Any,
    log_event: Any,
    state_log_summary_fields_fn: Any,
    compute_total_ms_from_start_fn: Any,
    max_history_turns: int,
    history_ttl_seconds: int,
) -> Dict[str, Any]:
    """요청 종료 시 history, canonical evidence, render profile를 KV store에 저장한다.
    save payload 조립, memory write, summary/end event 로깅을 함께 처리해 후속 회고와 재수화가 같은 근거를 공유하게 한다.
    """
    kv_store = getattr(state, "kv_store", None)
    save_payload = build_save_history_payload_fn(
        state,
        max_history_turns=max_history_turns,
    )
    cid = str(save_payload["conversation_id"] or "")

    saved = await save_conversation_memory_fn(
        kv_store=kv_store,
        conversation_id=cid,
        history=save_payload["history"],
        canonical_evidence=save_payload["canonical_evidence"],
        render_profile=save_payload["render_profile"],
        view_state=save_payload["view_state"],
        history_ttl_seconds=history_ttl_seconds,
    )
    if not saved:
        logger.debug("[memory] kv_store unavailable: skip history/context save (cid=%s)", cid)

    total_ms = compute_total_ms_from_start_fn(getattr(state, "request_started_at", None))
    summary_fields = state_log_summary_fields_fn(state, total_ms=total_ms)
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
    """답변 조인 전용 node 자리를 유지하는 no-op 헬퍼다.
    LangGraph 그래프 구조에서 분기 후 합류 지점을 명시하고, 실제 merge 로직은 다른 node에 위임한다.
    """
    return {}
