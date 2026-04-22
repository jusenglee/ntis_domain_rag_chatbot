from __future__ import annotations

from typing import Any, Dict, Optional
from langgraph.graph import StateGraph, END

from apps.platform.schemas import AgentState
from apps.api.workflow_nodes import (
    node_rule_precheck,
    node_analyze_question,
    node_judge_knowledge_sufficiency,
    node_rag_search,
    node_answer_generation,
    node_agent_entry,
    node_agent_router,
    node_agent_execute_tool,
    node_agent_direct_answer,
    node_agent_clarification,
)
from apps.conversation.agent_flags import agent_enabled


def build_request_workflow() -> StateGraph:
    """
    NTIS RAG 시스템의 전체 실행 워크플로우를 정의하고 빌드합니다.
    ADR-0001에 따라 '에이전틱 모드'와 '레거시 모드'를 지원합니다.
    """
    
    # 1. 시스템 상태(AgentState)를 기반으로 상태 그래프 초기화
    workflow = StateGraph(AgentState)

    # 2. 공통 노드 등록: 사전 규칙 체크
    workflow.add_node("rule_precheck", node_rule_precheck)

    # --- 에이전틱 경로 (Agentic Path) ---
    # 에이전트가 대화의 주도권을 가지고 도구를 호출하거나 직접 답하는 경로입니다.
    workflow.add_node("agent_entry", node_agent_entry)           # 상태 카드 생성 및 에이전트 준비
    workflow.add_node("agent_router", node_agent_router)         # 에이전트 의사결정 (LLM)
    workflow.add_node("agent_execute_tool", node_agent_execute_tool) # 도구 실행 및 플래너 연결
    workflow.add_node("agent_direct_answer", node_agent_direct_answer) # 즉시 답변 처리
    workflow.add_node("agent_clarification", node_agent_clarification) # 확인 요청 처리

    # --- 레거시 경로 (Legacy Path) ---
    # 코드 기반의 결정론적 파이프라인으로 질문을 분석합니다.
    workflow.add_node("analyze_question", node_analyze_question)

    # --- 공통 실행 경로 (Execution Path) ---
    # 에이전트가 도구를 호출했거나 레거시 분석이 끝난 후 실제로 데이터를 찾는 단계입니다.
    workflow.add_node("judge_sufficiency", node_judge_knowledge_sufficiency)
    workflow.add_node("rag_search", node_rag_search)
    workflow.add_node("answer_generation", node_answer_generation)

    # 3. 엣지(Edge) 및 분기 정의
    workflow.set_entry_point("rule_precheck")

    def route_after_precheck(state: AgentState) -> str:
        """피처 플래그에 따라 에이전틱 모드 진입 여부를 결정합니다."""
        if agent_enabled():
            return "agent_entry"
        return "analyze_question"

    workflow.add_conditional_edges(
        "rule_precheck",
        route_after_precheck,
        {
            "agent_entry": "agent_entry",
            "analyze_question": "analyze_question"
        }
    )

    # 에이전트 의사결정에 따른 분기
    def route_agent_decision(state: AgentState) -> str:
        decision = state.get("agent_decision")
        if not decision: return "agent_clarification"
        
        if decision.decision_type == "call_tool": return "agent_execute_tool"
        if decision.decision_type == "direct_answer": return "agent_direct_answer"
        return "agent_clarification"

    workflow.add_edge("agent_entry", "agent_router")
    workflow.add_conditional_edges("agent_router", route_agent_decision)

    # 도구 실행 후 검색 파이프라인 합류 또는 확인 요청
    def route_after_tool(state: AgentState) -> str:
        obs = state.get("agent_observation")
        if obs and obs.observation_type == "planned_intent":
            return "judge_sufficiency"
        return "agent_clarification"

    workflow.add_conditional_edges("agent_execute_tool", route_after_tool)

    # 레거시 분석 후 검색 파이프라인 진행
    workflow.add_edge("analyze_question", "judge_sufficiency")

    # 검색 및 답변 생성 흐름
    workflow.add_edge("judge_sufficiency", "rag_search")
    workflow.add_edge("rag_search", "answer_generation")
    
    # 최종 종료 엣지들
    workflow.add_edge("answer_generation", END)
    workflow.add_edge("agent_direct_answer", END)
    workflow.add_edge("agent_clarification", END)

    return workflow
