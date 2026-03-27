"""Stagewise planner orchestration helpers.

This module owns the LLM-driven stage-1/stage-2 planner flow so the app entry
module can stay focused on composition-root concerns.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate

from apps.api.services.canonical_context import render_canonical_evidence_text
from apps.api.services.planner_context_cards import build_planner_domain_cards
from apps.api.services.followup_anchor import anchor_to_seed_map
from apps.api.services.view_state import DisplaySnapshot, FocusEntity, render_display_snapshot_text
from apps.core.planner_contract import StrategyViolation
from apps.core.planner_stage15_types import PlannerEntityRolePlan
from apps.core.planner_staged import (
    DeterministicGateStrategy,
    collect_regate_seed_map,
    compose_locked_strategy,
    extract_single_project_seed,
    merge_locked_strategy_slots,
    regate_locked_strategy,
)
from apps.core.planner_surface_signals import SurfaceSignals, collect_surface_signals
from apps.core.planner_validation import Stage2ValidationResult, validate_stage2_slots
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

_PREV_CONTEXT_SEED_ID_KEYS = (
    "pjt_id",
    "pjt_no",
    "rst_id",
    "person_no",
    "org_id",
    "org_code",
    "biz_no",
    "doi",
    "issn",
)


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


def _render_prompt_template(template: str, **values: Any) -> str:
    template_text = str(template or "")
    placeholder_names = sorted(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template_text)))
    missing = [name for name in placeholder_names if name not in values]
    if missing:
        raise RuntimeError(f"planner prompt placeholder missing: {missing}")
    rendered = template_text
    for key in placeholder_names:
        value = values[key]
        replacement = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        rendered = rendered.replace("{" + key + "}", str(replacement))
    return rendered


def _signals_payload(signals: SurfaceSignals) -> dict[str, Any]:
    return {
        "explicit_count": signals.explicit_count,
        "ordinal_ref": signals.ordinal_ref,
        "years": list(signals.years),
        "id_like_terms": list(signals.id_like_terms),
        "people_terms": list(signals.people_terms),
        "org_terms": list(signals.org_terms),
        "perf_types": list(signals.perf_types),
        "followup_cues": list(signals.followup_cues),
        "high_salience_terms": list(signals.high_salience_terms),
    }


def _entity_role_payload(plan: PlannerEntityRolePlan) -> dict[str, Any]:
    return plan.model_dump()


def _validation_hints_payload(result: Stage2ValidationResult) -> dict[str, Any]:
    return {
        "errors": list(result.errors),
        "missing_people_terms": list(result.missing_people_terms),
        "missing_org_terms": list(result.missing_org_terms),
        "missing_years": list(result.missing_years),
        "missing_perf_types": list(result.missing_perf_types),
    }


def _has_explicit_perf_seed(ids_map: dict[str, list[str]]) -> bool:
    return any(ids_map.get(key) for key in ("rst_id", "doi", "issn", "perf_id", "paper_id", "patent_reg_no", "patent_app_no"))


def _enforce_runtime_legality(
    *,
    normalized_intent: Any,
    locked_strategy: DeterministicGateStrategy,
    stage2: Any,
    strategy_violation_cls: type[Exception],
) -> None:
    def _raise(error_code: str, reason: str) -> None:
        try:
            raise strategy_violation_cls(error_code=error_code, reason=reason)  # type: ignore[misc]
        except TypeError:
            if strategy_violation_cls is StrategyViolation:
                raise StrategyViolation(error_code=error_code, reason=reason)
            raise strategy_violation_cls(f"{error_code}: {reason}")

    ids_map = dict(getattr(stage2, "ids_map", None) or {})
    people_terms = list(getattr(normalized_intent, "people_terms", None) or [])
    org_terms = list(getattr(normalized_intent, "org_terms", None) or [])
    broad_people_org_query = bool(people_terms or org_terms)
    has_anchor_seed = bool(dict(locked_strategy.prev_context_seed or {}) or dict(locked_strategy.gate_seed_map or {}))

    if locked_strategy.head == "perf" and locked_strategy.action == "detail" and not _has_explicit_perf_seed(ids_map):
        _raise("PLANNER_PERF_DETAIL_EXPLICIT_ID_REQUIRED", "perf detail requires explicit perf id")
    if broad_people_org_query and str(locked_strategy.mode or "").strip().lower() == "join":
        _raise("PLANNER_BROAD_PEOPLE_ORG_JOIN_FORBIDDEN", "broad people/org query cannot use JOIN")
    if locked_strategy.relation in {"project_perf", "perf_project"} and not has_anchor_seed:
        _raise("PLANNER_RELATION_EXPLICIT_ANCHOR_REQUIRED", "relation query requires explicit anchor")


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
        seed_map = anchor_to_seed_map(focus_entity)
        if seed_map:
            return seed_map
    if display_snapshot is not None and len(display_snapshot.items) == 1:
        item = display_snapshot.items[0]
        seed_map = anchor_to_seed_map(FocusEntity(
            kind=item.entity_kind,
            source="display_snapshot",
            doc_id=item.doc_id,
            pjt_id=item.pjt_id,
            pjt_no=item.pjt_no,
            rst_id=item.rst_id,
            person_no=item.person_no,
            org_id=item.org_id,
            org_code=item.org_code,
            biz_no=item.biz_no,
            doi=item.doi,
            issn=item.issn,
            title_text=item.title_text,
        ))
        if seed_map:
            return seed_map
    if canonical_evidence:
        unique_ids: dict[str, set[str]] = {key: set() for key in _PREV_CONTEXT_SEED_ID_KEYS}
        for item in canonical_evidence:
            if not isinstance(item, dict):
                continue
            ids = item.get("ids") or {}
            for key in _PREV_CONTEXT_SEED_ID_KEYS:
                value = str(ids.get(key) or "").strip()
                if value:
                    unique_ids[key].add(value)
        for key in _PREV_CONTEXT_SEED_ID_KEYS:
            if len(unique_ids[key]) == 1:
                return {key: [next(iter(unique_ids[key]))]}
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
    cards: dict[str, str],
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
    system_prompt = _render_prompt_template(
        await load_prompt_file(Path(f"prompts/planner_stage1_{planner_stage1_prompt_version}.md")),
        **cards,
    )
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


async def run_planner_stage15(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    locked_strategy: DeterministicGateStrategy,
    signals: SurfaceSignals,
    cards: dict[str, str],
    build_llm: Any,
    planner_stage15_plan_cls: Any,
    load_prompt_file: Any,
    sanitize_llm_json: Any,
    log_event: Any,
    planner_stage15_prompt_version: str,
    planner_disable_thinking: bool,
    planner_temperature: float,
) -> PlannerEntityRolePlan:
    llm = build_llm("solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=planner_stage15_plan_cls)
    system_prompt = _render_prompt_template(
        await load_prompt_file(Path(f"prompts/planner_stage15_{planner_stage15_prompt_version}.md")),
        **cards,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
            (
                "human",
                "{format_instructions}\n<locked_strategy>{locked_strategy}</locked_strategy>\n<surface_signals>{surface_signals}</surface_signals>\n<user_query>{question}</user_query>",
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
    stage15 = await chain.ainvoke(
        {
            "format_instructions": parser.get_format_instructions(),
            "question": question,
            "locked_strategy": json.dumps(locked_strategy.to_prompt_payload(), ensure_ascii=False),
            "surface_signals": json.dumps(_signals_payload(signals), ensure_ascii=False),
        }
    )
    log_event(
        "PLANNER.STAGE15",
        request_id=request_id,
        conversation_id=conversation_id,
        confidence=round(stage15.confidence, 3),
        org_role_hint=stage15.org_role_hint,
        anchor_required=int(stage15.anchor_required),
        semantic_kind=stage15.semantic_kind,
        perf_type_policy=stage15.perf_type_policy,
        must_keep_terms=stage15.must_keep_terms,
        planner_stage15_prompt_version=planner_stage15_prompt_version,
    )
    return stage15


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
    signals: SurfaceSignals,
    entity_role_plan: PlannerEntityRolePlan,
    cards: dict[str, str],
    validation_hints: dict[str, Any] | None = None,
    previous_output: dict[str, Any] | None = None,
    planner_disable_thinking: bool,
    planner_temperature: float,
) -> Any:
    llm = build_llm("solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=planner_stage2_slots_cls)
    system_prompt = _render_prompt_template(
        await load_prompt_file(Path(f"prompts/planner_stage2_{planner_stage2_prompt_version}.md")),
        **cards,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
            (
                "human",
                "{format_instructions}\n<locked_strategy>{locked_strategy}</locked_strategy>\n<surface_signals>{surface_signals}</surface_signals>\n<entity_role_plan>{entity_role_plan}</entity_role_plan>\n<validation_hints>{validation_hints}</validation_hints>\n<previous_output>{previous_output}</previous_output>\n<user_query>{question}</user_query>",
            ),
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
            "surface_signals": json.dumps(_signals_payload(signals), ensure_ascii=False),
            "entity_role_plan": json.dumps(_entity_role_payload(entity_role_plan), ensure_ascii=False),
            "validation_hints": json.dumps(validation_hints or {}, ensure_ascii=False),
            "previous_output": json.dumps(previous_output or {}, ensure_ascii=False),
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
        retrieval_query=str(getattr(slots, "retrieval_query", "") or ""),
        filters=dict(getattr(slots, "filters", {}) or {}),
        limit=getattr(slots, "limit", None),
        display_limit=getattr(slots, "display_limit", None),
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
    planner_stage15_prompt_version: str,
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
        planner_stage15_prompt_version=planner_stage15_prompt_version,
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
    planner_stage15_plan_cls: Any,
    planner_stage2_slots_cls: Any,
    question_analysis_cls: Any,
    load_prompt_file: Any,
    sanitize_llm_json: Any,
    sanitize_ids_map_semantics: Any,
    log_event: Any,
    planner_stage1_prompt_version: str,
    planner_stage15_prompt_version: str,
    planner_stage2_prompt_version: str,
    planner_disable_thinking: bool,
    planner_temperature: float,
    planner_schema_version: str,
    planner_stagewise_enabled: bool,
    planner_stage2_regate_seed_allowed_keys: set[str],
    max_top_k_size: int,
    strategy_violation_cls: type[Exception] = StrategyViolation,
) -> Any:
    display_snapshot = getattr(view_state, "latest_display_snapshot", None)
    focus_entity = getattr(view_state, "latest_focus_entity", None)
    stage1_prompt_name = f"planner_stage1_{planner_stage1_prompt_version}"
    stage15_prompt_name = f"planner_stage15_{planner_stage15_prompt_version}"
    stage2_prompt_name = f"planner_stage2_{planner_stage2_prompt_version}"
    stage1_cards = await build_planner_domain_cards(load_prompt_file=load_prompt_file, prompt_name=stage1_prompt_name)
    stage15_cards = await build_planner_domain_cards(load_prompt_file=load_prompt_file, prompt_name=stage15_prompt_name)
    stage2_cards = await build_planner_domain_cards(load_prompt_file=load_prompt_file, prompt_name=stage2_prompt_name)
    signals = collect_surface_signals(question, normalized_intent)
    log_event(
        "PLANNER.SIGNALS",
        request_id=request_id,
        conversation_id=conversation_id,
        **_signals_payload(signals),
    )
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
        cards=stage1_cards,
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
    stage15 = await run_planner_stage15(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        locked_strategy=locked_strategy,
        signals=signals,
        cards=stage15_cards,
        build_llm=build_llm,
        planner_stage15_plan_cls=planner_stage15_plan_cls,
        load_prompt_file=load_prompt_file,
        sanitize_llm_json=sanitize_llm_json,
        log_event=log_event,
        planner_stage15_prompt_version=planner_stage15_prompt_version,
        planner_disable_thinking=planner_disable_thinking,
        planner_temperature=planner_temperature,
    )
    stage2 = await run_planner_stage2(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        locked_strategy=locked_strategy,
        signals=signals,
        entity_role_plan=stage15,
        cards=stage2_cards,
        build_llm=build_llm,
        planner_stage2_slots_cls=planner_stage2_slots_cls,
        load_prompt_file=load_prompt_file,
        sanitize_llm_json=sanitize_llm_json,
        log_event=log_event,
        planner_stage2_prompt_version=planner_stage2_prompt_version,
        planner_disable_thinking=planner_disable_thinking,
        planner_temperature=planner_temperature,
    )
    validation = validate_stage2_slots(
        question=question,
        signals=signals,
        entity_role_plan=stage15,
        locked_strategy=locked_strategy,
        stage2_slots=stage2,
    )
    if not validation.ok:
        log_event(
            "PLANNER.STAGE2.VALIDATION_FAILED",
            request_id=request_id,
            conversation_id=conversation_id,
            errors=validation.errors,
            missing_people_terms=validation.missing_people_terms,
            missing_org_terms=validation.missing_org_terms,
            missing_years=validation.missing_years,
            missing_perf_types=validation.missing_perf_types,
        )
        previous_output = stage2.model_dump() if hasattr(stage2, "model_dump") else {}
        stage2 = await run_planner_stage2(
            question=question,
            conversation_id=conversation_id,
            request_id=request_id,
            locked_strategy=locked_strategy,
            signals=signals,
            entity_role_plan=stage15,
            cards=stage2_cards,
            validation_hints=_validation_hints_payload(validation),
            previous_output=previous_output,
            build_llm=build_llm,
            planner_stage2_slots_cls=planner_stage2_slots_cls,
            load_prompt_file=load_prompt_file,
            sanitize_llm_json=sanitize_llm_json,
            log_event=log_event,
            planner_stage2_prompt_version=planner_stage2_prompt_version,
            planner_disable_thinking=planner_disable_thinking,
            planner_temperature=planner_temperature,
        )
        validation = validate_stage2_slots(
            question=question,
            signals=signals,
            entity_role_plan=stage15,
            locked_strategy=locked_strategy,
            stage2_slots=stage2,
        )
        if not validation.ok:
            log_event(
                "PLANNER.STAGE2.RETRY_FAILED",
                request_id=request_id,
                conversation_id=conversation_id,
                errors=validation.errors,
            )
            explicit_count = signals.explicit_count or 20
            fallback_limit = max(1, min(int(explicit_count), max_top_k_size))
            fallback_payload = stage2.model_dump() if hasattr(stage2, "model_dump") else {}
            fallback_payload["retrieval_query"] = question
            fallback_payload["limit"] = max(1, min(int(fallback_payload.get("limit") or fallback_limit), max_top_k_size))
            fallback_payload["display_limit"] = max(1, min(int(fallback_payload.get("display_limit") or fallback_payload["limit"]), fallback_payload["limit"]))
            fallback_payload["confidence"] = float(fallback_payload.get("confidence") or 0.0)
            stage2 = planner_stage2_slots_cls.model_validate(fallback_payload)
    _enforce_runtime_legality(
        normalized_intent=normalized_intent,
        locked_strategy=locked_strategy,
        stage2=stage2,
        strategy_violation_cls=strategy_violation_cls,
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
        planner_stage15_prompt_version=planner_stage15_prompt_version,
        planner_stage2_prompt_version=planner_stage2_prompt_version,
    )


