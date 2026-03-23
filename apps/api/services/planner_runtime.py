"""Stagewise planner orchestration helpers.

This module owns the LLM-driven stage-1/stage-2 planner flow so the app entry
module can stay focused on composition-root concerns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from apps.api.services.canonical_context import render_canonical_evidence_text
from apps.api.services.view_state import DisplaySnapshot, FocusEntity, render_display_snapshot_text
from apps.core.planner_staged import (
    DeterministicGateStrategy,
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


_STAGE2_ALLOWED_OUTPUT_FIELDS = {
    "ids_map",
    "candidate_keys",
    "project_key_policy",
    "join_resolution_policy",
    "filters",
    "retrieval_query",
    "limit",
    "display_limit",
    "confidence",
}


def _normalize_stage2_slots_payload(
    raw_payload: Any,
    *,
    request_id: Optional[str],
    conversation_id: str,
    log_event: Any,
) -> dict[str, Any]:
    if isinstance(raw_payload, str):
        payload = json.loads(raw_payload)
    else:
        payload = raw_payload
    if not isinstance(payload, dict):
        raise ValueError(f"Planner stage2 payload must be an object, got {type(payload).__name__}")

    cleaned = dict(payload)
    dropped = {key: cleaned.pop(key) for key in list(cleaned.keys()) if key not in _STAGE2_ALLOWED_OUTPUT_FIELDS}
    if dropped:
        log_event(
            "PLANNER.STAGE2.EXTRA_FIELDS_DROPPED",
            request_id=request_id,
            conversation_id=conversation_id,
            dropped_fields=sorted(dropped.keys()),
            dropped_non_null_fields=sorted(key for key, value in dropped.items() if value is not None),
        )
    return cleaned

def intent_snapshot(normalized_intent: Any) -> dict[str, Any]:
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
    display_snapshot: Optional[DisplaySnapshot] = None,
) -> str:
    if display_snapshot is not None and display_snapshot.items:
        return render_display_snapshot_text(display_snapshot, max_chars=1200)
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
    question: str,
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    display_snapshot: Optional[DisplaySnapshot] = None,
    focus_entity: Optional[FocusEntity] = None,
    allow_ordinal_resolution: bool = False,
    default_context_kind: str = "project",
) -> dict[str, list[str]]:
    if focus_entity is not None:
        if focus_entity.pjt_id:
            return {"pjt_id": [focus_entity.pjt_id]}
        if focus_entity.pjt_no:
            return {"pjt_no": [focus_entity.pjt_no]}
    if display_snapshot is not None and len(display_snapshot.items) == 1:
        item = display_snapshot.items[0]
        if item.pjt_id:
            return {"pjt_id": [item.pjt_id]}
        if item.pjt_no:
            return {"pjt_no": [item.pjt_no]}
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
    seed = extract_single_project_seed(prev_context)
    if seed:
        return seed
    return {}


async def run_planner_stage1(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    chat_history: list[BaseMessage],
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    normalized_intent: Any,
    display_snapshot: Optional[DisplaySnapshot],
    build_llm: Any,
    planner_stage1_decision_cls: Any,
    load_prompt_file: Any,
    sanitize_llm_json: Any,
    log_event: Any,
    planner_stage1_prompt_version: str,
    planner_disable_thinking: bool,
    planner_temperature: float,
) -> Any:
    llm = build_llm("solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=planner_stage1_decision_cls)
    history_str = "\n".join([f"{type(m).__name__}: {m.content}" for m in chat_history[-4:]])
    prev_context_text = _planner_prev_context_text(
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        normalized_intent=normalized_intent,
        display_snapshot=display_snapshot,
    )
    system_prompt = await load_prompt_file(Path(f"prompts/planner_stage1_{planner_stage1_prompt_version}.md"))
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
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
        prev_context_source="display_snapshot" if display_snapshot and display_snapshot.items else "canonical_evidence" if canonical_evidence else "prev_context_snapshot",
    )
    return stage1


def determine_locked_strategy(
    *,
    question: str,
    stage1: Any,
    normalized_intent: Any,
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    display_snapshot: Optional[DisplaySnapshot],
    focus_entity: Optional[FocusEntity],
    planner_stage2_regate_seed_allowed_keys: set[str],
    log_event: Any,
) -> DeterministicGateStrategy:
    base_ids_map = dict(getattr(normalized_intent, "ids_map", {}) or {})
    prev_context_seed = _extract_prev_context_seed(
        question=question,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        display_snapshot=display_snapshot,
        focus_entity=focus_entity,
        allow_ordinal_resolution=bool(getattr(stage1, "referential_followup", False)),
        default_context_kind=str(getattr(normalized_intent, "base_route", None) or "project").strip().lower() or "project",
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
        prev_context_seed_source="focus_entity" if focus_entity else "display_snapshot" if display_snapshot and display_snapshot.items else "canonical_evidence" if canonical_evidence else "prev_context_snapshot",
    )
    return locked


async def run_planner_stage2(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    locked_strategy: DeterministicGateStrategy,
    build_llm: Any,
    planner_stage2_slots_cls: Any,
    load_prompt_file: Any,
    sanitize_llm_json: Any,
    log_event: Any,
    planner_stage2_prompt_version: str,
    planner_disable_thinking: bool,
    planner_temperature: float,
) -> Any:
    llm = build_llm("solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=planner_stage2_slots_cls)
    system_prompt = await load_prompt_file(Path(f"prompts/planner_stage2_{planner_stage2_prompt_version}.md"))
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
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
    chain = prompt | planner_llm | sanitize_llm_json
    raw_slots = await chain.ainvoke(
        {
            "format_instructions": parser.get_format_instructions(),
            "question": question,
            "locked_strategy": json.dumps(locked_strategy.to_prompt_payload(), ensure_ascii=False),
        }
    )
    normalized_slots = _normalize_stage2_slots_payload(
        raw_slots,
        request_id=request_id,
        conversation_id=conversation_id,
        log_event=log_event,
    )
    slots = parser.parse(json.dumps(normalized_slots, ensure_ascii=False))
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
    locked_strategy: DeterministicGateStrategy,
    sanitize_ids_map_semantics: Any,
    question_analysis_cls: Any,
    log_event: Any,
    planner_schema_version: str,
    max_top_k_size: int,
    planner_stagewise_enabled: bool,
    planner_stage1_prompt_version: str,
    planner_stage2_prompt_version: str,
) -> Any:
    ids_map, candidate_keys, invalids = sanitize_ids_map_semantics(stage2.ids_map, question_text=question, candidate_keys=getattr(stage2, "candidate_keys", None))
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
            "candidate_keys": candidate_keys,
            "project_key_policy": getattr(stage2, "project_key_policy", None),
            "join_resolution_policy": getattr(stage2, "join_resolution_policy", None),
            "filters": stage2.filters,
            "limit": min(stage2.limit, max_top_k_size),
            "display_limit": min(getattr(stage2, "display_limit", stage2.limit), min(stage2.limit, max_top_k_size)),
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
    assembly_adjustment_kind = None
    assembly_adjustment_reason = None
    assembled_mode = str(payload.get("mode") or "").strip().lower()
    assembled_join_key_mode = str(payload.get("join_key_mode") or "").strip().lower() or None
    payload_project_key_policy = str(payload.get("project_key_policy") or "").strip().lower() or None
    payload_join_resolution_policy = str(payload.get("join_resolution_policy") or "").strip().lower() or None
    candidate_project_keys = list((candidate_keys or {}).get("project_key") or [])
    has_pjt_id_seed = bool(ids_map.get("pjt_id"))
    has_pjt_no_seed = bool(ids_map.get("pjt_no"))
    gate_join_key_mode = locked_strategy.join_key_mode
    if assembled_mode == "join":
        if assembled_join_key_mode == "instance" and not has_pjt_id_seed:
            payload["mode"] = "lookup"
            payload["join_key_mode"] = None
            assembly_adjustment_kind = "seed_loss_downgrade"
            assembly_adjustment_reason = "sanitized_instance_seed_unresolved_anchor_pair" if unresolved_anchor_pair else "sanitized_instance_seed_missing"
        elif assembled_join_key_mode == "group" and not has_pjt_no_seed:
            payload["mode"] = "lookup"
            payload["join_key_mode"] = None
            assembly_adjustment_kind = "seed_loss_downgrade"
            assembly_adjustment_reason = "sanitized_group_seed_missing"
        elif payload_project_key_policy == "ambiguous_or" and candidate_project_keys:
            payload["join_key_mode"] = "deferred"
            payload.setdefault("join_resolution_policy", payload_join_resolution_policy or "auto_resolve")
            assembly_adjustment_kind = "assembly_legalize"
            assembly_adjustment_reason = "ambiguous_project_key_deferred_join"
    if assembly_adjustment_reason:
        log_event(
            "PLANNER.ASSEMBLE.JOIN_RESHAPED",
            request_id=request_id,
            conversation_id=conversation_id,
            assembly_adjustment_kind=assembly_adjustment_kind,
            assembly_adjustment_reason=assembly_adjustment_reason,
            gate_join_key_mode=gate_join_key_mode,
            assembled_join_key_mode=payload.get("join_key_mode"),
            original_mode=assembled_mode,
            final_mode=payload.get("mode"),
            relation=payload.get("relation"),
            original_join_key_mode=assembled_join_key_mode,
            final_join_key_mode=payload.get("join_key_mode"),
            has_pjt_id_seed=int(has_pjt_id_seed),
            has_pjt_no_seed=int(has_pjt_no_seed),
            candidate_project_key_count=len(candidate_project_keys),
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
        gate_join_key_mode=gate_join_key_mode,
        assembled_join_key_mode=qa.join_key_mode,
        assembly_adjustment_kind=assembly_adjustment_kind,
        assembly_adjustment_reason=assembly_adjustment_reason,
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
    view_state: Any = None,
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
    display_snapshot = getattr(view_state, "latest_display_snapshot", None)
    focus_entity = getattr(view_state, "latest_focus_entity", None)
    stage1 = await run_planner_stage1(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        chat_history=chat_history,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        normalized_intent=normalized_intent,
        display_snapshot=display_snapshot,
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
        question=question,
        stage1=stage1,
        normalized_intent=normalized_intent,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        display_snapshot=display_snapshot,
        focus_entity=focus_entity,
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


