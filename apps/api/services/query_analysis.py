from __future__ import annotations

from typing import Any, Optional

from apps.core.planner_stage15_types import PlannerEntityRolePlan


async def run_question_analysis(
    *,
    question: str,
    conversation_id: str,
    chat_history: list[Any],
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]] | None = None,
    view_state: Any = None,
    request_id: Optional[str] = None,
    normalized_intent_base: Any = None,
    classify_query_intent: Any,
    normalize_intent: Any,
    run_stagewise_question_analysis: Any,
    build_llm: Any,
    planner_stage1_decision_cls: Any,
    planner_stage15_plan_cls: Any = None,
    planner_stage2_slots_cls: Any,
    question_analysis_cls: Any,
    load_prompt_file: Any,
    sanitize_llm_json: Any,
    sanitize_ids_map_semantics: Any,
    log_event: Any,
    planner_stage1_prompt_version: str,
    planner_stage15_prompt_version: str = "v1",
    planner_stage2_prompt_version: str,
    planner_disable_thinking: bool,
    planner_temperature: float,
    planner_schema_version: str,
    planner_stage2_regate_seed_allowed_keys: set[str],
    max_top_k_size: int,
) -> Any:
    """질문 분석 플래너를 호출하기 전에 기본 normalized intent를 준비하고 stagewise planner에 위임한다.

    기본 intent가 이미 있으면 그것을 재사용하고, 없으면 cheap 분류 결과로 최소 intent를 만들어 planner 입력 계약을 맞춘다.
    """
    normalized_intent = normalized_intent_base or normalize_intent(
        classify_query_intent(question, [], hint={}),
        query=question,
        keywords=[],
    )
    return await run_stagewise_question_analysis(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        chat_history=chat_history,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence or [],
        view_state=view_state,
        normalized_intent=normalized_intent,
        build_llm=build_llm,
        planner_stage1_decision_cls=planner_stage1_decision_cls,
        planner_stage15_plan_cls=planner_stage15_plan_cls or PlannerEntityRolePlan,
        planner_stage2_slots_cls=planner_stage2_slots_cls,
        question_analysis_cls=question_analysis_cls,
        load_prompt_file=load_prompt_file,
        sanitize_llm_json=sanitize_llm_json,
        sanitize_ids_map_semantics=sanitize_ids_map_semantics,
        log_event=log_event,
        planner_stage1_prompt_version=planner_stage1_prompt_version,
        planner_stage15_prompt_version=planner_stage15_prompt_version,
        planner_stage2_prompt_version=planner_stage2_prompt_version,
        planner_disable_thinking=planner_disable_thinking,
        planner_temperature=planner_temperature,
        planner_schema_version=planner_schema_version,
        planner_stagewise_enabled=True,
        planner_stage2_regate_seed_allowed_keys=planner_stage2_regate_seed_allowed_keys,
        max_top_k_size=max_top_k_size,
    )
