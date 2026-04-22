from __future__ import annotations

from typing import Any, Dict, Optional

from apps.conversation.agent_observation import AgentObservation, AgentToolExecutionResult
from apps.conversation.agent_tools import tool_spec_by_name
from apps.conversation.request_facade import build_agent_intent_payload
from apps.conversation.session_memory import SessionMemory, SubjectQueryContext, view_state_from_current_context
from apps.conversation.view_state import ConversationViewState


def _get_state_attr(state: Any, key: str, default: Any = None) -> Any:
    return getattr(state, key, default)


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _model_dump(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            return dict(value.model_dump())
        except Exception:
            return {}
    return dict(getattr(value, "__dict__", {}) or {})


def _append_filter_parts(parts: list[str], tool_args: Dict[str, Any]) -> None:
    people_name = _first_text(tool_args.get("people_name"))
    org_name = _first_text(tool_args.get("org_name"))
    role = _first_text(tool_args.get("role"))
    perf_type = _first_text(tool_args.get("perf_type"))
    target = _first_text(tool_args.get("target"))
    year_from = tool_args.get("year_from")
    year_to = tool_args.get("year_to")
    limit = tool_args.get("limit")

    for value in (people_name, org_name, target, role, perf_type):
        if value and value not in parts:
            parts.append(value)
    if year_from or year_to:
        parts.append(f"{year_from or ''}~{year_to or ''}")
    if limit:
        parts.append(f"{limit} items")


def _question_from_search_args(tool_args: Dict[str, Any]) -> Optional[str]:
    query = _first_text(tool_args.get("query"))
    if not query:
        return None

    parts = [query]
    domain_head = _first_text(tool_args.get("domain_head"))
    if domain_head and domain_head != "auto":
        parts.append(domain_head)
    _append_filter_parts(parts, tool_args)
    return " ".join(parts)


def _question_from_refine_args(tool_args: Dict[str, Any], state: Any) -> tuple[Optional[str], Optional[str]]:
    session_memory = _get_state_attr(state, "session_memory")
    if not isinstance(session_memory, SessionMemory):
        return None, "missing_session_memory"

    context = session_memory.current_context
    if not isinstance(context, SubjectQueryContext):
        return None, "missing_current_subject"

    if not bool(context.followup_rights.refinement_allowed):
        return None, "subject_refinement_not_allowed"

    parts = [context.subject_name]
    subject_kind = _first_text(context.subject_kind)
    if subject_kind:
        parts.append(subject_kind)
    _append_filter_parts(parts, tool_args)
    return " ".join(part for part in parts if part), None


async def execute_agent_tool(
    *,
    tool_name: str,
    tool_args: Dict[str, Any],
    state: Any,
) -> AgentToolExecutionResult:
    """Execute the Agent-selected tool through guarded planner/contract code."""

    name = str(tool_name or "").strip()
    args = dict(tool_args or {})
    spec = tool_spec_by_name().get(name)

    if spec is None or not spec.implemented:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="contract_violation",
                summary=f"Unsupported or unimplemented agent tool: {name}",
                warnings=["unknown_or_unimplemented_tool"],
                structured_refs={"tool_name": name},
            )
        )

    generated_question: Optional[str] = None
    if name == "search_ntis_domain":
        generated_question = _question_from_search_args(args)
        if not generated_question:
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="clarification_required",
                    summary="검색어가 없어 NTIS 검색을 안전하게 실행할 수 없습니다.",
                    warnings=["missing_query"],
                    structured_refs={"tool_name": name},
                )
            )
    elif name == "refine_current_subject":
        generated_question, error = _question_from_refine_args(args, state)
        if error:
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="clarification_required",
                    summary="현재 대화 주제를 특정할 수 없어 조건을 추가할 수 없습니다.",
                    warnings=[error],
                    structured_refs={"tool_name": name},
                )
            )
    elif name == "ask_user_for_clarification":
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="clarification_required",
                summary=str(args.get("question_to_user") or "").strip(),
                warnings=[],
                structured_refs={"tool_name": name, "reason": args.get("reason")},
            )
        )
    else:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="contract_violation",
                summary=f"No executor is registered for agent tool: {name}",
                warnings=["unregistered_tool_executor"],
                structured_refs={"tool_name": name},
            )
        )

    try:
        session_memory = _get_state_attr(state, "session_memory")
        view_state = _get_state_attr(state, "view_state")
        active_view_state = (
            view_state_from_current_context(session_memory)
            if session_memory
            else (view_state or ConversationViewState())
        )
        intent_payload, question_analysis = await build_agent_intent_payload(
            question=str(generated_question or ""),
            conversation_id=str(_get_state_attr(state, "conversation_id", "")),
            chat_history=list(_get_state_attr(state, "chat_history", [])),
            prev_context=list(_get_state_attr(state, "prev_context", [])),
            canonical_evidence=list(_get_state_attr(state, "canonical_evidence", [])),
            view_state=active_view_state,
            session_memory=session_memory,
            request_id=_first_text(_get_state_attr(state, "request_id")),
            turn_id=_first_text(_get_state_attr(state, "turn_id")),
        )
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="planned_intent",
                summary=f"Agent tool '{name}' produced a guarded planner intent.",
                structured_refs={
                    "tool_name": name,
                    "generated_question": generated_question,
                    "question_analysis": _model_dump(question_analysis),
                },
            ),
            intent_payload=intent_payload,
            question_analysis=question_analysis,
        )
    except Exception as exc:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="error",
                summary=f"Agent tool planning failed: {type(exc).__name__}: {exc}",
                warnings=["planner_error"],
                structured_refs={"tool_name": name, "generated_question": generated_question},
            )
        )
