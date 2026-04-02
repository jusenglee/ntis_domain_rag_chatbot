from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowNodes:
    """요청 workflow를 조립할 때 필요한 노드 함수 집합이다."""
    load_memory: Any
    rule_precheck: Any
    analyze_question: Any
    judge_knowledge_sufficiency: Any
    rag_search: Any
    generate_answer_gemma: Any
    generate_answer_solar: Any
    join_answers: Any
    direct_answer: Any
    merge_answers: Any
    save_history: Any


def route_after_rule(state: Any) -> str:
    """rule precheck 결과가 direct answer면 그 분기로, 아니면 질문 분석으로 보낸다."""
    rule_decision = getattr(state, "rule_decision", None)
    if rule_decision and getattr(rule_decision, "action", None) == "direct_answer":
        return "direct_answer"
    return "analyze_question"


def route_after_knowledge_sufficiency(state: Any) -> str | list[str]:
    """기존 문맥만으로 답할 수 있으면 두 답변 모델을 병렬로 실행하고, 아니면 RAG 검색으로 보낸다."""
    knowledge_sufficiency = getattr(state, "knowledge_sufficiency", None)
    prev_context = getattr(state, "prev_context", None)
    if (
        knowledge_sufficiency is not None
        and getattr(knowledge_sufficiency, "requires_new_knowledge", None) == "low"
        and prev_context
    ):
        return ["generate_answer_solar", "generate_answer_gemma"]
    return "rag_search"


def build_request_workflow(
    *,
    agent_state_type: Any,
    state_graph_cls: Any,
    end: Any,
    nodes: WorkflowNodes,
) -> Any:
    """요청 처리용 state graph를 구성한다.

    메모리 적재, 규칙 기반 우회, 질문 분석, 지식 충족도 판단, RAG 검색, 듀얼 모델 답변, 저장까지의 고정 흐름을 한곳에서 연결한다.
    """
    workflow = state_graph_cls(agent_state_type)

    workflow.add_node("load_memory", nodes.load_memory)
    workflow.add_node("rule_precheck", nodes.rule_precheck)
    workflow.add_node("analyze_question", nodes.analyze_question)
    workflow.add_node("judge_knowledge_sufficiency", nodes.judge_knowledge_sufficiency)
    workflow.add_node("rag_search", nodes.rag_search)
    workflow.add_node("generate_answer_gemma", nodes.generate_answer_gemma)
    workflow.add_node("generate_answer_solar", nodes.generate_answer_solar)
    workflow.add_node("join_answers", nodes.join_answers)
    workflow.add_node("direct_answer", nodes.direct_answer)
    workflow.add_node("merge_answers", nodes.merge_answers)
    workflow.add_node("save_history", nodes.save_history)

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
    workflow.add_edge("save_history", end)

    return workflow
