"""Stagewise planner orchestration helpers.\n\nThis module owns the LLM-driven stage1/stage2 planner flow so the app entry module\ncan stay focused on composition-root concerns.\n"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from langchain_core.messages import BaseMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from apps.api.services.canonical_context import render_canonical_evidence_text
from apps.core.planner_staged import (
    LockedStrategy,
    collect_regate_seed_map,
    compose_locked_strategy,
    extract_single_project_seed,
    merge_locked_strategy_slots,
    regate_locked_strategy,
)
from apps.core.query_intent import (
    has_ambiguous_project_key_label,
    has_explicit_project_id_label,
    has_explicit_project_no_label,
)


def intent_snapshot(normalized_intent: Any) -> dict[str, Any]:
    """stage 1 planner에 넘길 정규화 intent 요약본을 만든다.
    LLM이 보아야 할 필드만 간추려 프롬프트 노이즈를 줄이고, stage 1이 슬롯 세부 사항까지 선행 결정하지 않게 한다.
    """
    return {
        "action": getattr(normalized_intent, "action", None),
        "base_route": getattr(normalized_intent, "base_route", None),
        "is_id_query": bool(getattr(normalized_intent, "is_id_query", False)),
        "people_terms": list(getattr(normalized_intent, "people_terms", []) or []),
        "org_terms": list(getattr(normalized_intent, "org_terms", []) or []),
        "perf_types": list(getattr(normalized_intent, "perf_types", []) or []),
        "years": list(getattr(normalized_intent, "years", []) or []),
    }


def _planner_prev_context_text(
    *,
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    normalized_intent: Any,
) -> str:
    """이전 대화 컨텍스트를 stage 1 planner prompt에 넣을 텍스트로 만든다.
    canonical evidence가 있으면 그것을 우선하고, 없으면 prev_context snapshot을 짤라 넣어 stage 1이 참조형 질의를 해석할 수 있게 한다.
    """
    if canonical_evidence:
        return render_canonical_evidence_text(
            canonical_evidence,
            {
                "name": str(getattr(normalized_intent, "output_type", None) or "summary").strip().lower() or "summary",
                "context_kind": str(getattr(normalized_intent, "base_route", None) or "project").strip().lower() or "project",
            },
            max_chars=1200,
        )
    prev_lines = [json.dumps(item, ensure_ascii=False)[:180] for item in (prev_context or [])[:6]]
    return "\n".join(prev_lines) or "NONE"


def _extract_prev_context_seed(
    *,
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """이전 context 또는 canonical evidence에서 유효한 project seed를 추출한다.
    referential follow-up에서 새 id가 없어도 JOIN/LOOKUP gate가 참조 seed를 쓸 수 있게 하는 입구다.
    """
    if canonical_evidence:
        pjt_ids, pjt_nos = set(), set()
        for item in canonical_evidence:
            if not isinstance(item, dict):
                continue
            ids = item.get("ids") or {}
            pjt_id = str(ids.get("pjt_id") or "").strip()
            pjt_no = str(ids.get("pjt_no") or "").strip()
            if pjt_id:
                pjt_ids.add(pjt_id)
            if pjt_no:
                pjt_nos.add(pjt_no)
        if len(pjt_ids) == 1:
            return {"pjt_id": [next(iter(pjt_ids))]}
        if len(pjt_nos) == 1:
            return {"pjt_no": [next(iter(pjt_nos))]}
    return extract_single_project_seed(prev_context)


async def run_planner_stage1(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    chat_history: list[BaseMessage],
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    normalized_intent: Any,
    build_llm: Any,
    planner_stage1_decision_cls: Any,
    load_prompt_file: Any,
    sanitize_llm_json: Any,
    log_event: Any,
    planner_stage1_prompt_version: str,
    planner_disable_thinking: bool,
    planner_temperature: float,
) -> Any:
    """stage 1 planner prompt를 구성하고 LLM을 호출해 action·head·relation_candidate를 얻는다.
    history, prev context, intent snapshot을 함께 넘기되 stage 1이 mode·ids·filters를 직접 확정하지 않는 계약 내에서만 출력하게 한다.
    """
    llm = build_llm("solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=planner_stage1_decision_cls)
    history_str = "\n".join([f"{type(m).__name__}: {m.content}" for m in chat_history[-4:]])
    prev_context_text = _planner_prev_context_text(
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        normalized_intent=normalized_intent,
    )
    system_prompt = await load_prompt_file(Path(f"prompts/planner_stage1_{planner_stage1_prompt_version}.md"))
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            (
                "human",
                "{format_instructions}\n<user_query>{question}</user_query>\n<history>{history}</history>\n<prev_context>{prev_context}</prev_context>\n<intent_snapshot>{intent_snapshot}</intent_snapshot>",
            ),
        ]
    )
    planner_llm = llm.bind(
        reasoning_effort="low",
        include_reasoning=False,
        disable_thinking=planner_disable_thinking,
        temperature=planner_temperature,
        top_p=1.0,
        max_tokens=250,
    )
    chain = prompt | planner_llm | sanitize_llm_json | parser
    stage1 = await chain.ainvoke(
        {
            "format_instructions": parser.get_format_instructions(),
            "question": question,
            "history": history_str or "NONE",
            "prev_context": prev_context_text or "NONE",
            "intent_snapshot": json.dumps(intent_snapshot(normalized_intent), ensure_ascii=False),
        }
    )
    log_event(
        "PLANNER.STAGE1",
        request_id=request_id,
        conversation_id=conversation_id,
        action=stage1.action,
        head=stage1.head,
        relation_candidate=stage1.relation_candidate,
        referential_followup=int(stage1.referential_followup),
        confidence=round(stage1.confidence, 3),
        planner_stage1_prompt_version=planner_stage1_prompt_version,
        prev_context_source="canonical_evidence" if canonical_evidence else "prev_context_snapshot",
    )
    return stage1


def determine_locked_strategy(
    *,
    stage1: Any,
    normalized_intent: Any,
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    planner_stage2_regate_seed_allowed_keys: set[str],
    log_event: Any,
) -> LockedStrategy:
    """stage 1 결과와 기존 id/context seed로 locked strategy를 고정한다.
    prev context seed가 JOIN으로 상향시키는지까지 포함해, stage 2가 손대면 안 되는 진입 전략을 먼저 닫는 단계다.
    """
    base_ids_map = dict(getattr(normalized_intent, "ids_map", {}) or {})
    prev_context_seed = _extract_prev_context_seed(
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
    )
    gate_seed_map = collect_regate_seed_map(
        {**base_ids_map, **prev_context_seed},
        allowed_keys=planner_stage2_regate_seed_allowed_keys,
    )
    stage1_payload = stage1.model_dump() if hasattr(stage1, "model_dump") else {
        "action": getattr(stage1, "action", None),
        "head": getattr(stage1, "head", None),
        "relation_candidate": getattr(stage1, "relation_candidate", None),
        "referential_followup": getattr(stage1, "referential_followup", None),
        "confidence": getattr(stage1, "confidence", None),
    }
    locked = compose_locked_strategy(
        stage1=stage1_payload,
        ids_map={**base_ids_map, **prev_context_seed},
        has_prev_anchor=bool(prev_context_seed),
        prev_context_seed=prev_context_seed,
        gate_seed_map=gate_seed_map,
    )
    log_event(
        "PLANNER.GATE",
        stage1_action=stage1.action,
        stage1_head=stage1.head,
        stage1_relation_candidate=stage1.relation_candidate,
        gate_mode=locked.mode,
        gate_relation=locked.relation,
        gate_join_key_mode=locked.join_key_mode,
        gate_target_cols=locked.target_cols,
        used_prev_context_seed=int(bool(prev_context_seed)),
        prev_context_seed_source="canonical_evidence" if canonical_evidence else "prev_context_snapshot",
    )
    return locked


async def run_planner_stage2(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    locked_strategy: LockedStrategy,
    build_llm: Any,
    planner_stage2_slots_cls: Any,
    load_prompt_file: Any,
    sanitize_llm_json: Any,
    log_event: Any,
    planner_stage2_prompt_version: str,
    planner_disable_thinking: bool,
    planner_temperature: float,
) -> Any:
    """locked strategy를 prompt에 주입한 stage 2 planner를 실행해 ids/filter/retrieval_query와 한도를 채운다.
    stage 2는 가변 슬롯만 내리게 되며, locked strategy는 JSON 형태로 그대로 넘겨 모드 변조를 막는다.
    """
    llm = build_llm("solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=planner_stage2_slots_cls)
    system_prompt = await load_prompt_file(Path(f"prompts/planner_stage2_{planner_stage2_prompt_version}.md"))
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "{format_instructions}\n<locked_strategy>{locked_strategy}</locked_strategy>\n<user_query>{question}</user_query>"),
        ]
    )
    planner_llm = llm.bind(
        reasoning_effort="low",
        include_reasoning=False,
        disable_thinking=planner_disable_thinking,
        temperature=planner_temperature,
        top_p=1.0,
        max_tokens=300,
    )
    chain = prompt | planner_llm | sanitize_llm_json | parser
    slots = await chain.ainvoke(
        {
            "format_instructions": parser.get_format_instructions(),
            "question": question,
            "locked_strategy": json.dumps(locked_strategy.to_prompt_payload(), ensure_ascii=False),
        }
    )
    log_event(
        "PLANNER.STAGE2",
        request_id=request_id,
        conversation_id=conversation_id,
        confidence=round(slots.confidence, 3),
        planner_stage2_prompt_version=planner_stage2_prompt_version,
    )
    return slots


def assemble_question_analysis(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    stage1: Any,
    stage2: Any,
    locked_strategy: LockedStrategy,
    sanitize_ids_map_semantics: Any,
    question_analysis_cls: Any,
    log_event: Any,
    planner_schema_version: str,
    max_top_k_size: int,
    planner_stagewise_enabled: bool,
    planner_stage1_prompt_version: str,
    planner_stage2_prompt_version: str,
) -> Any:
    # `question_analysis` is a planner artifact. Retrieval/runtime stages must use
    # the final assembled execution strategy and normalized intent as source of truth.
    """stage 1, locked strategy, stage 2 slots를 합쳐 최종 question analysis payload를 만든다.
    ids_map semantic sanitize, regate, top-k clamp, schema version 주입까지 포함한 planner orchestration의 최종 합성 단계다.
    """
    ids_map, invalids = sanitize_ids_map_semantics(stage2.ids_map, question_text=question)
    for item in invalids:
        log_event(
            "PLANNER.IDS_MAP.INVALID_VALUE",
            request_id=request_id,
            conversation_id=conversation_id,
            key=item["key"],
            value=item["value"],
        )
    payload = merge_locked_strategy_slots(
        schema_version=planner_schema_version,
        locked=locked_strategy,
        slots={
            "ids_map": ids_map,
            "filters": stage2.filters,
            "limit": min(stage2.limit, max_top_k_size),
            "retrieval_query": stage2.retrieval_query or question,
            "confidence": min(stage1.confidence, stage2.confidence),
        },
        default_query=question,
    )
    project_key_ambiguity = bool(has_ambiguous_project_key_label(question))
    has_explicit_pjt_id_label = bool(has_explicit_project_id_label(question))
    has_explicit_pjt_no_label = bool(has_explicit_project_no_label(question))
    stage2_filters = dict(stage2.filters or {})
    researcher_gate_terms = [
        str(value).strip()
        for key in ("participant_researcher_name", "participant_researcher_names", "participant_researcher", "participant_researchers", "researcher_name", "researcher_names", "researcher", "people_name")
        for value in ((stage2_filters.get(key) or []) if isinstance(stage2_filters.get(key), list) else [stage2_filters.get(key)] if stage2_filters.get(key) else [])
        if str(value).strip()
    ]
    generic_org_gate = bool(stage2_filters.get("org_name"))
    role_scoped_org_gate = bool(stage2_filters.get("org_role") or stage2_filters.get("lead_org_name") or stage2_filters.get("performing_org_name") or stage2_filters.get("participant_org_name") or stage2_filters.get("people_affiliation_org_name"))
    unresolved_anchor_pair = bool(researcher_gate_terms and generic_org_gate and not role_scoped_org_gate)
    regate_reason = None
    payload_mode = str(payload.get("mode") or "").strip().lower()
    payload_join_key_mode = str(payload.get("join_key_mode") or "").strip().lower() or None
    has_pjt_id_seed = bool(ids_map.get("pjt_id"))
    has_pjt_no_seed = bool(ids_map.get("pjt_no"))
    if payload_mode == "join":
        if payload_join_key_mode == "instance" and not has_pjt_id_seed:
            payload["mode"] = "lookup"
            payload["join_key_mode"] = None
            regate_reason = "sanitized_instance_seed_unresolved_anchor_pair" if unresolved_anchor_pair else "sanitized_instance_seed_missing"
        elif payload_join_key_mode == "group" and not has_pjt_no_seed:
            payload["mode"] = "lookup"
            payload["join_key_mode"] = None
            regate_reason = "sanitized_group_seed_missing"
    if regate_reason:
        log_event(
            "PLANNER.REGATE",
            request_id=request_id,
            conversation_id=conversation_id,
            reason=regate_reason,
            original_mode=payload_mode,
            downgraded_mode=payload.get("mode"),
            relation=payload.get("relation"),
            original_join_key_mode=payload_join_key_mode,
            has_pjt_id_seed=int(has_pjt_id_seed),
            has_pjt_no_seed=int(has_pjt_no_seed),
            project_key_ambiguity=int(project_key_ambiguity),
            has_explicit_pjt_id_label=int(has_explicit_pjt_id_label),
            has_explicit_pjt_no_label=int(has_explicit_pjt_no_label),
            unresolved_anchor_pair=int(unresolved_anchor_pair),
        )
    payload["planner_source"] = "stagewise"
    qa = question_analysis_cls.model_validate(payload)
    log_event(
        "PLANNER.ASSEMBLE",
        request_id=request_id,
        conversation_id=conversation_id,
        artifact="question_analysis",
        mode=qa.mode,
        action=qa.action,
        relation=qa.relation,
        join_key_mode=qa.join_key_mode,
        target_cols=qa.target_cols,
        planner_output_mode=qa.mode,
        planner_output_relation=qa.relation,
        planner_output_target_cols=qa.target_cols,
        project_key_ambiguity=int(project_key_ambiguity),
        planner_stagewise_enabled=int(planner_stagewise_enabled),
        planner_stage1_prompt_version=planner_stage1_prompt_version,
        planner_stage2_prompt_version=planner_stage2_prompt_version,
    )
    return qa


async def run_stagewise_question_analysis(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    chat_history: list[BaseMessage],
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    normalized_intent: Any,
    build_llm: Any,
    planner_stage1_decision_cls: Any,
    planner_stage2_slots_cls: Any,
    question_analysis_cls: Any,
    load_prompt_file: Any,
    sanitize_llm_json: Any,
    sanitize_ids_map_semantics: Any,
    log_event: Any,
    planner_stage1_prompt_version: str,
    planner_stage2_prompt_version: str,
    planner_disable_thinking: bool,
    planner_temperature: float,
    planner_schema_version: str,
    planner_stagewise_enabled: bool,
    planner_stage2_regate_seed_allowed_keys: set[str],
    max_top_k_size: int,
) -> Any:
    """stage 1 -> gate -> stage 2 -> assemble 순서로 stagewise planner 전체 흐름을 실행한다.
    app entry는 이 파사드만 호출하면 stagewise planner 전체 절차와 로그가 한 곳에서 완결된다.
    """
    stage1 = await run_planner_stage1(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        chat_history=chat_history,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        normalized_intent=normalized_intent,
        build_llm=build_llm,
        planner_stage1_decision_cls=planner_stage1_decision_cls,
        load_prompt_file=load_prompt_file,
        sanitize_llm_json=sanitize_llm_json,
        log_event=log_event,
        planner_stage1_prompt_version=planner_stage1_prompt_version,
        planner_disable_thinking=planner_disable_thinking,
        planner_temperature=planner_temperature,
    )
    locked_strategy = determine_locked_strategy(
        stage1=stage1,
        normalized_intent=normalized_intent,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        planner_stage2_regate_seed_allowed_keys=planner_stage2_regate_seed_allowed_keys,
        log_event=log_event,
    )
    stage2 = await run_planner_stage2(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        locked_strategy=locked_strategy,
        build_llm=build_llm,
        planner_stage2_slots_cls=planner_stage2_slots_cls,
        load_prompt_file=load_prompt_file,
        sanitize_llm_json=sanitize_llm_json,
        log_event=log_event,
        planner_stage2_prompt_version=planner_stage2_prompt_version,
        planner_disable_thinking=planner_disable_thinking,
        planner_temperature=planner_temperature,
    )
    locked_strategy = regate_locked_strategy(
        request_id=request_id,
        conversation_id=conversation_id,
        stage1=stage1,
        stage2=stage2,
        locked_strategy=locked_strategy,
        allowed_keys=planner_stage2_regate_seed_allowed_keys,
        log_event=log_event,
    )
    return assemble_question_analysis(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        stage1=stage1,
        stage2=stage2,
        locked_strategy=locked_strategy,
        sanitize_ids_map_semantics=sanitize_ids_map_semantics,
        question_analysis_cls=question_analysis_cls,
        log_event=log_event,
        planner_schema_version=planner_schema_version,
        max_top_k_size=max_top_k_size,
        planner_stagewise_enabled=planner_stagewise_enabled,
        planner_stage1_prompt_version=planner_stage1_prompt_version,
        planner_stage2_prompt_version=planner_stage2_prompt_version,
    )
