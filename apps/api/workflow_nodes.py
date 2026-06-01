from __future__ import annotations

import re
import json
from typing import Any, Dict

from langchain_core.messages import AIMessage

from apps.api.contracts.workflow_models import AgentAnswerContext, RuleDecision
from apps.api.runtime_helpers import (
    _state_log_summary_fields,
    compute_total_ms_from_start,
    log_event,
    logger,
    merge_log_fields,
    truncate_text,
)
from apps.api.streaming.contracts import AnswerArtifact, ErrorArtifact
from apps.conversation.agent_contracts import AgentDecision
from apps.conversation.agent_dialogue_router import run_dialogue_agent
from apps.conversation.agent_flags import agentic_max_steps
from apps.conversation.agent_observation import AgentObservation, AgentToolExecutionResult
from apps.conversation.agent_tool_executor import execute_agent_tool
from apps.conversation.agent_tools import default_agent_tool_specs
from apps.conversation.conversation_state_card import (
    build_conversation_state_card_model,
    render_conversation_state_card,
)
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
from apps.conversation.entity_reference import ClarificationRequest
from apps.conversation.session_memory import ClarificationContext, EmptyContext, SessionMemory
from apps.evidence.canonical_context import rehydrate_prev_context_from_canonical_evidence
from apps.platform.settings import REDIS_TTL

# --- 설정 상수 ---
_MAX_HISTORY_TURNS = 10         # 저장 및 로드할 최대 대화 턴 수
_HISTORY_PREVIEW_LIMIT = 100     # 로그 출력 시 히스토리 텍스트 절단 길이
_AGENT_QUERY_PREVIEW_LIMIT = 80  # 로그 출력 시 에이전트 쿼리 절단 길이


def _agent_base_fields(state: Any) -> Dict[str, Any]:
    """로깅을 위해 현재 상태에서 기본 요청 식별자들을 추출합니다."""
    return {
        "request_id": getattr(state, "request_id", None),
        "conversation_id": getattr(state, "conversation_id", None),
        "turn_id": getattr(state, "turn_id", None),
    }


def _agent_context_meta_from_card_model(card_model: Any) -> Dict[str, Any]:
    """상태 카드 모델에서 에이전트의 문맥 정보를 추출하여 로깅용 메타데이터로 변환합니다."""
    constraints = dict(getattr(card_model, "unresolved_constraints", {}) or {})
    return {
        "current_context_type": getattr(card_model, "current_context_type", None),
        "subject_kind": getattr(card_model, "current_subject_kind", None),
        "subject_name": getattr(card_model, "current_subject_name", None),
        "subject_identity_status": getattr(card_model, "current_subject_identity_status", None),
        "refinement_allowed": bool(getattr(card_model, "refinement_allowed", False)),
        "last_publication_status": getattr(card_model, "last_publication_status", None),
        "unresolved_constraint_keys": sorted(constraints.keys()),
    }


def _agent_context_fields(state: Any) -> Dict[str, Any]:
    """상태(state)에 저장된 에이전트 문맥 메타데이터를 로깅 필드로 변환합니다."""
    meta = getattr(state, "agent_context_meta", None)
    if isinstance(meta, dict) and meta:
        return {
            "current_context_type": meta.get("current_context_type"),
            "subject_kind": meta.get("subject_kind"),
            "subject_name": meta.get("subject_name"),
            "subject_identity_status": meta.get("subject_identity_status"),
            "refinement_allowed": meta.get("refinement_allowed"),
            "last_publication_status": meta.get("last_publication_status"),
        }
    return {}


def _safe_agent_tool_arg_fields(tool_args: Dict[str, Any]) -> Dict[str, Any]:
    """도구 호출 인자 중 민감하지 않고 로깅에 유용한 필드만 안전하게 추출합니다."""
    args = dict(tool_args or {})
    fields: Dict[str, Any] = {}
    # 분석에 유의미한 주요 검색/필터 인자들
    for key in (
        "subject_ref",
        "subject_kind",
        "subject_name",
        "domain_head",
        "people_name",
        "org_name",
        "affiliation_org_name",
        "perf_type",
        "year_from",
        "year_to",
        "role",
        "target",
        "limit",
    ):
        value = args.get(key)
        if value not in (None, "", [], {}):
            fields[key] = value
    
    # 쿼리문은 길이를 제한하여 미리보기 형태로 기록
    query = str(args.get("query") or "").strip()
    if query:
        fields["query_chars"] = len(query)
        fields["query_preview"] = truncate_text(query, _AGENT_QUERY_PREVIEW_LIMIT)
    return fields


def _agent_decision_fields(decision: Any) -> Dict[str, Any]:
    """에이전트의 결정(Decision) 객체에서 로깅용 정보를 추출합니다."""
    return {
        "decision_type": getattr(decision, "decision_type", None),
        "tool_name": getattr(decision, "tool_name", None),
        "confidence": round(float(getattr(decision, "confidence", 0.0) or 0.0), 3),
        **_safe_agent_tool_arg_fields(dict(getattr(decision, "tool_args", None) or {})),
    }


def _observation_reason(observation: Any) -> str | None:
    """도구 실행 관측 결과(Observation)에서 실패나 경고의 주된 원인을 파악합니다."""
    warnings = list(getattr(observation, "warnings", []) or [])
    if warnings:
        return str(warnings[0])
    summary = str(getattr(observation, "summary", "") or "").strip()
    return summary or None


# 에이전트가 사용자에게 묻지 않고 스스로 재시도해야 할 내부 오류 패턴들
_INTERNAL_TOOL_ERROR_REASONS = {
    "planner_error",
    "llmjsonextractionerror",
    "tool_error",
    "schema_error",
    "provider_error",
    "backend_error",
}


def _is_internal_tool_error_text(value: Any) -> bool:
    """텍스트 내용이 시스템 내부 오류를 나타내는지 검사합니다."""
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(reason in text for reason in _INTERNAL_TOOL_ERROR_REASONS)


def _has_internal_tool_error_context(state: Any, observation: Any) -> bool:
    """현재 상태나 관측 결과에 시스템 내부 오류가 포함되어 있는지 종합적으로 판단합니다."""
    for candidate in (
        observation,
        getattr(state, "agent_observation", None),
        getattr(state, "agent_tool_retry_observation", None),
    ):
        warnings = list(getattr(candidate, "warnings", []) or [])
        if any(_is_internal_tool_error_text(item) for item in warnings):
            return True
        if _is_internal_tool_error_text(getattr(candidate, "summary", "")):
            return True
    return _is_internal_tool_error_text(getattr(state, "agent_tool_retry_reason", ""))


def _agent_tool_call_fingerprint(tool_name: str, tool_args: Dict[str, Any]) -> str:
    """도구 호출의 고유 지문을 생성하여 무한 루프 탐지에 활용합니다."""
    return json.dumps(
        {"tool_name": str(tool_name or ""), "tool_args": dict(tool_args or {})},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _render_tool_retry_feedback(state: Any) -> str:
    """에이전트에게 전달할 도구 실행 실패 피드백 메시지를 생성합니다.
    에이전트는 이 메시지를 보고 무엇이 잘못되었는지 파악하여 다시 시도합니다.
    """
    decision = getattr(state, "agent_decision", None)
    observation = getattr(state, "agent_observation", None)
    structured_refs = dict(getattr(observation, "structured_refs", {}) or {})
    generated_question = str(structured_refs.get("generated_question") or "").strip()
    summary = str(getattr(observation, "summary", "") or "").strip()
    warnings = [str(item) for item in (getattr(observation, "warnings", []) or []) if str(item).strip()]
    
    lines = [
        "[Agent Tool Observation Feedback]",
        f"tool_name: {getattr(decision, 'tool_name', None) or structured_refs.get('tool_name') or 'unknown'}",
        f"observation_type: {getattr(observation, 'observation_type', None) or 'unknown'}",
        f"warnings: {', '.join(warnings) if warnings else 'none'}",
    ]
    if generated_question:
        lines.append(f"generated_question_preview: {truncate_text(generated_question, _AGENT_QUERY_PREVIEW_LIMIT)}")
    if summary:
        lines.append(f"summary: {truncate_text(summary, 300)}")
    
    # manifest_item_not_found: 3-way recovery decision tree
    if "manifest_item_not_found" in warnings:
        from apps.conversation.agent_tool_executor import _extract_bracket_title
        _question_text = str(getattr(state, "question", "") or "").strip()
        _bracket_title = _extract_bracket_title(_question_text)
        if _bracket_title:
            lines.append(
                f"IMPORTANT: manifest_item_not_found — explicit title detected in question: '{_bracket_title}'. "
                f"Call resolve_project_title(title='{_bracket_title}'). "
                "Do NOT use search_subject_activity or search_ntis_domain."
            )
        else:
            lines.append(
                "IMPORTANT: manifest_item_not_found — the referenced item is not in the current published context.\n"
                "Recovery decision tree:\n"
                "  1. User question has explicit title in [...] or quotes → call resolve_project_title(title=<title>)\n"
                "  2. User question has explicit ID (PJT_ID/PJT_NO pattern) → call lookup_specific_entity with that ID\n"
                "  3. Only deictic references (해당/그/이) with no title or ID → call ask_user_for_clarification\n"
                "Do NOT use search_subject_activity or search_ntis_domain."
            )

    # 에이전트의 다음 행동 지침 추가
    lines.append(
        "instruction: This is an internal tool-backend observation, not user ambiguity. "
        "Choose a corrected call_tool if possible. Use ask_clarification only when the user-facing target is truly ambiguous. "
        "Use agent_internal_error if no safe retry is available."
    )
    return "\n".join(lines)


def _loop_guard_triggered(state: Any, *, tool_name: str, tool_args: Dict[str, Any]) -> bool:
    """에이전트가 동일한 인자로 도구를 반복 호출하여 루프에 빠지는 것을 방지합니다."""
    trace = list(getattr(state, "execution_trace", []) or [])
    fingerprint = _agent_tool_call_fingerprint(tool_name, tool_args)
    
    # 에이전트 단계만 필터링
    agent_calls = [
        item
        for item in trace
        if isinstance(item, dict) and item.get("stage") == "agent_tool_call"
    ]
    
    # 최대 단계 초과 또는 동일 호출 반복 시 차단
    if len(agent_calls) >= agentic_max_steps():
        return True
    return any(item.get("fingerprint") == fingerprint for item in agent_calls)


def is_short_query_exception(raw_query: str) -> bool:
    """식별자 형태(숫자 나열 등)의 짧은 쿼리를 유효한 쿼리로 간주할지 판단합니다."""
    text = str(raw_query or "").strip()
    if not text:
        return False

    # 공백 제거 후 6자리 이상의 숫자(과제번호 등)나 3자리 이상의 대문자(기관코드 등) 검사
    compact = re.sub(r"[\s\-_/]", "", text)
    if re.fullmatch(r"\d{6,}", compact):
        return True
    if re.fullmatch(r"[A-Z]{3,}", compact):
        return True
    return False


async def node_load_memory(state: Any) -> Dict[str, Any]:
    """[노드] 저장소에서 이전 대화 기록과 문맥 정보를 불러와 워크플로우 상태에 복원합니다."""
    cid = state.conversation_id
    
    # 히스토리, 증거(Evidence), 뷰 상태 등 로드
    loaded_history, canonical_evidence, render_profile, view_state, session_memory = await load_conversation_memory_from_store(
        cid,
        kv_store=getattr(state, "kv_store", None),
        logger=logger,
        truncate_text=lambda value, limit=_HISTORY_PREVIEW_LIMIT: truncate_text(value, limit),
    )
    
    # 원본 페이로드 메모리 로드 및 현재 앵커 동기화
    raw_payload_memory = await load_raw_payload_memory_from_store(
        cid,
        kv_store=getattr(state, "kv_store", None),
        logger=logger,
        truncate_text=lambda value, limit=_HISTORY_PREVIEW_LIMIT: truncate_text(value, limit),
    )
    raw_payload_memory = sync_active_anchor_record(raw_payload_memory, view_state)
    
    # 이전 문맥 복원
    effective_prev_context = rehydrate_prev_context_from_canonical_evidence(canonical_evidence)
    current_full_history = loaded_history + [state.messages[-1]]
    
    # 이번 턴의 고유 ID 생성
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
    
    # ADR-0013: 로드 시점의 메모리 상태를 관측 로그로 기록 (재현성 확보용)
    log_memory_snapshot(
        stage="load",
        request_id=state.request_id,
        conversation_id=cid,
        turn_id=turn_id,
        raw_payload_memory=raw_payload_memory,
        history_turns=len(loaded_history),
        view_state=view_state,
        kv_store=getattr(state, "kv_store", None),
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
    """[노드] 인사말이나 지나치게 짧은 쿼리 등을 분석 단계 이전에 미리 처리(Short-circuit)합니다."""
    raw_user_msg = state.messages[-1].content.strip()
    user_msg = raw_user_msg.lower()

    # 간단한 인사말 처리
    greetings = ["안녕", "hello", "hi", "여보", "반가", "헤이"]
    if any(g in user_msg for g in greetings) and len(user_msg) < 10:
        return {
            "rule_decision": RuleDecision(
                action="direct_answer",
                direct_response="안녕하세요. 무엇을 도와드릴까요?",
                reason="Simple greeting detected",
            )
        }

    # 너무 짧은 쿼리(의미 파악 불가) 차단
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


async def node_direct_answer(state: Any) -> Dict[str, Any]:
    """[노드] 사전 규칙에 의해 결정된 직접 응답을 아티팩트로 생성하여 반환합니다."""
    response_text = state.rule_decision.direct_response
    artifact = AnswerArtifact(
        text=response_text,
        answer_kind="direct_answer",
        stream_metrics={"content_chars": len(response_text or ""), "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        meta={"answer_source": "direct_answer"},
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
            "selected_model": None,
            "selected_answer_source": "direct_answer",
            "selected_answer_kind": artifact.answer_kind,
        },
        "messages": [AIMessage(content=response_text)],
    }


async def node_build_conversation_state_card(state: Any) -> Dict[str, Any]:
    """[노드] LLM 에이전트가 현재 대화의 상태를 이해할 수 있도록 구조화된 '상태 카드'를 텍스트로 렌더링합니다."""
    card_model = build_conversation_state_card_model(
        session_memory=getattr(state, "session_memory", None),
        view_state=getattr(state, "view_state", None),
        selected_answer_meta=getattr(state, "selected_answer_meta", None),
    )
    card = render_conversation_state_card(card_model)
    context_meta = _agent_context_meta_from_card_model(card_model)
    
    log_event(
        "AGENT.STATE_CARD.BUILT",
        **_agent_base_fields(state),
        card_chars=len(card),
        front_controller=True,
        **context_meta,
    )
    return {"conversation_state_card": card, "agent_context_meta": context_meta}


async def node_run_dialogue_agent(state: Any) -> Dict[str, Any]:
    """[노드] LLM 대화 에이전트를 실행하여 다음 행동(도구 호출, 직접 응답 등)을 결정합니다."""
    messages = list(getattr(state, "messages", []) or [])
    latest_content = getattr(messages[-1], "content", "") if messages else ""
    
    # 에이전트 실행 (LLM 호출)
    decision = await run_dialogue_agent(
        question=str(getattr(state, "question", None) or latest_content),
        conversation_state_card=str(getattr(state, "conversation_state_card", "") or ""),
        recent_chat=list(getattr(state, "chat_history", []) or []),
        available_tools=default_agent_tool_specs(),
        request_id=str(getattr(state, "request_id", "") or ""),
        conversation_id=str(getattr(state, "conversation_id", "") or ""),
        turn_id=str(getattr(state, "turn_id", "") or ""),
    )
    return {"agent_decision": decision}


async def node_retry_dialogue_agent_after_tool_error(state: Any) -> Dict[str, Any]:
    """[노드] 도구 실행 중 시스템 오류 발생 시, 에이전트에게 오류 원인을 알려주고 1회 재시도 기회를 줍니다."""
    retry_count = int(getattr(state, "agent_tool_retry_count", 0) or 0)
    observation = getattr(state, "agent_observation", None)
    retry_reason = _observation_reason(observation) or "agent_tool_error"
    
    # 에이전트에게 줄 피드백 생성
    feedback = _render_tool_retry_feedback(state)
    
    log_event(
        "AGENT.TOOL_RETRY.START",
        **merge_log_fields(
            _agent_base_fields(state),
            _agent_context_fields(state),
            _agent_decision_fields(getattr(state, "agent_decision", None)),
        ),
        retry_count=retry_count + 1,
        retry_reason=retry_reason,
        observation_type=getattr(observation, "observation_type", None),
        warnings=list(getattr(observation, "warnings", []) or []),
    )
    
    messages = list(getattr(state, "messages", []) or [])
    latest_content = getattr(messages[-1], "content", "") if messages else ""
    base_card = str(getattr(state, "conversation_state_card", "") or "")
    
    # 상태 카드 뒤에 실패 피드백을 덧붙여 LLM이 상황을 인지하게 함
    retry_card = f"{base_card}\n\n{feedback}".strip()
    
    decision = await run_dialogue_agent(
        question=str(getattr(state, "question", None) or latest_content),
        conversation_state_card=retry_card,
        recent_chat=list(getattr(state, "chat_history", []) or []),
        available_tools=default_agent_tool_specs(),
        request_id=str(getattr(state, "request_id", "") or ""),
        conversation_id=str(getattr(state, "conversation_id", "") or ""),
        turn_id=str(getattr(state, "turn_id", "") or ""),
    )
    
    log_event(
        "AGENT.TOOL_RETRY.DECISION",
        **merge_log_fields(
            _agent_base_fields(state),
            _agent_context_fields(state),
            _agent_decision_fields(decision),
        ),
        retry_count=retry_count + 1,
        retry_reason=retry_reason,
    )
    
    updates: Dict[str, Any] = {
        "agent_decision": decision,
        "agent_tool_retry_count": retry_count + 1,
        "agent_tool_retry_reason": retry_reason,
        "agent_tool_retry_observation": observation,
    }
    
    # ADR-0015: 재시도 시에도 이전 관측 결과의 메모(answer_note)를 보존하여 최종 응답 생성 시 활용
    retry_answer_note = getattr(observation, "answer_note", None)
    if isinstance(retry_answer_note, str) and retry_answer_note.strip():
        updates["agent_answer_context"] = AgentAnswerContext(
            note=retry_answer_note.strip(),
            source_observation_type=getattr(observation, "observation_type", None),
        )
        log_event(
            "AGENT.ANSWER_NOTE.ATTACHED",
            **_agent_base_fields(state),
            **_agent_context_fields(state),
            source_observation_type=getattr(observation, "observation_type", None),
            note_chars=len(retry_answer_note.strip()),
            retry_count=retry_count + 1,
        )
    return updates


def _agent_decision_meta(decision: Any) -> Dict[str, Any]:
    """에이전트 결정 객체를 딕셔너리 형태로 직렬화합니다."""
    if isinstance(decision, AgentDecision):
        return decision.model_dump()
    if hasattr(decision, "model_dump"):
        try:
            return dict(decision.model_dump())
        except Exception:
            pass
    if isinstance(decision, dict):
        return dict(decision)
    return {}


def _agent_observation_meta(observation: Any) -> Dict[str, Any]:
    """관측 결과 객체를 딕셔너리 형태로 직렬화합니다."""
    if isinstance(observation, AgentObservation):
        return observation.model_dump()
    if hasattr(observation, "model_dump"):
        try:
            return dict(observation.model_dump())
        except Exception:
            pass
    if isinstance(observation, dict):
        return dict(observation)
    return {}


def _coerce_agent_tool_result(result: Any) -> AgentToolExecutionResult:
    """다양한 형태의 도구 실행 결과를 표준 AgentToolExecutionResult 객체로 강제 변환합니다."""
    if isinstance(result, AgentToolExecutionResult):
        return result
    if isinstance(result, AgentObservation):
        return AgentToolExecutionResult(observation=result)
    
    observation = getattr(result, "observation", None)
    if isinstance(observation, AgentObservation):
        return AgentToolExecutionResult(
            observation=observation,
            intent_payload=getattr(result, "intent_payload", None),
            question_analysis=getattr(result, "question_analysis", None),
            next_current_context=getattr(result, "next_current_context", None),
        )
    
    # 알 수 없는 결과 형식인 경우 에러 관측 결과 생성
    return AgentToolExecutionResult(
        observation=AgentObservation(
            observation_type="error",
            summary="Agent tool returned an invalid execution result.",
            warnings=["invalid_tool_result"],
        )
    )


async def node_execute_agent_tool(state: Any) -> Dict[str, Any]:
    """[노드] 에이전트가 선택한 도구를 실제로 실행하고 그 결과를 관측 결과(Observation)로 저장합니다."""
    decision = getattr(state, "agent_decision", None)
    tool_name = str(getattr(decision, "tool_name", "") or "")
    tool_args = dict(getattr(decision, "tool_args", None) or {})
    decision_fields = _agent_decision_fields(decision)
    context_fields = _agent_context_fields(state)

    # 무한 루프 차단(Guard) 로직 실행
    if _loop_guard_triggered(state, tool_name=tool_name, tool_args=tool_args):
        observation = AgentObservation(
            observation_type="contract_violation",
            summary="Agent loop guard stopped repeated tool execution.",
            warnings=["agent_loop_guard_triggered"],
        )
        log_event(
            "AGENT.LOOP_GUARD.TRIGGERED",
            **merge_log_fields(
                _agent_base_fields(state),
                context_fields,
                decision_fields,
            ),
        )
        return {
            "agent_observation": observation,
            "agent_loop_guard_triggered": True,
            "agent_tool_intent_payload": None,
            "agent_tool_question_analysis": None,
        }

    log_event(
        "AGENT.TOOL_CALL",
        **merge_log_fields(
            _agent_base_fields(state),
            context_fields,
            decision_fields,
        ),
    )
    
    # 실행 추적(Execution Trace) 기록
    execution_trace = list(getattr(state, "execution_trace", []) or [])
    execution_trace.append(
        {
            "stage": "agent_tool_call",
            "tool_name": tool_name,
            "fingerprint": _agent_tool_call_fingerprint(tool_name, tool_args),
        }
    )
    
    # 도구 실제 실행 (백엔드 로직 호출)
    result = _coerce_agent_tool_result(
        await execute_agent_tool(tool_name=tool_name, tool_args=tool_args, state=state)
    )
    
    observation = result.observation
    structured_refs = dict(getattr(observation, "structured_refs", {}) or {})
    generated_question = str(structured_refs.get("generated_question") or "").strip()
    strategy_meta = (
        dict(getattr(result.intent_payload, "strategy_meta", {}) or {})
        if getattr(result, "intent_payload", None) is not None
        else {}
    )
    
    updates: Dict[str, Any] = {
        "agent_observation": observation,
        "agent_tool_intent_payload": result.intent_payload,
        "agent_tool_question_analysis": result.question_analysis,
        "execution_trace": execution_trace,
    }
    
    if getattr(result, "next_current_context", None) is not None:
        updates["next_current_context"] = result.next_current_context
    
    # ADR-0015: 관측 결과의 answer_note를 응답 문맥으로 전달
    answer_note_value = getattr(observation, "answer_note", None)
    if isinstance(answer_note_value, str) and answer_note_value.strip():
        updates["agent_answer_context"] = AgentAnswerContext(
            note=answer_note_value.strip(),
            source_observation_type=observation.observation_type,
        )
        log_event(
            "AGENT.ANSWER_NOTE.ATTACHED",
            **_agent_base_fields(state),
            **context_fields,
            tool_name=tool_name,
            source_observation_type=observation.observation_type,
            note_chars=len(answer_note_value.strip()),
        )
    
    # 플래너 의도가 성공적으로 계획된 경우 상태에 반영
    if observation.observation_type == "planned_intent" and result.intent_payload and result.question_analysis:
        updates["intent_payload"] = result.intent_payload
        updates["question_analysis"] = result.question_analysis
    
    log_event(
        "AGENT.TOOL_OBSERVATION",
        **_agent_base_fields(state),
        **context_fields,
        tool_name=tool_name,
        observation_type=observation.observation_type,
        warnings=list(observation.warnings or []),
        has_intent_payload=bool(result.intent_payload),
        has_question_analysis=bool(result.question_analysis),
        staged_current_context_type=getattr(getattr(result, "next_current_context", None), "context_type", None),
        tool_execution_source=structured_refs.get("tool_execution_source") or strategy_meta.get("tool_execution_source"),
        planner_llm_skipped=strategy_meta.get("planner_llm_skipped"),
        publication_status=strategy_meta.get("direct_compile_publication_status"),
        structured_ref_keys=sorted(structured_refs.keys()),
        generated_question_chars=len(generated_question) if generated_question else None,
        generated_question_preview=truncate_text(generated_question, _AGENT_QUERY_PREVIEW_LIMIT)
        if generated_question
        else None,
    )
    
    # 계약 위반(보안 차단 등) 로그
    if observation.observation_type == "contract_violation":
        log_event(
            "AGENT.CONTRACT_BLOCKED",
            **_agent_base_fields(state),
            **context_fields,
            tool_name=tool_name,
            contract_block_reason=_observation_reason(observation),
            warnings=list(observation.warnings or []),
        )
    return updates


async def node_agent_direct_answer(state: Any) -> Dict[str, Any]:
    """[노드] 에이전트가 직접 답변하기로 결정한 경우, 라우팅 마커만 세팅하고
    실제 답변 본문 생성은 후속 generate_answer_gemma/solar(Triton/vLLM)에 위임한다."""
    decision = getattr(state, "agent_decision", None)

    log_event(
        "AGENT.DIRECT_ANSWER",
        **merge_log_fields(
            _agent_base_fields(state),
            _agent_context_fields(state),
            _agent_decision_fields(decision),
        ),
    )

    return {
        "direct_answer_mode": True,
        "next_current_context": EmptyContext(),
    }


async def node_agent_clarification(state: Any) -> Dict[str, Any]:
    """[노드] 사용자의 질문이 모호하여 에이전트가 추가 정보를 요청(Clarification)하는 경우 처리합니다."""
    decision = getattr(state, "agent_decision", None)
    observation = getattr(state, "agent_observation", None)
    decision_type = str(getattr(decision, "decision_type", "") or "").strip()
    observation_type = str(getattr(observation, "observation_type", "") or "")
    tool_observation_clarification = observation_type == "clarification_required"
    
    # 예외 상황: 에이전트가 모호함 해소를 선택하지 않았는데 이 노드로 온 경우 에러 처리
    if decision_type != "ask_clarification" and not tool_observation_clarification:
        log_event(
            "AGENT.CLARIFICATION.BLOCKED",
            **merge_log_fields(
                _agent_base_fields(state),
                _agent_context_fields(state),
                _agent_decision_fields(decision),
            ),
            observation_type=str(getattr(observation, "observation_type", "") or ""),
            blocked_reason=_observation_reason(observation) or "non_agent_clarification_path",
        )
        return await node_agent_internal_error(state)

    # 내부 도구 오류가 깔려있는 경우 모호함 해소 대신 에러 응답으로 전환 (사용자 탓이 아니므로)
    if _has_internal_tool_error_context(state, observation):
        log_event(
            "AGENT.CLARIFICATION.BLOCKED",
            **merge_log_fields(
                _agent_base_fields(state),
                _agent_context_fields(state),
                _agent_decision_fields(decision),
            ),
            observation_type=observation_type,
            blocked_reason=_observation_reason(observation) or getattr(state, "agent_tool_retry_reason", None) or "internal_tool_error",
            internal_error_boundary=1,
        )
        return await node_agent_internal_error(state)

    # 응답 텍스트 결정 순서: 에이전트가 생성한 질문 -> 관측 결과의 요약 -> 기본 방어 문구
    decision_question = str(getattr(decision, "clarification_question", None) or "").strip()
    observation_summary = str(getattr(observation, "summary", None) or "").strip()
    
    if decision_question:
        response_text = decision_question
    elif observation_type == "contract_violation":
        response_text = "요청을 안전하게 실행할 수 없습니다. 조회할 대상이나 조건을 더 구체적으로 지정해 주세요."
    elif observation_summary:
        response_text = observation_summary
    else:
        response_text = "조회 대상을 안전하게 특정하려면 대상 이름, ID, 기간 같은 조건을 더 구체적으로 알려 주세요."

    structured_refs = dict(getattr(observation, "structured_refs", None) or {})
    if tool_observation_clarification and decision_type != "ask_clarification":
        tool_args = structured_refs
        clarification_candidates = list(structured_refs.get("candidates") or [])
        clarification_source = "tool_clarification_required"
    else:
        tool_args = dict(getattr(decision, "tool_args", None) or {})
        clarification_candidates = []
        clarification_source = "agent_clarification"

    artifact = AnswerArtifact(
        text=response_text,
        answer_kind="clarification",
        stream_metrics={"content_chars": len(response_text), "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        clarification=ClarificationRequest(
            clarification_type=clarification_source,
            message=response_text,
            candidates=clarification_candidates,
            resume_token={"tool_args": tool_args} if tool_args else {},
        ),
        meta={
            "answer_source": clarification_source,
            "model_key": "agent",
            "agent_decision": _agent_decision_meta(decision),
            "agent_observation": _agent_observation_meta(observation),
        },
    )
    
    log_event(
        "AGENT.CLARIFICATION",
        **merge_log_fields(
            _agent_base_fields(state),
            _agent_context_fields(state),
            _agent_decision_fields(decision),
        ),
        observation_type=observation_type,
        clarification_reason=(
            str(getattr(decision, "reasoning_summary", "") or "")
            if observation_type == "error"
            else (_observation_reason(observation) or str(getattr(decision, "reasoning_summary", "") or ""))
        ),
        answer_chars=len(response_text),
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
        "next_current_context": ClarificationContext(
            reason=clarification_source,
            unresolved_question=str(getattr(state, "question", "") or ""),
            unresolved_constraints=tool_args,
        ),
        "merge_debug": {
            "selected_model": "agent",
            "selected_answer_source": clarification_source,
            "selected_answer_kind": artifact.answer_kind,
        },
        "messages": [AIMessage(content=response_text)],
    }


async def node_agent_internal_error(state: Any) -> Dict[str, Any]:
    """[노드] 에이전트 실행이나 도구 호출 중 복구 불가능한 시스템 오류 발생 시 안내 메시지를 보냅니다."""
    decision = getattr(state, "agent_decision", None)
    observation = getattr(state, "agent_observation", None)
    decision_type = str(getattr(decision, "decision_type", "") or "").strip()
    
    internal_reason = (
        str(getattr(decision, "reasoning_summary", "") or "").strip()
        if decision_type == "agent_internal_error"
        else ""
    )
    if not internal_reason:
        internal_reason = _observation_reason(observation) or "agent_internal_error"

    # 마지막 관측 경고를 사용자 가시 메시지에 반영하여 단순 "문제 발생" 대신 원인별 안내로 분기
    last_warnings = {str(w) for w in (getattr(observation, "warnings", None) or [])}
    retry_reason = str(getattr(state, "agent_tool_retry_reason", "") or "").strip()
    if "manifest_item_not_found" in last_warnings or retry_reason == "manifest_item_not_found":
        response_text = (
            "참조하신 항목을 현재 발행된 목록에서 찾지 못했습니다. "
            "항목 번호 대신 과제명이나 ID를 직접 알려 주시거나, "
            "새로운 검색 조건으로 다시 시도해 주세요."
        )
    elif "agent_loop_guard_triggered" in last_warnings:
        response_text = (
            "동일한 조회가 반복되어 안전을 위해 작업을 중단했습니다. "
            "질문을 다시 작성하거나 조건을 더 구체적으로 알려 주세요."
        )
    else:
        response_text = "요청을 처리하는 중 문제가 발생했습니다. 다시 시도해 주세요."
    
    # 다음 문맥은 현재 문맥을 유지하여 사용자가 다시 시도할 수 있게 함
    session_memory = getattr(state, "session_memory", None)
    next_context = session_memory.current_context if isinstance(session_memory, SessionMemory) else EmptyContext()
    
    artifact = AnswerArtifact(
        text=response_text,
        answer_kind="error",
        stream_metrics={"content_chars": len(response_text), "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        error=ErrorArtifact(
            error_code="AGENT_INTERNAL_ERROR",
            reason=internal_reason,
            retryable=True,
        ),
        meta={
            "answer_source": "agent_internal_error",
            "model_key": "agent",
            "agent_decision": _agent_decision_meta(decision),
            "agent_observation": _agent_observation_meta(observation),
        },
    )
    
    log_event(
        "AGENT.INTERNAL_ERROR",
        **merge_log_fields(
            _agent_base_fields(state),
            _agent_context_fields(state),
            _agent_decision_fields(decision),
        ),
        observation_type=str(getattr(observation, "observation_type", "") or ""),
        internal_error_reason=internal_reason,
        retry_count=int(getattr(state, "agent_tool_retry_count", 0) or 0),
        answer_chars=len(response_text),
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
        "next_current_context": next_context,
        "merge_debug": {
            "selected_model": "agent",
            "selected_answer_source": "agent_internal_error",
            "selected_answer_kind": artifact.answer_kind,
        },
        "messages": [AIMessage(content=response_text)],
    }


async def node_render_anchor_answer(state: Any) -> Dict[str, Any]:
    """[노드] resolve_project_title 결과(GroupAnchorContext)를 사람이 읽을 수 있는 텍스트로 렌더링합니다."""
    observation = getattr(state, "agent_observation", None)
    refs = dict(getattr(observation, "structured_refs", {}) or {})

    title = refs.get("title") or "제목 미상"
    pjt_no = refs.get("pjt_no") or "-"
    years = list(refs.get("years") or [])
    lead_researcher = refs.get("lead_researcher") or "미확인"
    instance_count = refs.get("instance_count") or 0

    year_range = f"{min(years)}–{max(years)}" if years else "미확인"
    lines = [
        f"**{title}**",
        f"- 과제 그룹 번호: {pjt_no}",
        f"- 연도 범위: {year_range}",
        f"- 연구책임자: {lead_researcher}",
        f"- 확인 인스턴스: {instance_count}건",
    ]
    response_text = "\n".join(lines)

    artifact = AnswerArtifact(
        text=response_text,
        answer_kind="direct_answer",
        stream_metrics={"content_chars": len(response_text), "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        meta={
            "answer_source": "render_anchor_answer",
            "model_key": "agent",
            "pjt_no": pjt_no,
        },
    )

    log_event(
        "AGENT.RENDER_ANCHOR_ANSWER",
        **_agent_base_fields(state),
        pjt_no=pjt_no,
        title=title,
        instance_count=instance_count,
        answer_chars=len(response_text),
    )

    next_ctx = getattr(state, "next_current_context", None)
    return {
        "answer_gemma": response_text,
        "answer_solar": response_text,
        "answer_artifact_gemma": artifact,
        "answer_artifact_solar": artifact,
        "answer_artifact": artifact,
        "final_answer_text": response_text,
        "final_answer_artifact": artifact,
        "selected_answer_meta": artifact.to_meta_dict(),
        "next_current_context": next_ctx,
        "merge_debug": {
            "selected_model": "agent",
            "selected_answer_source": "render_anchor_answer",
            "selected_answer_kind": artifact.answer_kind,
        },
        "messages": [AIMessage(content=response_text)],
    }


async def node_render_participant_answer(state: Any) -> Dict[str, Any]:
    """[노드] extract_project_participants 결과를 사람이 읽을 수 있는 텍스트로 렌더링합니다."""
    observation = getattr(state, "agent_observation", None)
    refs = dict(getattr(observation, "structured_refs", {}) or {})

    pjt_no = refs.get("pjt_no") or "-"
    project_title = refs.get("project_title") or ""
    instances_checked = refs.get("instances_checked") or 0
    participant_count = refs.get("participant_count") or 0
    participants = list(refs.get("participants") or [])

    header_parts = [
        f"확인 범위: pjt_no={pjt_no} 동일 과제 그룹, {instances_checked}개 연도 인스턴스",
    ]
    if project_title:
        header_parts.append(f"과제명: {project_title}")
    header_parts.append(f"참여 연구자 ({participant_count}명):")
    header = "\n".join(header_parts)

    rows = []
    for i, p in enumerate(participants, 1):
        name = p.get("hm_nm") or "이름 미상"
        roles = ", ".join(p.get("roles") or []) or "미확인"
        affiliations = ", ".join(p.get("affiliations") or []) or "미확인"
        yrs = ", ".join(sorted(p.get("years") or []))
        rows.append(
            f"{i}. {name}\n"
            f"   - 역할: {roles}\n"
            f"   - 소속: {affiliations}\n"
            f"   - 확인 연도: {yrs}"
        )

    footer = f"\n현재 DB의 prtcp_mp[] 기준으로 총 {participant_count}명입니다."
    response_text = header + "\n" + "\n".join(rows) + footer

    artifact = AnswerArtifact(
        text=response_text,
        answer_kind="direct_answer",
        stream_metrics={"content_chars": len(response_text), "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        meta={
            "answer_source": "render_participant_answer",
            "model_key": "agent",
            "pjt_no": pjt_no,
            "participant_count": participant_count,
        },
    )

    log_event(
        "AGENT.RENDER_PARTICIPANT_ANSWER",
        **_agent_base_fields(state),
        pjt_no=pjt_no,
        instances_checked=instances_checked,
        participant_count=participant_count,
        answer_chars=len(response_text),
    )

    next_ctx = getattr(state, "next_current_context", None)
    return {
        "answer_gemma": response_text,
        "answer_solar": response_text,
        "answer_artifact_gemma": artifact,
        "answer_artifact_solar": artifact,
        "answer_artifact": artifact,
        "final_answer_text": response_text,
        "final_answer_artifact": artifact,
        "selected_answer_meta": artifact.to_meta_dict(),
        "next_current_context": next_ctx,
        "merge_debug": {
            "selected_model": "agent",
            "selected_answer_source": "render_participant_answer",
            "selected_answer_kind": artifact.answer_kind,
        },
        "messages": [AIMessage(content=response_text)],
    }


async def node_save_history(state: Any) -> Dict[str, Any]:
    """[노드] 이번 턴의 대화 내용, 증거 데이터, 뷰 상태 등을 저장소에 영구 저장(Persist)합니다."""
    kv_store = getattr(state, "kv_store", None)
    
    # 저장할 페이로드 구성
    save_payload = build_save_history_payload(state, max_history_turns=_MAX_HISTORY_TURNS)
    cid = str(save_payload["conversation_id"] or "")
    
    # 원본 페이로드 메모리와 현재 활성 앵커 동기화
    raw_payload_memory = sync_active_anchor_record(
        getattr(state, "raw_payload_memory", None) or {},
        save_payload["view_state"],
    )

    # 영구 저장 실행
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
        logger.debug("[memory] kv_store unavailable: skip history/context save (cid={})", cid)
    if not raw_saved:
        logger.debug("[memory] kv_store unavailable: skip raw payload save (cid={})", cid)

    # ADR-0013: 저장 직후 메모리 스냅샷 기록 (실제 저장된 상태 확인용)
    log_memory_snapshot(
        stage="save",
        request_id=getattr(state, "request_id", None),
        conversation_id=cid,
        turn_id=str(getattr(state, "turn_id", "") or "") or None,
        raw_payload_memory=raw_payload_memory,
        history_turns=len(save_payload.get("history") or []),
        view_state=save_payload.get("view_state"),
        note=None if (saved and raw_saved) else "kv_store_unavailable",
        kv_store=getattr(state, "kv_store", None),
    )

    # 전체 요청 실행 요약 로그 (REQ.SUMMARY)
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
    """[노드] 병렬 실행 노드들을 하나로 합치는 명시적 합류 노드입니다 (기능적 No-op)."""
    return {}
