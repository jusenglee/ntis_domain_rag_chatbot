"""NTIS RAG 요청 처리 워크플로우 빌더 모듈.

LangGraph를 사용하여 질문 분석, 도구 실행, RAG 검색, 답변 생성 및 병합의 전체 과정을
상태 머신(State Machine) 형태로 정의하고 조립합니다.
"""

from __future__ import annotations

from typing import Any

from apps.api.contracts.workflow_models import AgentState
from apps.api.workflow_nodes import (
    node_agent_clarification,
    node_agent_direct_answer,
    node_agent_internal_error,
    node_retry_dialogue_agent_after_tool_error,
    node_build_conversation_state_card,
    node_direct_answer,
    node_execute_agent_tool,
    node_join_answers,
    node_load_memory,
    node_render_anchor_answer,
    node_render_participant_answer,
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
    """
    사전 규칙 체크(rule_precheck) 이후의 경로를 결정합니다.
    규칙에 의해 즉시 답변이 결정된 경우 'direct_answer'로, 그렇지 않으면 일반 에이전트 흐름으로 이동합니다.
    """
    rule_decision = getattr(state, "rule_decision", None)
    if rule_decision and getattr(rule_decision, "action", None) == "direct_answer":
        return "direct_answer"
    return "build_conversation_state_card"


def route_after_agent_decision(state: Any) -> str:
    """
    대화 에이전트(run_dialogue_agent)의 의사결정에 따라 경로를 결정합니다.
    - direct_answer: 에이전트가 즉시 답변 가능
    - call_tool: 검색 도구 실행 필요
    - ask_clarification: 사용자에게 추가 질문 필요
    """
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
    """
    에이전트 도구 실행(execute_agent_tool) 이후의 경로를 결정합니다.
    도구가 정상적으로 검색 의도를 생성한 경우 지식 충분성 판단 단계로 가고,
    결정적 앵커/참여인력 결과는 템플릿 렌더링 노드로,
    실패한 경우 1회 재시도하거나 에러 노드로 이동합니다.
    """
    observation = getattr(state, "agent_observation", None)
    observation_type = str(getattr(observation, "observation_type", "") or "")

    if (
        observation_type == "planned_intent"
        and getattr(state, "agent_tool_intent_payload", None) is not None
        and getattr(state, "agent_tool_question_analysis", None) is not None
    ):
        return "judge_knowledge_sufficiency"

    if observation_type == "resolved_anchor":
        return "render_anchor_answer"

    if observation_type == "participant_extraction":
        return "render_participant_answer"

    retry_count = int(getattr(state, "agent_tool_retry_count", 0) or 0)
    if retry_count < 1:
        return "retry_agent_after_tool_error"
    return "agent_internal_error"


def route_after_knowledge_sufficiency(state: Any) -> str | list[str]:
    """
    지식 충분성 판단(judge_knowledge_sufficiency) 이후의 경로를 결정합니다.
    이미 충분한 지식이 대화 맥락에 있다면 검색을 건너뛰고 답변 생성 단계(병렬)로 바로 진입합니다.
    그렇지 않으면 실제 RAG 검색(rag_search)을 수행합니다.
    """
    knowledge_sufficiency = getattr(state, "knowledge_sufficiency", None)
    prev_context = getattr(state, "prev_context", None)
    if (
        knowledge_sufficiency is not None
        and getattr(knowledge_sufficiency, "requires_new_knowledge", None) == "low"
        and prev_context
    ):
        # 지식이 충분하면 검색 생략 후 두 모델 병렬 실행
        return ["generate_answer_solar", "generate_answer_gemma"]
    return "rag_search"


def route_after_rag_search(state: Any) -> str | list[str]:
    """
    RAG 검색(rag_search) 결과에 따라 경로를 결정합니다.
    검색 결과가 없거나 부족한 경우 검색 조건을 완화하여 재시도(relax_and_retry)하거나,
    결과가 확보된 경우 답변 생성 단계로 이동합니다.
    """
    qa = getattr(state, "question_analysis", None)
    return decide_post_retrieval_route(
        context=getattr(state, "context", None) or [],
        retry_count=getattr(state, "search_retry_count", 0) or 0,
        qa_mode=getattr(qa, "mode", ""),
        join_key_mode=getattr(qa, "join_key_mode", None),
        retrieval_runtime_meta=getattr(state, "retrieval_runtime_meta", None) or {},
    )


def build_request_workflow() -> Any:
    """
    전체 워크플로우 그래프를 생성하고 노드 및 에지를 연결합니다.
    이 함수는 서버 시작 시 1회 실행되어 컴파일된 그래프를 생성합니다.
    """
    from langgraph.graph import END, StateGraph

    workflow = StateGraph(AgentState)
    
    # 1. 노드 추가
    workflow.add_node("load_memory", node_load_memory)
    workflow.add_node("rule_precheck", node_rule_precheck)
    workflow.add_node("build_conversation_state_card", node_build_conversation_state_card)
    workflow.add_node("run_dialogue_agent", node_run_dialogue_agent)
    workflow.add_node("execute_agent_tool", node_execute_agent_tool)
    workflow.add_node("retry_agent_after_tool_error", node_retry_dialogue_agent_after_tool_error)
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
    workflow.add_node("render_anchor_answer", node_render_anchor_answer)
    workflow.add_node("render_participant_answer", node_render_participant_answer)
    workflow.add_node("save_history", node_save_history)

    # 2. 에지(흐름) 연결
    workflow.set_entry_point("load_memory")
    workflow.add_edge("load_memory", "rule_precheck")
    
    # 사전 규칙 기반 분기
    workflow.add_conditional_edges(
        "rule_precheck",
        route_after_rule,
        {
            "direct_answer": "direct_answer",
            "build_conversation_state_card": "build_conversation_state_card",
        },
    )

    # 에이전트 의사결정 흐름
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
    
    # 도구 실행 결과에 따른 분기
    workflow.add_conditional_edges(
        "execute_agent_tool",
        route_after_agent_tool,
        {
            "judge_knowledge_sufficiency": "judge_knowledge_sufficiency",
            "render_anchor_answer": "render_anchor_answer",
            "render_participant_answer": "render_participant_answer",
            "retry_agent_after_tool_error": "retry_agent_after_tool_error",
            "agent_internal_error": "agent_internal_error",
        },
    )
    
    # 도구 에러 후 재시도 흐름
    workflow.add_conditional_edges(
        "retry_agent_after_tool_error",
        route_after_agent_decision,
        {
            "agent_direct_answer": "agent_direct_answer",
            "execute_agent_tool": "execute_agent_tool",
            "agent_clarification": "agent_clarification",
            "agent_internal_error": "agent_internal_error",
        },
    )

    # 검색 전 지식 판단 및 검색/답변 분기
    workflow.add_conditional_edges(
        "judge_knowledge_sufficiency",
        route_after_knowledge_sufficiency,
        {
            "generate_answer_solar": "generate_answer_solar",
            "generate_answer_gemma": "generate_answer_gemma",
            "rag_search": "rag_search",
        },
    )

    # 검색 결과에 따른 완화(Retry) 또는 생성 분기
    workflow.add_conditional_edges(
        "rag_search",
        route_after_rag_search,
        {
            "relax_and_retry": "relax_and_retry",
            "generate_answer_gemma": "generate_answer_gemma",
            "generate_answer_solar": "generate_answer_solar",
        },
    )

    # 후속 흐름 연결 (답변 생성 -> 병합 -> 저장 -> 종료)
    workflow.add_edge("relax_and_retry", "rag_search")
    workflow.add_edge("generate_answer_gemma", "join_answers")
    workflow.add_edge("generate_answer_solar", "join_answers")
    workflow.add_edge("join_answers", "merge_answers")
    workflow.add_edge("direct_answer", "save_history")
    # agent direct_answer 결정 시 본문은 Triton(gemma)/vLLM(solar) 두 LLM에 위임.
    workflow.add_edge("agent_direct_answer", "generate_answer_gemma")
    workflow.add_edge("agent_direct_answer", "generate_answer_solar")
    workflow.add_edge("agent_clarification", "save_history")
    workflow.add_edge("agent_internal_error", "save_history")
    workflow.add_edge("merge_answers", "save_history")
    workflow.add_edge("render_anchor_answer", "save_history")
    workflow.add_edge("render_participant_answer", "save_history")
    workflow.add_edge("save_history", END)
    
    return workflow
