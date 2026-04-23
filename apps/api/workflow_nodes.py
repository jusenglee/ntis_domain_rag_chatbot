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

_MAX_HISTORY_TURNS = 10
_HISTORY_PREVIEW_LIMIT = 100
_AGENT_QUERY_PREVIEW_LIMIT = 80


def _agent_base_fields(state: Any) -> Dict[str, Any]:
    return {
        "request_id": getattr(state, "request_id", None),
        "conversation_id": getattr(state, "conversation_id", None),
        "turn_id": getattr(state, "turn_id", None),
    }


def _agent_context_meta_from_card_model(card_model: Any) -> Dict[str, Any]:
    constraints = dict(getattr(card_model, "unresolved_constraints", {}) or {})
    return {
        "current_context_type": getattr(card_model, "current_context_type", None),
        "subject_kind": getattr(card_model, "current_subject_kind", None),
        "subject_name": getattr(card_model, "current_subject_name", None),
        "refinement_allowed": bool(getattr(card_model, "refinement_allowed", False)),
        "last_publication_status": getattr(card_model, "last_publication_status", None),
        "unresolved_constraint_keys": sorted(constraints.keys()),
    }


def _agent_context_fields(state: Any) -> Dict[str, Any]:
    meta = getattr(state, "agent_context_meta", None)
    if isinstance(meta, dict) and meta:
        return {
            "current_context_type": meta.get("current_context_type"),
            "subject_kind": meta.get("subject_kind"),
            "subject_name": meta.get("subject_name"),
            "refinement_allowed": meta.get("refinement_allowed"),
            "last_publication_status": meta.get("last_publication_status"),
        }
    return {}


def _safe_agent_tool_arg_fields(tool_args: Dict[str, Any]) -> Dict[str, Any]:
    args = dict(tool_args or {})
    fields: Dict[str, Any] = {}
    for key in (
        "subject_ref",
        "domain_head",
        "people_name",
        "org_name",
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
    query = str(args.get("query") or "").strip()
    if query:
        fields["query_chars"] = len(query)
        fields["query_preview"] = truncate_text(query, _AGENT_QUERY_PREVIEW_LIMIT)
    return fields


def _agent_decision_fields(decision: Any) -> Dict[str, Any]:
    return {
        "decision_type": getattr(decision, "decision_type", None),
        "tool_name": getattr(decision, "tool_name", None),
        "confidence": round(float(getattr(decision, "confidence", 0.0) or 0.0), 3),
        **_safe_agent_tool_arg_fields(dict(getattr(decision, "tool_args", None) or {})),
    }


def _observation_reason(observation: Any) -> str | None:
    warnings = list(getattr(observation, "warnings", []) or [])
    if warnings:
        return str(warnings[0])
    summary = str(getattr(observation, "summary", "") or "").strip()
    return summary or None


def _agent_tool_call_fingerprint(tool_name: str, tool_args: Dict[str, Any]) -> str:
    return json.dumps(
        {"tool_name": str(tool_name or ""), "tool_args": dict(tool_args or {})},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _render_tool_retry_feedback(state: Any) -> str:
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
    lines.append(
        "instruction: This is an internal tool-backend observation, not user ambiguity. "
        "Choose a corrected call_tool if possible. Use ask_clarification only when the user-facing target is truly ambiguous. "
        "Use agent_internal_error if no safe retry is available."
    )
    return "\n".join(lines)


def _loop_guard_triggered(state: Any, *, tool_name: str, tool_args: Dict[str, Any]) -> bool:
    trace = list(getattr(state, "execution_trace", []) or [])
    fingerprint = _agent_tool_call_fingerprint(tool_name, tool_args)
    agent_calls = [
        item
        for item in trace
        if isinstance(item, dict) and item.get("stage") == "agent_tool_call"
    ]
    if len(agent_calls) >= agentic_max_steps():
        return True
    return any(item.get("fingerprint") == fingerprint for item in agent_calls)


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


async def node_build_conversation_state_card(state: Any) -> Dict[str, Any]:
    """Build the LLM-readable state card from official session memory."""

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
    """Run the LLM-first dialogue agent and store its decision."""

    messages = list(getattr(state, "messages", []) or [])
    latest_content = getattr(messages[-1], "content", "") if messages else ""
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
    """Give one compact tool-backend observation back to the Agent for retry."""

    retry_count = int(getattr(state, "agent_tool_retry_count", 0) or 0)
    observation = getattr(state, "agent_observation", None)
    retry_reason = _observation_reason(observation) or "agent_tool_error"
    feedback = _render_tool_retry_feedback(state)
    log_event(
        "AGENT.TOOL_RETRY.START",
        **_agent_base_fields(state),
        **_agent_context_fields(state),
        **_agent_decision_fields(getattr(state, "agent_decision", None)),
        retry_count=retry_count + 1,
        retry_reason=retry_reason,
        observation_type=getattr(observation, "observation_type", None),
        warnings=list(getattr(observation, "warnings", []) or []),
    )
    messages = list(getattr(state, "messages", []) or [])
    latest_content = getattr(messages[-1], "content", "") if messages else ""
    base_card = str(getattr(state, "conversation_state_card", "") or "")
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
        **_agent_base_fields(state),
        **_agent_context_fields(state),
        **_agent_decision_fields(decision),
        retry_count=retry_count + 1,
        retry_reason=retry_reason,
    )
    updates: Dict[str, Any] = {
        "agent_decision": decision,
        "agent_tool_retry_count": retry_count + 1,
        "agent_tool_retry_reason": retry_reason,
        "agent_tool_retry_observation": observation,
    }
    # ADR-0015 C1 Option B: 재시도 시점에도 observation.answer_note 보존.
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
        )
    return AgentToolExecutionResult(
        observation=AgentObservation(
            observation_type="error",
            summary="Agent tool returned an invalid execution result.",
            warnings=["invalid_tool_result"],
        )
    )


async def node_execute_agent_tool(state: Any) -> Dict[str, Any]:
    """Execute a validated agent tool and expose guarded planner outputs."""

    decision = getattr(state, "agent_decision", None)
    tool_name = str(getattr(decision, "tool_name", "") or "")
    tool_args = dict(getattr(decision, "tool_args", None) or {})
    decision_fields = _agent_decision_fields(decision)
    context_fields = _agent_context_fields(state)

    if _loop_guard_triggered(state, tool_name=tool_name, tool_args=tool_args):
        observation = AgentObservation(
            observation_type="contract_violation",
            summary="Agent loop guard stopped repeated tool execution.",
            warnings=["agent_loop_guard_triggered"],
        )
        log_event(
            "AGENT.LOOP_GUARD.TRIGGERED",
            **_agent_base_fields(state),
            **context_fields,
            **decision_fields,
        )
        return {
            "agent_observation": observation,
            "agent_loop_guard_triggered": True,
            "agent_tool_intent_payload": None,
            "agent_tool_question_analysis": None,
        }

    log_event(
        "AGENT.TOOL_CALL",
        **_agent_base_fields(state),
        **context_fields,
        **decision_fields,
    )
    execution_trace = list(getattr(state, "execution_trace", []) or [])
    execution_trace.append(
        {
            "stage": "agent_tool_call",
            "tool_name": tool_name,
            "fingerprint": _agent_tool_call_fingerprint(tool_name, tool_args),
        }
    )
    result = _coerce_agent_tool_result(
        await execute_agent_tool(tool_name=tool_name, tool_args=tool_args, state=state)
    )
    observation = result.observation
    structured_refs = dict(getattr(observation, "structured_refs", {}) or {})
    generated_question = str(structured_refs.get("generated_question") or "").strip()
    updates: Dict[str, Any] = {
        "agent_observation": observation,
        "agent_tool_intent_payload": result.intent_payload,
        "agent_tool_question_analysis": result.question_analysis,
        "execution_trace": execution_trace,
    }
    # ADR-0015 C1 Option B: observation.answer_note → AgentAnswerContext 전달.
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
        structured_ref_keys=sorted(structured_refs.keys()),
        generated_question_chars=len(generated_question) if generated_question else None,
        generated_question_preview=truncate_text(generated_question, _AGENT_QUERY_PREVIEW_LIMIT)
        if generated_question
        else None,
    )
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
    """Publish a direct answer chosen by the dialogue agent."""

    decision = getattr(state, "agent_decision", None)
    response_text = str(getattr(decision, "response_text", None) or "").strip()
    if not response_text:
        response_text = "요청을 처리하려면 질문을 조금 더 구체적으로 작성해 주세요."
    artifact = AnswerArtifact(
        text=response_text,
        answer_kind="direct_answer",
        stream_metrics={"content_chars": len(response_text), "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        meta={
            "answer_source": "agent_direct_answer",
            "model_key": "agent",
            "agent_decision": _agent_decision_meta(decision),
        },
    )
    log_event(
        "AGENT.DIRECT_ANSWER",
        **_agent_base_fields(state),
        **_agent_context_fields(state),
        **_agent_decision_fields(decision),
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
        "next_current_context": EmptyContext(),
        "merge_debug": {
            "selected_model": "agent",
            "selected_answer_source": "agent_direct_answer",
            "selected_answer_kind": artifact.answer_kind,
        },
        "messages": [AIMessage(content=response_text)],
    }


async def node_agent_clarification(state: Any) -> Dict[str, Any]:
    """Publish an agent clarification or contract-block response."""

    decision = getattr(state, "agent_decision", None)
    observation = getattr(state, "agent_observation", None)
    decision_type = str(getattr(decision, "decision_type", "") or "").strip()
    if decision_type != "ask_clarification":
        log_event(
            "AGENT.CLARIFICATION.BLOCKED",
            **_agent_base_fields(state),
            **_agent_context_fields(state),
            **_agent_decision_fields(decision),
            observation_type=str(getattr(observation, "observation_type", "") or ""),
            blocked_reason=_observation_reason(observation) or "non_agent_clarification_path",
        )
        return await node_agent_internal_error(state)

    observation_type = str(getattr(observation, "observation_type", "") or "")
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

    tool_args = dict(getattr(decision, "tool_args", None) or {})
    artifact = AnswerArtifact(
        text=response_text,
        answer_kind="clarification",
        stream_metrics={"content_chars": len(response_text), "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        clarification=ClarificationRequest(
            clarification_type="agent_clarification",
            message=response_text,
            candidates=[],
            resume_token={"tool_args": tool_args} if tool_args else {},
        ),
        meta={
            "answer_source": "agent_clarification",
            "model_key": "agent",
            "agent_decision": _agent_decision_meta(decision),
            "agent_observation": _agent_observation_meta(observation),
        },
    )
    log_event(
        "AGENT.CLARIFICATION",
        **_agent_base_fields(state),
        **_agent_context_fields(state),
        **_agent_decision_fields(decision),
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
            reason="agent_clarification",
            unresolved_question=str(getattr(state, "question", "") or ""),
            unresolved_constraints=tool_args,
        ),
        "merge_debug": {
            "selected_model": "agent",
            "selected_answer_source": "agent_clarification",
            "selected_answer_kind": artifact.answer_kind,
        },
        "messages": [AIMessage(content=response_text)],
    }


async def node_agent_internal_error(state: Any) -> Dict[str, Any]:
    """Publish a generic internal-error artifact without creating clarification state."""

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
    response_text = "요청을 처리하는 중 문제가 발생했습니다. 다시 시도해 주세요."
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
        **_agent_base_fields(state),
        **_agent_context_fields(state),
        **_agent_decision_fields(decision),
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
        logger.debug("[memory] kv_store unavailable: skip history/context save (cid={})", cid)
    if not raw_saved:
        logger.debug("[memory] kv_store unavailable: skip raw payload save (cid={})", cid)

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
