from __future__ import annotations

from typing import Any

from apps.api.contracts.workflow_models import AgentState
from apps.api.workflow_nodes import (
    node_analyze_question,
    node_direct_answer,
    node_join_answers,
    node_load_memory,
    node_rule_precheck,
    node_save_history,
)
from apps.chat.answer_generation import (
    node_generate_answer_gemma,
    node_generate_answer_solar,
    node_merge_answers,
)
from apps.retrieval.retrieval_workflow import node_knowledge_sufficiency, node_rag_search


def route_after_rule(state: Any) -> str:
    """Route direct-answer prechecks away from the planner pipeline."""

    rule_decision = getattr(state, "rule_decision", None)
    if rule_decision and getattr(rule_decision, "action", None) == "direct_answer":
        return "direct_answer"
    return "analyze_question"


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


def build_request_workflow() -> Any:
    """Assemble the fixed request workflow using module-owned nodes."""

    from langgraph.graph import END, StateGraph

    workflow = StateGraph(AgentState)
    workflow.add_node("load_memory", node_load_memory)
    workflow.add_node("rule_precheck", node_rule_precheck)
    workflow.add_node("analyze_question", node_analyze_question)
    workflow.add_node("judge_knowledge_sufficiency", node_knowledge_sufficiency)
    workflow.add_node("rag_search", node_rag_search)
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
            "analyze_question": "analyze_question",
        },
    )

    workflow.add_edge("analyze_question", "judge_knowledge_sufficiency")
    workflow.add_conditional_edges(
        "judge_knowledge_sufficiency",
        route_after_knowledge_sufficiency,
        {
            "generate_answer_solar": "generate_answer_solar",
            "generate_answer_gemma": "generate_answer_gemma",
            "rag_search": "rag_search",
        },
    )

    workflow.add_edge("rag_search", "generate_answer_gemma")
    workflow.add_edge("rag_search", "generate_answer_solar")
    workflow.add_edge("generate_answer_gemma", "join_answers")
    workflow.add_edge("generate_answer_solar", "join_answers")
    workflow.add_edge("join_answers", "merge_answers")
    workflow.add_edge("direct_answer", "save_history")
    workflow.add_edge("merge_answers", "save_history")
    workflow.add_edge("save_history", END)
    return workflow
