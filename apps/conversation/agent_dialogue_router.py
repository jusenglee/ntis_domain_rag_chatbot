"""
대화의 제어권을 쥐고 사용자의 의도를 분석하여 다음 행동을 결정하는 'Dialogue Agent'의 실행 엔진입니다.

[설계 의도: ADR-0001 LLM-First Dialogue Agent]
이 모듈은 시스템의 'Front-Controller' 역할을 수행합니다. 사용자의 자연어 질문을 
규칙 기반으로 처리하던 과거 방식에서 벗어나, LLM이 현재 대화 상태(ConversationStateCard)와 
이전 기록(Recent Chat)을 종합적으로 판단하여 최적의 도구(Tool)를 선택하거나 
사용자에게 되묻는(Clarification) 결정을 내립니다.

주요 역할:
1. Decision Making: AgentDecision 구조체를 통해 도구 호출, 직접 답변, 보정 요청 등을 결정합니다.
2. Self-Repair: LLM이 구조화된 JSON 형식을 어겼을 경우, 오류 내용을 바탕으로 스스로 형식을 수정하여 
   재시도하는 복원 로직을 포함합니다.
3. Observability: 에이전트가 왜 그런 결정을 내렸는지에 대한 추론 근거(Reasoning Summary)를 
   로그로 남겨 시스템의 가독성을 높입니다.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from pydantic import ValidationError

from apps.api.runtime_helpers import log_event
from apps.conversation.agent_contracts import AgentDecision
from apps.conversation.agent_dialogue_router_validation import (
    AgentDecisionValidationError,
    validate_agent_decision,
)
from apps.conversation.agent_flags import agentic_decision_repair_max_attempts
from apps.conversation.agent_tools import AgentToolSpec, default_agent_tool_specs
from apps.platform.langchain_compat import BaseMessage, HumanMessage, SystemMessage
from apps.planner.prompt_asset_paths import planner_prompt_path


AgentModelInvoker = Callable[[List[BaseMessage]], Awaitable[Any] | Any]
_AGENT_PROMPT_VERSION = "dialogue_agent_v1"
_AGENT_REPAIR_PROMPT_VERSION = "dialogue_agent_decision_repair_v1"
_AGENT_MODEL_NAME = "solar_vllm_0"
_RAW_LOG_LIMIT = 2000


async def _load_agent_prompt() -> str:
    path = planner_prompt_path("dialogue_agent_v1.md")
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


async def _load_repair_prompt() -> str:
    path = planner_prompt_path("dialogue_agent_decision_repair_v1.md")
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


def _tool_payload(tools: Iterable[AgentToolSpec]) -> str:
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "implemented": tool.implemented,
        }
        for tool in tools
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _recent_chat_payload(recent_chat: List[BaseMessage], *, max_items: int = 6) -> str:
    items: List[Dict[str, str]] = []
    for message in list(recent_chat or [])[-max_items:]:
        role = type(message).__name__.replace("Message", "").lower() or "message"
        content = str(getattr(message, "content", message) or "").strip()
        if content:
            items.append({"role": role, "content": content[:800]})
    return json.dumps(items, ensure_ascii=False)


def _message_content(value: Any) -> str:
    if hasattr(value, "content"):
        return str(getattr(value, "content") or "")
    return str(value or "")


def _raw_output_text(value: Any) -> str:
    if isinstance(value, AgentDecision):
        return value.model_dump_json()
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(value)
    return _message_content(value)


def _truncate_raw_output(value: Any) -> str:
    return _raw_output_text(value)[:_RAW_LOG_LIMIT]


def _decision_from_payload(value: Any) -> AgentDecision:
    if isinstance(value, AgentDecision):
        return value
    if isinstance(value, dict):
        return AgentDecision.model_validate(value)

    from apps.chat.llm_json import sanitize_llm_json

    text = _message_content(value)
    json_text = sanitize_llm_json(text)
    return AgentDecision.model_validate_json(json_text)


def _agent_internal_error_decision(*, reason: str) -> AgentDecision:
    return AgentDecision(
        decision_type="agent_internal_error",
        confidence=0.0,
        reasoning_summary=reason,
    )


def _safe_tool_arg_fields(tool_args: Dict[str, Any]) -> Dict[str, Any]:
    args = dict(tool_args or {})
    fields: Dict[str, Any] = {}
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

    query = str(args.get("query") or "").strip()
    if query:
        fields["query_chars"] = len(query)
        fields["query_preview"] = query[:80] + ("..." if len(query) > 80 else "")
    return fields


def _context_type_from_card(conversation_state_card: str) -> Optional[str]:
    for line in str(conversation_state_card or "").splitlines():
        if line.strip().startswith("current_context_type:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _subject_fields_from_card(conversation_state_card: str) -> Dict[str, Optional[str]]:
    for line in str(conversation_state_card or "").splitlines():
        text = line.strip()
        if not text.startswith("current subject:"):
            continue
        value = text.split(":", 1)[1].strip()
        if value == "none confirmed":
            return {"subject_name": None, "subject_kind": None}
        if value.endswith(")") and "(" in value:
            subject_name, subject_kind = value.rsplit("(", 1)
            return {
                "subject_name": subject_name.strip(),
                "subject_kind": subject_kind[:-1].strip() or None,
            }
        return {"subject_name": value or None, "subject_kind": None}
    return {"subject_name": None, "subject_kind": None}


def _log_agent_decision(
    decision: AgentDecision,
    *,
    request_id: str,
    conversation_id: str,
    turn_id: str,
    conversation_state_card: str,
) -> None:
    try:
        log_event(
            "AGENT.DECISION",
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            decision_type=decision.decision_type,
            tool_name=decision.tool_name,
            confidence=round(float(decision.confidence), 3),
            current_context_type=_context_type_from_card(conversation_state_card),
            **_subject_fields_from_card(conversation_state_card),
            reasoning_summary=decision.reasoning_summary,
            **_safe_tool_arg_fields(decision.tool_args),
        )
    except Exception:
        pass


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return str(value)


def _validation_error_info(exc: Exception) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(exc, AgentDecisionValidationError):
        return exc.error_type, _json_safe(exc.validation_errors)
    if isinstance(exc, ValidationError):
        return "ValidationError", _json_safe(exc.errors())
    return type(exc).__name__, [{"msg": str(exc), "type": type(exc).__name__}]


def _log_agent_parse_error(
    *,
    exc: Exception,
    raw_output: Any,
    request_id: str,
    conversation_id: str,
    turn_id: str,
) -> None:
    error_type, validation_errors = _validation_error_info(exc)
    try:
        raw_text = _raw_output_text(raw_output)
        log_event(
            "AGENT.PARSE_ERROR",
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            error_type=error_type,
            validation_errors=validation_errors,
            raw_output=raw_text[:_RAW_LOG_LIMIT],
            raw_output_chars=len(raw_text),
            prompt_version=_AGENT_PROMPT_VERSION,
            model_name=_AGENT_MODEL_NAME,
        )
    except Exception:
        pass


def _log_agent_invoke_error(
    *,
    exc: Exception,
    request_id: str,
    conversation_id: str,
    turn_id: str,
) -> None:
    try:
        log_event(
            "AGENT.INVOKE_ERROR",
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],
            prompt_version=_AGENT_PROMPT_VERSION,
            model_name=_AGENT_MODEL_NAME,
        )
    except Exception:
        pass


def _log_self_repair(
    event_name: str,
    *,
    request_id: str,
    conversation_id: str,
    turn_id: str,
    attempt: int,
    exc: Exception | None = None,
    raw_output: Any = None,
    repaired_decision: AgentDecision | None = None,
) -> None:
    try:
        payload: Dict[str, Any] = {
            "request_id": request_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "attempt": attempt,
            "prompt_version": _AGENT_REPAIR_PROMPT_VERSION,
            "model_name": _AGENT_MODEL_NAME,
        }
        if exc is not None:
            error_type, validation_errors = _validation_error_info(exc)
            payload.update(
                {
                    "error_type": error_type,
                    "validation_errors": validation_errors,
                }
            )
        if raw_output is not None:
            raw_text = _raw_output_text(raw_output)
            payload.update({"raw_output": raw_text[:_RAW_LOG_LIMIT], "raw_output_chars": len(raw_text)})
        if repaired_decision is not None:
            payload.update(
                {
                    "decision_type": repaired_decision.decision_type,
                    "tool_name": repaired_decision.tool_name,
                    "confidence": round(float(repaired_decision.confidence), 3),
                    "reasoning_summary": repaired_decision.reasoning_summary,
                }
            )
        log_event(event_name, **payload)
    except Exception:
        pass


def _build_primary_messages(
    *,
    system_prompt_text: str,
    tools: List[AgentToolSpec],
    question: str,
    conversation_state_card: str,
    recent_chat: List[BaseMessage],
) -> List[BaseMessage]:
    system_message = SystemMessage(content=f"{system_prompt_text}\n\n[Available Tools]\n{_tool_payload(tools)}")
    human_content = (
        f"<conversation_state_card>\n{conversation_state_card}\n</conversation_state_card>\n\n"
        f"<recent_chat_history>\n{_recent_chat_payload(recent_chat)}\n</recent_chat_history>\n\n"
        f"<user_query>{question}</user_query>"
    )
    return [system_message, HumanMessage(content=human_content)]


async def _build_repair_messages(
    *,
    tools: List[AgentToolSpec],
    raw_output: Any,
    exc: Exception,
) -> List[BaseMessage]:
    error_type, validation_errors = _validation_error_info(exc)
    repair_prompt = await _load_repair_prompt()
    human_content = "\n\n".join(
        [
            "[Invalid AgentDecision Output]",
            _truncate_raw_output(raw_output),
            "[Validation Error Type]",
            error_type,
            "[Validation Errors]",
            json.dumps(validation_errors, ensure_ascii=False, sort_keys=True, default=str),
            "[Available Tools]",
            _tool_payload(tools),
            "[Output]",
            "Return one repaired AgentDecision JSON object.",
        ]
    )
    return [SystemMessage(content=repair_prompt), HumanMessage(content=human_content)]


async def _repair_agent_decision(
    *,
    invoke_model: AgentModelInvoker,
    raw_output: Any,
    exc: Exception,
    tools: List[AgentToolSpec],
    request_id: str,
    conversation_id: str,
    turn_id: str,
) -> AgentDecision | None:
    max_attempts = agentic_decision_repair_max_attempts()
    for attempt in range(1, max_attempts + 1):
        _log_self_repair(
            "AGENT.SELF_REPAIR.START",
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            attempt=attempt,
            exc=exc,
            raw_output=raw_output,
        )
        try:
            repair_messages = await _build_repair_messages(tools=tools, raw_output=raw_output, exc=exc)
            repair_result = invoke_model(repair_messages)
            raw_repair = await repair_result if inspect.isawaitable(repair_result) else repair_result
            decision = validate_agent_decision(_decision_from_payload(raw_repair), available_tools=tools)
        except Exception as repair_exc:
            _log_self_repair(
                "AGENT.SELF_REPAIR.FAILED",
                request_id=request_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                attempt=attempt,
                exc=repair_exc,
                raw_output=locals().get("raw_repair"),
            )
            return None

        _log_self_repair(
            "AGENT.SELF_REPAIR.SUCCESS",
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            attempt=attempt,
            repaired_decision=decision,
        )
        return decision
    return None


async def run_dialogue_agent(
    *,
    question: str,
    conversation_state_card: str,
    recent_chat: List[BaseMessage],
    available_tools: Optional[List[AgentToolSpec]] = None,
    request_id: str,
    conversation_id: str,
    turn_id: str = "",
    invoke_model: Optional[AgentModelInvoker] = None,
) -> AgentDecision:
    """Run the Dialogue Agent as the request front-controller."""

    tools = list(available_tools or default_agent_tool_specs())

    if invoke_model is None:

        async def _default_invoker(messages: List[BaseMessage]) -> Any:
            from apps.chat.llm_runtime import build_llm

            llm = build_llm(model_name="solar_vllm_0")
            return await llm.ainvoke_non_stream(messages)

        invoke_model = _default_invoker

    system_prompt_text = await _load_agent_prompt()
    messages = _build_primary_messages(
        system_prompt_text=system_prompt_text,
        tools=tools,
        question=question,
        conversation_state_card=conversation_state_card,
        recent_chat=recent_chat,
    )

    try:
        raw_result = invoke_model(messages)
        raw_decision = await raw_result if inspect.isawaitable(raw_result) else raw_result
    except Exception as exc:
        _log_agent_invoke_error(
            exc=exc,
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        decision = _agent_internal_error_decision(reason=f"dialogue_agent_invoke_error:{type(exc).__name__}")
        _log_agent_decision(
            decision,
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            conversation_state_card=conversation_state_card,
        )
        return decision

    try:
        decision = validate_agent_decision(_decision_from_payload(raw_decision), available_tools=tools)
    except Exception as exc:
        _log_agent_parse_error(
            exc=exc,
            raw_output=raw_decision,
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        repaired_decision = await _repair_agent_decision(
            invoke_model=invoke_model,
            raw_output=raw_decision,
            exc=exc,
            tools=tools,
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        decision = repaired_decision or _agent_internal_error_decision(
            reason=f"dialogue_agent_parse_or_validation_error:{type(exc).__name__}"
        )

    _log_agent_decision(
        decision,
        request_id=request_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        conversation_state_card=conversation_state_card,
    )
    return decision
