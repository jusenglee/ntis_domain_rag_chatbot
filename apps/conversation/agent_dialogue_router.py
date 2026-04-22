from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from apps.conversation.agent_contracts import AgentDecision
from apps.conversation.agent_tools import AgentToolSpec, default_agent_tool_specs
from apps.platform.langchain_compat import BaseMessage, HumanMessage, SystemMessage
from apps.planner.prompt_asset_paths import planner_prompt_path


AgentModelInvoker = Callable[[List[BaseMessage]], Awaitable[Any] | Any]


async def _load_agent_prompt() -> str:
    path = planner_prompt_path("dialogue_agent_v1.md")
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


def _tool_payload(tools: Iterable[AgentToolSpec]) -> str:
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
            "implemented": tool.implemented,
            "feature_flag": tool.feature_flag,
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


def _decision_from_payload(value: Any) -> AgentDecision:
    if isinstance(value, AgentDecision):
        return value
    if isinstance(value, dict):
        return AgentDecision.model_validate(value)
    from apps.chat.llm_json import sanitize_llm_json

    text = _message_content(value)
    json_text = sanitize_llm_json(text)
    return AgentDecision.model_validate_json(json_text)


def _fail_closed_decision(*, reason: str, confidence: float = 0.0) -> AgentDecision:
    return AgentDecision(
        decision_type="ask_clarification",
        clarification_question="NTIS에서 조회할 대상이나 조건을 안전하게 특정할 수 없습니다. 대상 이름이나 ID를 더 구체적으로 알려 주세요.",
        confidence=max(0.0, min(1.0, confidence)),
        reasoning_summary=reason,
    )


def _safe_tool_arg_fields(tool_args: Dict[str, Any]) -> Dict[str, Any]:
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
        fields["query_preview"] = query[:80] + ("...(truncated)" if len(query) > 80 else "")
    return fields


def _validate_tool_decision(
    decision: AgentDecision,
    *,
    available_tools: List[AgentToolSpec],
) -> AgentDecision:
    if decision.decision_type != "call_tool":
        return decision
    tools = {tool.name: tool for tool in available_tools}
    spec = tools.get(str(decision.tool_name or ""))
    if spec is None:
        return _fail_closed_decision(reason=f"agent_selected_unknown_tool:{decision.tool_name}")
    if not spec.implemented:
        return _fail_closed_decision(reason=f"agent_selected_unimplemented_tool:{decision.tool_name}")
    return decision


def _card_meta_from_card(conversation_state_card: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    for raw_line in str(conversation_state_card or "").splitlines():
        line = raw_line.strip()
        if line.startswith("current_context_type:"):
            meta["current_context_type"] = line.split(":", 1)[1].strip() or None
        elif line.startswith("current subject:"):
            subject = line.split(":", 1)[1].strip()
            if not subject or subject == "none confirmed":
                continue
            if subject.endswith(")") and "(" in subject:
                name, kind = subject.rsplit("(", 1)
                meta["subject_name"] = name.strip()
                meta["subject_kind"] = kind[:-1].strip() or None
            else:
                meta["subject_name"] = subject
        elif line.startswith("refinement_allowed:"):
            meta["refinement_allowed"] = line.split(":", 1)[1].strip().lower() == "yes"
    return meta


def _log_agent_decision(
    *,
    decision: AgentDecision,
    request_id: str,
    conversation_id: str,
    turn_id: str,
    current_context_type: Optional[str],
    subject_kind: Optional[str],
    subject_name: Optional[str],
) -> None:
    try:
        from apps.api.runtime_helpers import log_event

        tool_arg_fields = _safe_tool_arg_fields(decision.tool_args)
        log_event(
            "AGENT.DECISION",
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            decision_type=decision.decision_type,
            tool_name=decision.tool_name,
            confidence=round(float(decision.confidence), 3),
            current_context_type=current_context_type,
            subject_kind=subject_kind,
            subject_name=subject_name,
            reasoning_summary=decision.reasoning_summary,
            **tool_arg_fields,
        )
    except Exception:
        pass


def _context_type_from_card(conversation_state_card: str) -> Optional[str]:
    for line in str(conversation_state_card or "").splitlines():
        if line.strip().startswith("current_context_type:"):
            return line.split(":", 1)[1].strip() or None
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
    """Run the LLM-first dialogue router.

    The agent contract intentionally has no legacy fallback decision. If the
    model is unavailable or returns an unsafe tool, this fails closed to a
    clarification decision.
    """

    tools = list(available_tools or default_agent_tool_specs(implemented_only=True))
    if invoke_model is None:
        async def _default_invoker(messages: List[BaseMessage]) -> Any:
            from apps.chat.llm_runtime import build_llm

            llm = build_llm(model_name="solar_vllm_0")
            return await llm.ainvoke_non_stream(messages)

        invoke_model = _default_invoker

    system_prompt = await _load_agent_prompt()
    user_payload = "\n\n".join(
        [
            "[User Question]",
            str(question or "").strip(),
            str(conversation_state_card or "").strip(),
            "[Recent Chat]",
            _recent_chat_payload(recent_chat),
            "[Available Tools]",
            _tool_payload(tools),
            "[Output]",
            "Return only a JSON object that matches AgentDecision.",
        ]
    )
    messages: List[BaseMessage] = [SystemMessage(content=system_prompt), HumanMessage(content=user_payload)]

    try:
        raw_result = invoke_model(messages)
        if inspect.isawaitable(raw_result):
            raw_result = await raw_result
        decision = _decision_from_payload(raw_result)
        decision = _validate_tool_decision(decision, available_tools=tools)
    except Exception as exc:
        decision = _fail_closed_decision(reason=f"dialogue_agent_parse_or_invoke_error:{type(exc).__name__}")

    card_meta = _card_meta_from_card(conversation_state_card)
    _log_agent_decision(
        decision=decision,
        request_id=request_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        current_context_type=card_meta.get("current_context_type") or _context_type_from_card(conversation_state_card),
        subject_kind=card_meta.get("subject_kind"),
        subject_name=card_meta.get("subject_name"),
    )
    return decision
