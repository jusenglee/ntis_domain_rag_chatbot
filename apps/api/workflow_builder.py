from __future__ import annotations

from typing import Any

from apps.api.contracts.workflow_models import AgentState
from apps.api.workflow_nodes import (
    node_agent_clarification,
    node_agent_direct_answer,
    node_agent_internal_error,
    node_build_conversation_state_card,
    node_direct_answer,
    node_execute_agent_tool,
    node_join_answers,
    node_load_memory,
    node_rule_precheck,
    node_run_dialogue_agent,
    node_save_history,
)
from apps.chat.answer_generation import (
    node_generate_answer_gemma,
    node_generate_answer_solar,
    node_merge_answers,
)
from apps.retrieval.runtime_routing import decide_post_retrieval_route
from apps.retrieval.retrieval_workflow import node_knowledge_sufficiency, node_rag_search, node_relax_and_retry


def route_after_rule(state: Any) -> str:
    """Route direct-answer prechecks or the normal agent front-controller path."""

    rule_decision = getattr(state, "rule_decision", None)
    if rule_decision and getattr(rule_decision, "action", None) == "direct_answer":
        return "direct_answer"
    return "build_conversation_state_card"


def route_after_agent_decision(state: Any) -> str:
    """Route the dialogue-agent decision as the front-controller decision."""

    decision = getattr(state, "agent_decision", None)
    decision_type = str(getattr(decision, "decision_type", "") or "").strip()
    if decision_type == "direct_answer":
        return "agent_direct_answer"
    if decision_type == "call_tool":
        return "execute_agent_tool"
    if decision_type == "ask_clarification":
        return "agent_clarification"
    return "agent_internal_error"


def route_after_agent_tool(state: Any) -> str:
    """Continue to retrieval only when the tool produced a guarded intent."""

    observation = getattr(state, "agent_observation", None)
    if (
        str(getattr(observation, "observation_type", "") or "") == "planned_intent"
        and getattr(state, "agent_tool_intent_payload", None) is not None
        and getattr(state, "agent_tool_question_analysis", None) is not None
    ):
        return "judge_knowledge_sufficiency"
    return "agent_clarification"


def route_after_knowledge_sufficiency(state: Any) -> str | list[str]:
    """Run answer generation in parallel when previous context is already sufficient."""

    knowledge_sufficiency = getattr(state, "knowledge_sufficiency", None)
    prev_context = getattr(state, "prev_context", None)
    if (
        knowledge_sufficiency is not None
        and getattr(knowledge_sufficiency, "requires_new_knowledge", None) == "low"
        and prev_context
    ):
        return ["generate_answer_solar", "generate_answer_gemma"]
    return "rag_search"


def route_after_rag_search(state: Any) -> str | list[str]:
    """Route empty retrieval results to bounded answer generation or configured retry."""

    qa = getattr(state, "question_analysis", None)
    return decide_post_retrieval_route(
        context=getattr(state, "context", None) or [],
        retry_count=getattr(state, "search_retry_count", 0) or 0,
        qa_mode=getattr(qa, "mode", ""),
        join_key_mode=getattr(qa, "join_key_mode", None),
        retrieval_runtime_meta=getattr(state, "retrieval_runtime_meta", None) or {},
    )


def build_request_workflow() -> Any:
    """Assemble the fixed request workflow using module-owned nodes."""

    from langgraph.graph import END, StateGraph

    workflow = StateGraph(AgentState)
    workflow.add_node("load_memory", node_load_memory)
    workflow.add_node("rule_precheck", node_rule_precheck)
    workflow.add_node("build_conversation_state_card", node_build_conversation_state_card)
    workflow.add_node("run_dialogue_agent", node_run_dialogue_agent)
    workflow.add_node("execute_agent_tool", node_execute_agent_tool)
    workflow.add_node("agent_direct_answer", node_agent_direct_answer)
    workflow.add_node("agent_clarification", node_agent_clarification)
    workflow.add_node("agent_internal_error", node_agent_internal_error)
    workflow.add_node("judge_knowledge_sufficiency", node_knowledge_sufficiency)
    workflow.add_node("rag_search", node_rag_search)
    workflow.add_node("relax_and_retry", node_relax_and_retry)
    workflow.add_node("generate_answer_gemma", node_generate_answer_gemma)
    workflow.add_node("generate_answer_solar", node_generate_answer_solar)
    workflow.add_node("join_answers", node_join_answers)
    workflow.add_node("direct_answer", node_direct_answer)
    workflow.add_node("merge_answers", node_merge_answers)
    workflow.add_node("save_history", node_save_history)

    workflow.set_entry_point("load_memory")
    workflow.add_edge("load_memory", "rule_precheck")
    workflow.add_conditional_edges(
        "rule_precheck",
        route_after_rule,
        {
            "direct_answer": "direct_answer",
            "build_conversation_state_card": "build_conversation_state_card",
        },
    )

    workflow.add_edge("build_conversation_state_card", "run_dialogue_agent")
    workflow.add_conditional_edges(
        "run_dialogue_agent",
        route_after_agent_decision,
        {
            "agent_direct_answer": "agent_direct_answer",
            "execute_agent_tool": "execute_agent_tool",
            "agent_clarification": "agent_clarification",
            "agent_internal_error": "agent_internal_error",
        },
    )
    workflow.add_conditional_edges(
        "execute_agent_tool",
        route_after_agent_tool,
        {
            "judge_knowledge_sufficiency": "judge_knowledge_sufficiency",
            "agent_clarification": "agent_clarification",
        },
    )

    workflow.add_conditional_edges(
        "judge_knowledge_sufficiency",
        route_after_knowledge_sufficiency,
        {
            "generate_answer_solar": "generate_answer_solar",
            "generate_answer_gemma": "generate_answer_gemma",
            "rag_search": "rag_search",
        },
    )

    workflow.add_conditional_edges(
        "rag_search",
        route_after_rag_search,
        {
            "relax_and_retry": "relax_and_retry",
            "generate_answer_gemma": "generate_answer_gemma",
            "generate_answer_solar": "generate_answer_solar",
        },
    )

    workflow.add_edge("relax_and_retry", "rag_search")
    workflow.add_edge("generate_answer_gemma", "join_answers")
    workflow.add_edge("generate_answer_solar", "join_answers")
    workflow.add_edge("join_answers", "merge_answers")
    workflow.add_edge("direct_answer", "save_history")
    workflow.add_edge("agent_direct_answer", "save_history")
    workflow.add_edge("agent_clarification", "save_history")
    workflow.add_edge("agent_internal_error", "save_history")
    workflow.add_edge("merge_answers", "save_history")
    workflow.add_edge("save_history", END)
    return workflow
