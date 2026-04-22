from __future__ import annotations

from typing import Any, Dict, Optional

from apps.conversation.agent_flags import agent_tool_enabled
from apps.conversation.agent_observation import AgentObservation, AgentToolExecutionResult
from apps.conversation.agent_tools import tool_spec_by_name
from apps.conversation.request_facade import build_intent_payload
from apps.conversation.session_memory import SessionMemory, SubjectQueryContext, view_state_from_current_context
from apps.conversation.view_state import ConversationViewState


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _get_state_attr(state: Any, name: str, default: Any = None) -> Any:
    if isinstance(state, dict):
        return state.get(name, default)
    return getattr(state, name, default)


def _latest_question(state: Any) -> str:
    question = _first_text(_get_state_attr(state, "question"))
    if question:
        return question
    messages = _get_state_attr(state, "messages", []) or []
    if messages:
        return _first_text(getattr(messages[-1], "content", None), messages[-1]) or ""
    return ""


def _question_from_search_args(tool_args: Dict[str, Any]) -> str:
    parts = [_first_text(tool_args.get("query"))]
    for key in ("people_name", "org_name", "perf_type"):
        value = _first_text(tool_args.get(key))
        if value and value not in parts:
            parts.append(value)
    year_from = tool_args.get("year_from")
    year_to = tool_args.get("year_to")
    if year_from is not None or year_to is not None:
        parts.append(f"{year_from or ''}~{year_to or ''}")
    return " ".join(part for part in parts if part)


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
    target = _first_text(tool_args.get("target"))
    if target and target != "auto":
        parts.append(target)
    role = _first_text(tool_args.get("role"))
    if role:
        parts.append(role)
    perf_type = _first_text(tool_args.get("perf_type"))
    if perf_type:
        parts.append(perf_type)
    year_from = tool_args.get("year_from")
    year_to = tool_args.get("year_to")
    if year_from is not None or year_to is not None:
        parts.append(f"{year_from or ''}~{year_to or ''}")
    limit = tool_args.get("limit")
    if limit is not None:
        parts.append(f"limit {limit}")
    generated = " ".join(part for part in parts if _first_text(part))
    return generated or _latest_question(state), None


def _question_analysis_summary(question_analysis: Any) -> Dict[str, Any]:
    return {
        "mode": getattr(question_analysis, "mode", None),
        "head": getattr(question_analysis, "head", None),
        "action": getattr(question_analysis, "action", None),
        "relation": getattr(question_analysis, "relation", None),
        "join_key_mode": getattr(question_analysis, "join_key_mode", None),
        "ids_map": dict(getattr(question_analysis, "ids_map", {}) or {}),
        "filters": dict(getattr(question_analysis, "filters", {}) or {}),
        "target_cols": list(getattr(question_analysis, "target_cols", []) or []),
        "retrieval_query": getattr(question_analysis, "retrieval_query", None),
        "confidence": getattr(question_analysis, "confidence", None),
    }


def _result(
    observation: AgentObservation,
    *,
    intent_payload: Any = None,
    question_analysis: Any = None,
) -> AgentToolExecutionResult:
    return AgentToolExecutionResult(
        observation=observation,
        intent_payload=intent_payload,
        question_analysis=question_analysis,
    )


async def execute_agent_tool(
    *,
    tool_name: str,
    tool_args: Dict[str, Any],
    state: Any,
    enforce_feature_flags: bool = True,
) -> AgentToolExecutionResult:
    """Execute an agent tool through the guarded planner adapter.

    The executor returns both a user-facing observation and guarded planner
    outputs so the workflow can join the existing retrieval pipeline without
    legacy fallback.
    """

    name = str(tool_name or "").strip()
    args = dict(tool_args or {})
    spec = tool_spec_by_name().get(name)
    if spec is None:
        return _result(
            AgentObservation(
                observation_type="contract_violation",
                summary=f"Unknown agent tool: {name or 'none'}",
                warnings=["unknown_tool"],
            )
        )
    if not spec.implemented:
        return _result(
            AgentObservation(
                observation_type="contract_violation",
                summary=f"Agent tool is declared but not implemented yet: {name}",
                warnings=["tool_not_implemented"],
                next_suggested_actions=["ask_user_for_clarification"],
            )
        )
    if enforce_feature_flags and not agent_tool_enabled(spec.feature_flag):
        return _result(
            AgentObservation(
                observation_type="contract_violation",
                summary=f"Agent tool is disabled by feature flag: {spec.feature_flag}",
                structured_refs={"feature_flag": spec.feature_flag},
                warnings=["feature_flag_disabled"],
            )
        )

    if name == "ask_user_for_clarification":
        question = _first_text(args.get("question_to_user"), args.get("reason"), "Please clarify the target.")
        return _result(
            AgentObservation(
                observation_type="clarification_required",
                summary=question,
                structured_refs={
                    "reason": _first_text(args.get("reason")),
                    "suggested_options": list(args.get("suggested_options") or []),
                },
            )
        )

    if name == "search_ntis_domain":
        generated_question = _question_from_search_args(args)
        if not generated_question:
            return _result(
                AgentObservation(
                    observation_type="contract_violation",
                    summary="search_ntis_domain requires a non-empty query.",
                    warnings=["missing_query"],
                )
            )
    elif name == "refine_current_subject":
        generated_question, blocked_reason = _question_from_refine_args(args, state)
        if blocked_reason:
            return _result(
                AgentObservation(
                    observation_type="clarification_required",
                    summary="Current subject cannot be refined safely.",
                    structured_refs={"blocked_reason": blocked_reason},
                    warnings=[blocked_reason],
                )
            )
    else:
        return _result(
            AgentObservation(
                observation_type="contract_violation",
                summary=f"Unsupported implemented tool: {name}",
                warnings=["unsupported_tool"],
            )
        )

    session_memory = _get_state_attr(state, "session_memory")
    view_state = _get_state_attr(state, "view_state")
    if isinstance(session_memory, SessionMemory):
        active_view_state = view_state_from_current_context(session_memory)
    else:
        active_view_state = view_state if isinstance(view_state, ConversationViewState) else ConversationViewState()

    try:
        intent_payload, question_analysis = await build_intent_payload(
            question=str(generated_question or ""),
            conversation_id=str(_get_state_attr(state, "conversation_id", "") or ""),
            chat_history=list(_get_state_attr(state, "chat_history", []) or []),
            prev_context=list(_get_state_attr(state, "prev_context", []) or []),
            canonical_evidence=list(_get_state_attr(state, "canonical_evidence", []) or []),
            view_state=active_view_state,
            session_memory=session_memory if isinstance(session_memory, SessionMemory) else None,
            request_id=_first_text(_get_state_attr(state, "request_id")),
            turn_id=_first_text(_get_state_attr(state, "turn_id")),
        )
    except Exception as exc:
        return _result(
            AgentObservation(
                observation_type="error",
                summary=f"Agent tool planner adapter failed: {type(exc).__name__}",
                structured_refs={"error": str(exc)},
                warnings=["planner_adapter_error"],
            )
        )

    return _result(
        AgentObservation(
            observation_type="planned_intent",
            summary=f"Built guarded intent payload for agent tool: {name}",
            structured_refs={
                "tool_name": name,
                "generated_question": generated_question,
                "question_analysis": _question_analysis_summary(question_analysis),
                "strategy_meta": dict(getattr(intent_payload, "strategy_meta", {}) or {}),
            },
            evidence_count=len(list(_get_state_attr(state, "canonical_evidence", []) or [])),
            next_suggested_actions=["execute_retrieval"],
        ),
        intent_payload=intent_payload,
        question_analysis=question_analysis,
    )
