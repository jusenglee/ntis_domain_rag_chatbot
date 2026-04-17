"""Stagewise planner orchestration helpers.

This module owns the LLM-driven stage-1/stage-2 planner flow so the app entry
module can stay focused on composition-root concerns.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from apps.api.contracts.runtime_contracts import (
    extract_unsupported_project_key_aliases,
    has_ambiguous_project_key_label,
    has_explicit_project_id_label,
    has_explicit_project_no_label,
    infer_project_key_axis,
    sanitize_ids_map_semantics,
)
from apps.api.contracts.workflow_models import HardContractV1, QuestionAnalysis, SoftStrategyHintsV1
from apps.api.runtime_helpers import log_event
from apps.chat.llm_json import sanitize_llm_json
from apps.chat.llm_runtime import build_llm, load_prompt_file

from apps.platform.langchain_compat import BaseMessage, ChatPromptTemplate, PydanticOutputParser, SystemMessage

from apps.evidence.canonical_context import render_canonical_evidence_text
from apps.conversation.followup_anchor import anchor_to_seed_map
from apps.conversation.view_state import (
    DisplaySnapshot,
    FocusEntity,
    get_active_subject_entity,
    render_display_snapshot_text,
)
from apps.planner.planner_contract import StrategyViolation
from apps.planner.planner_context_cards import build_planner_domain_cards
from apps.planner.planner_defaults import (
    PLANNER_DISABLE_THINKING,
    PLANNER_RUNTIME_MAX_TOP_K_SIZE,
    PLANNER_RUNTIME_SCHEMA_VERSION,
    PLANNER_STAGE1_PROMPT_VERSION,
    PLANNER_STAGE15_PROMPT_VERSION,
    PLANNER_STAGE2_PROMPT_VERSION,
    PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS,
    PLANNER_STAGEWISE_ENABLED,
    PLANNER_TEMPERATURE,
)
from apps.planner.prompt_asset_paths import planner_prompt_path
from apps.planner.planner_stage15_types import PlannerEntityRolePlan
from apps.planner.planner_staged import (
    DeterministicGateStrategy,
    collect_regate_seed_map,
    compose_locked_strategy,
    extract_single_project_seed,
    merge_locked_strategy_slots,
    regate_locked_strategy,
)
from apps.planner.planner_surface_signals import SurfaceSignals, collect_surface_signals
from apps.planner.planner_validation import Stage2ValidationResult, validate_stage2_slots
from apps.planner.query_intent import ORG_CUES
from apps.platform.schemas import PlannerStage1Decision, PlannerStage2Slots


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
_RESEARCHER_FILTER_KEYS = (
    "participant_researcher_name",
    "participant_researcher_names",
    "participant_researcher",
    "participant_researchers",
    "researcher_name",
    "researcher_names",
    "researcher",
    "people_name",
)
_GENERIC_ORG_FILTER_KEYS = ("org_name",)
_ROLE_SCOPED_ORG_FILTER_KEYS = (
    "lead_org_name",
    "performing_org_name",
    "participant_org_name",
    "people_affiliation_org_name",
)
_ID_LIKE_FILTER_TERM_RE = re.compile(r"^(?:\d{8,12}|(?=.*\d)[A-Za-z0-9][A-Za-z0-9_-]{3,63})$")
_PROJECT_AXIS_CUES = ("과제", "project", "pjt")
_EXPLICIT_ORG_QUERY_CUES = tuple(str(cue or "").strip().lower() for cue in ORG_CUES) + (
    "소속",
    "affiliation",
    "organization",
    "institution",
    "company",
    "org",
)


def _normalize_stage2_slots_payload(
    raw_payload: Any,
    *,
    request_id: Optional[str],
    conversation_id: str,
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
        "missing_must_keep_terms": list(result.missing_must_keep_terms),
        "missing_people_terms": list(result.missing_people_terms),
        "missing_org_terms": list(result.missing_org_terms),
        "missing_years": list(result.missing_years),
        "missing_perf_types": list(result.missing_perf_types),
    }


def _normalize_terms(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _append_terms_to_query(query: str, terms: list[str]) -> str:
    merged: list[str] = []
    base = str(query or "").strip()
    if base:
        merged.append(base)
    for term in terms:
        text = str(term or "").strip()
        if not text or text in merged:
            continue
        merged.append(text)
    return " ".join(merged).strip()


def _semantic_query_terms_for_repair(*, entity_role_plan: PlannerEntityRolePlan, retrieval_query: str) -> list[str]:
    semantic_kind = str(getattr(entity_role_plan, "semantic_kind", "") or "").strip().lower()
    if semantic_kind != "broad_history":
        return []

    existing_query = str(retrieval_query or "").strip()
    out: list[str] = []
    seen: set[str] = set()
    for value in getattr(entity_role_plan, "must_keep_terms", []) or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        if re.fullmatch(r"(?:19|20)\d{2}", text):
            continue
        if re.fullmatch(r"\d+\s*(?:개|건|명|편|종)", text):
            continue
        if text in existing_query:
            continue
        seen.add(text)
        out.append(text)
    return out


def _looks_identifier_like_filter_term(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or re.fullmatch(r"(?:19|20)\d{2}", text):
        return False
    return bool(_ID_LIKE_FILTER_TERM_RE.fullmatch(text))


def _query_mentions_project_axis(query: str) -> bool:
    lowered = str(query or "").strip().lower()
    if not lowered:
        return False
    return any(cue in lowered for cue in _PROJECT_AXIS_CUES)


def _query_has_explicit_org_cue(query: str) -> bool:
    lowered = str(query or "").strip().lower()
    if not lowered:
        return False
    return any(cue and cue in lowered for cue in _EXPLICIT_ORG_QUERY_CUES)


def _merge_filter_terms(filters: dict[str, Any], key: str, values: list[str]) -> list[str]:
    merged = _normalize_terms([*_normalize_terms(filters.get(key)), *values])
    if merged:
        filters[key] = merged
    return merged


def _resolve_org_filter_key(entity_role_plan: PlannerEntityRolePlan) -> str:
    org_role_hint = str(getattr(entity_role_plan, "org_role_hint", "") or "").strip().lower()
    if org_role_hint == "lead_org":
        return "lead_org_name"
    if org_role_hint == "participant_org":
        return "participant_org_name"
    if org_role_hint == "affiliation_org":
        return "people_affiliation_org_name"
    return "org_name"


def _candidate_project_key_count(candidate_keys: dict[str, Any] | None) -> int:
    if not isinstance(candidate_keys, dict):
        return 0
    return len(list(candidate_keys.get("project_key") or []))


def _has_prev_anchor(locked_strategy: DeterministicGateStrategy) -> bool:
    return bool(
        dict(getattr(locked_strategy, "prev_context_seed", None) or {})
        or dict(getattr(locked_strategy, "gate_seed_map", None) or {})
    )


def _build_hard_contract(
    *,
    question: str,
    locked_strategy: DeterministicGateStrategy,
    entity_role_plan: PlannerEntityRolePlan,
    ids_map: dict[str, list[str]] | None,
    candidate_keys: dict[str, Any] | None,
    project_key_policy: str | None,
    join_key_mode: str | None,
) -> HardContractV1:
    policy_norm = str(project_key_policy or "").strip().lower() or None
    join_key_mode_norm = str(join_key_mode or getattr(locked_strategy, "join_key_mode", None) or "").strip().lower() or None
    return HardContractV1(
        explicit_project_id_label=bool(has_explicit_project_id_label(question)),
        explicit_project_no_label=bool(has_explicit_project_no_label(question)),
        ambiguous_project_key_label=bool(has_ambiguous_project_key_label(question)),
        unsupported_project_key_aliases=extract_unsupported_project_key_aliases(question),
        resolved_project_key_axis=infer_project_key_axis(ids_map=ids_map, project_key_policy=policy_norm),
        candidate_project_key_count=_candidate_project_key_count(candidate_keys),
        project_key_policy=policy_norm,
        join_anchor_required=bool(getattr(entity_role_plan, "anchor_required", False) and getattr(locked_strategy, "relation", None)),
        join_anchor_present=_has_prev_anchor(locked_strategy),
        join_key_mode=join_key_mode_norm,
        project_key_axis_locked=bool(policy_norm in {"resolved_pjt_id", "resolved_pjt_no", "anchor_locked_pjt_id", "anchor_locked_pjt_no"}),
    )


def _build_soft_strategy_hints(
    *,
    signals: SurfaceSignals,
    entity_role_plan: PlannerEntityRolePlan,
    locked_strategy: DeterministicGateStrategy,
    candidate_keys: dict[str, Any] | None = None,
) -> SoftStrategyHintsV1:
    return SoftStrategyHintsV1(
        years=list(signals.years or []),
        id_like_terms=list(signals.id_like_terms or []),
        people_terms=list(signals.people_terms or []),
        org_terms=list(signals.org_terms or []),
        perf_types=list(signals.perf_types or []),
        followup_cues=list(signals.followup_cues or []),
        must_keep_terms=list(getattr(entity_role_plan, "must_keep_terms", []) or []),
        semantic_kind=str(getattr(entity_role_plan, "semantic_kind", "") or "").strip() or None,
        perf_type_policy=str(getattr(entity_role_plan, "perf_type_policy", "") or "").strip() or None,
        org_role_hint=str(getattr(entity_role_plan, "org_role_hint", "") or "").strip() or None,
        candidate_project_key_count=_candidate_project_key_count(candidate_keys),
        has_prev_anchor=_has_prev_anchor(locked_strategy),
    )


def _apply_deterministic_stage2_repair(
    *,
    stage2_slots: Any,
    validation: Stage2ValidationResult,
    locked_strategy: DeterministicGateStrategy,
    entity_role_plan: PlannerEntityRolePlan,
    question: str,
    request_id: Optional[str],
    conversation_id: str,
) -> tuple[Any, dict[str, Any]]:
    payload = stage2_slots.model_dump() if hasattr(stage2_slots, "model_dump") else dict(stage2_slots or {})
    filters = dict(payload.get("filters") or {})
    locked_mode = str(getattr(locked_strategy, "mode", "") or "").strip().lower()
    retrieval_query_before = str(payload.get("retrieval_query") or "").strip()
    injected_filters: dict[str, list[str]] = {}
    filter_repairs_allowed = locked_mode != "search"

    if filter_repairs_allowed and validation.missing_people_terms:
        merged = _merge_filter_terms(filters, "participant_researcher_name", list(validation.missing_people_terms))
        if merged:
            injected_filters["participant_researcher_name"] = merged

    if filter_repairs_allowed and validation.missing_org_terms:
        org_filter_key = _resolve_org_filter_key(entity_role_plan)
        merged = _merge_filter_terms(filters, org_filter_key, list(validation.missing_org_terms))
        if merged:
            injected_filters[org_filter_key] = merged

    if filter_repairs_allowed and validation.missing_years:
        merged = _merge_filter_terms(filters, "years", list(validation.missing_years))
        if merged:
            injected_filters["years"] = merged

    semantic_query_terms = _semantic_query_terms_for_repair(
        entity_role_plan=entity_role_plan,
        retrieval_query=retrieval_query_before,
    )
    repair_terms = _normalize_terms(
        [
            *semantic_query_terms,
            *list(validation.missing_must_keep_terms),
            *list(validation.missing_people_terms),
            *list(validation.missing_org_terms),
            *list(validation.missing_years),
        ]
    )
    retrieval_query_after = _append_terms_to_query(retrieval_query_before or question, repair_terms)

    payload["filters"] = filters
    payload["retrieval_query"] = retrieval_query_after or question
    repaired_slots = PlannerStage2Slots.model_validate(payload)
    repaired_slots = _sanitize_stage2_structured_filters(
        slots=repaired_slots,
        entity_role_plan=entity_role_plan,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    repaired_payload = repaired_slots.model_dump() if hasattr(repaired_slots, "model_dump") else dict(repaired_slots or {})
    changed = repaired_payload != (stage2_slots.model_dump() if hasattr(stage2_slots, "model_dump") else dict(stage2_slots or {}))
    metadata = {
        "applied": changed,
        "prompt_miss_terms": list(validation.missing_must_keep_terms),
        "injected_filters": injected_filters,
        "query_terms": repair_terms,
        "retrieval_query_before": retrieval_query_before,
        "retrieval_query_after": str(repaired_payload.get("retrieval_query") or "").strip(),
        "filter_repairs_allowed": filter_repairs_allowed,
    }
    if changed:
        log_event(
            "PLANNER.STAGE2.DETERMINISTIC_REPAIR",
            request_id=request_id,
            conversation_id=conversation_id,
            reason_code="deterministic_repair_applied",
            prompt_miss_terms=metadata["prompt_miss_terms"],
            injected_filter_keys=sorted(injected_filters.keys()),
            query_terms=repair_terms,
            filter_repairs_allowed=int(filter_repairs_allowed),
            retrieval_query_before=retrieval_query_before,
            retrieval_query_after=metadata["retrieval_query_after"],
        )
    return repaired_slots, metadata


def _sanitize_stage2_structured_filters(
    *,
    slots: Any,
    entity_role_plan: PlannerEntityRolePlan,
    request_id: Optional[str],
    conversation_id: str,
) -> Any:
    payload = slots.model_dump() if hasattr(slots, "model_dump") else dict(slots or {})
    filters = dict(payload.get("filters") or {})
    retrieval_query = str(payload.get("retrieval_query") or "").strip()

    allowed_people_terms = list(getattr(entity_role_plan, "people_terms_to_keep", []) or [])
    allowed_org_terms = list(getattr(entity_role_plan, "org_terms_to_keep", []) or [])
    org_role_hint = str(getattr(entity_role_plan, "org_role_hint", "") or "").strip().lower()
    dropped: dict[str, list[str]] = {}

    if not allowed_people_terms:
        for key in _RESEARCHER_FILTER_KEYS:
            values = _normalize_terms(filters.pop(key, None))
            if values:
                dropped[key] = values

    if not allowed_org_terms:
        for key in (*_GENERIC_ORG_FILTER_KEYS, *_ROLE_SCOPED_ORG_FILTER_KEYS):
            values = _normalize_terms(filters.pop(key, None))
            if values:
                dropped[key] = values
    elif org_role_hint not in {"lead_org", "participant_org", "affiliation_org"}:
        for key in _ROLE_SCOPED_ORG_FILTER_KEYS:
            values = _normalize_terms(filters.pop(key, None))
            if values:
                dropped[key] = values
    elif org_role_hint == "lead_org":
        for key in ("participant_org_name", "people_affiliation_org_name"):
            values = _normalize_terms(filters.pop(key, None))
            if values:
                dropped[key] = values
    elif org_role_hint == "participant_org":
        for key in ("lead_org_name", "performing_org_name", "people_affiliation_org_name"):
            values = _normalize_terms(filters.pop(key, None))
            if values:
                dropped[key] = values
    elif org_role_hint == "affiliation_org":
        for key in ("lead_org_name", "performing_org_name", "participant_org_name"):
            values = _normalize_terms(filters.pop(key, None))
            if values:
                dropped[key] = values

    if _query_mentions_project_axis(retrieval_query) and not _query_has_explicit_org_cue(retrieval_query):
        for key in (*_GENERIC_ORG_FILTER_KEYS, *_ROLE_SCOPED_ORG_FILTER_KEYS):
            values = _normalize_terms(filters.get(key))
            if not values or not all(_looks_identifier_like_filter_term(value) for value in values):
                continue
            removed = _normalize_terms(filters.pop(key, None))
            if removed:
                dropped[key] = list(dict.fromkeys([*dropped.get(key, []), *removed]))

    salvaged_terms = [term for values in dropped.values() for term in values]
    if salvaged_terms:
        payload["retrieval_query"] = _append_terms_to_query(retrieval_query, salvaged_terms)
        log_event(
            "PLANNER.STAGE2.FILTERS.SANITIZED",
            request_id=request_id,
            conversation_id=conversation_id,
            dropped_fields=sorted(key for key, values in dropped.items() if values),
            salvaged_terms=salvaged_terms,
        )

    payload["filters"] = {key: value for key, value in filters.items() if value not in (None, [], {}, "")}
    return PlannerStage2Slots.model_validate(payload)


def _has_explicit_perf_seed(ids_map: dict[str, list[str]]) -> bool:
    return any(ids_map.get(key) for key in ("rst_id", "doi", "issn", "perf_id", "paper_id", "patent_reg_no", "patent_app_no"))


def _enforce_runtime_legality(
    *,
    normalized_intent: Any,
    locked_strategy: DeterministicGateStrategy,
    stage2: Any,
) -> None:
    def _raise(error_code: str, reason: str) -> None:
        raise StrategyViolation(error_code=error_code, reason=reason)

    ids_map = dict(getattr(stage2, "ids_map", None) or {})
    people_terms = list(getattr(normalized_intent, "people_terms", None) or [])
    org_terms = list(getattr(normalized_intent, "org_terms", None) or [])
    broad_people_org_query = bool(people_terms or org_terms)
    has_anchor_seed = bool(
        dict(getattr(locked_strategy, "prev_context_seed", None) or {})
        or dict(getattr(locked_strategy, "gate_seed_map", None) or {})
    )

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
    cards: dict[str, str],
) -> Any:
    llm = build_llm(model_name="solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=PlannerStage1Decision)
    history_str = "\n".join([f"{type(m).__name__}: {m.content}" for m in chat_history[-4:]])
    prev_context_text = _planner_prev_context_text(
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        normalized_intent=normalized_intent,
        display_snapshot=display_snapshot,
    )
    system_prompt = _render_prompt_template(
        await load_prompt_file(planner_prompt_path(f"planner_stage1_{PLANNER_STAGE1_PROMPT_VERSION}.md")),
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
        disable_thinking=PLANNER_DISABLE_THINKING,
        temperature=PLANNER_TEMPERATURE,
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
        planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
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
) -> PlannerEntityRolePlan:
    llm = build_llm(model_name="solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=PlannerEntityRolePlan)
    system_prompt = _render_prompt_template(
        await load_prompt_file(planner_prompt_path(f"planner_stage15_{PLANNER_STAGE15_PROMPT_VERSION}.md")),
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
        disable_thinking=PLANNER_DISABLE_THINKING,
        temperature=PLANNER_TEMPERATURE,
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
        planner_stage15_prompt_version=PLANNER_STAGE15_PROMPT_VERSION,
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
        allowed_keys=PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS,
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
    signals: SurfaceSignals,
    entity_role_plan: PlannerEntityRolePlan,
    hard_contract: HardContractV1,
    soft_strategy_hints: SoftStrategyHintsV1,
    cards: dict[str, str],
    validation_hints: dict[str, Any] | None = None,
    previous_output: dict[str, Any] | None = None,
) -> Any:
    llm = build_llm(model_name="solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=PlannerStage2Slots)
    system_prompt = _render_prompt_template(
        await load_prompt_file(planner_prompt_path(f"planner_stage2_{PLANNER_STAGE2_PROMPT_VERSION}.md")),
        **cards,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
            (
                "human",
                "{format_instructions}\n<locked_strategy>{locked_strategy}</locked_strategy>\n<hard_contract>{hard_contract}</hard_contract>\n<soft_strategy_hints>{soft_strategy_hints}</soft_strategy_hints>\n<surface_signals>{surface_signals}</surface_signals>\n<entity_role_plan>{entity_role_plan}</entity_role_plan>\n<validation_hints>{validation_hints}</validation_hints>\n<previous_output>{previous_output}</previous_output>\n<user_query>{question}</user_query>",
            ),
        ]
    )
    planner_llm = llm.bind(
        reasoning_effort="low",
        include_reasoning=False,
        disable_thinking=PLANNER_DISABLE_THINKING,
        temperature=PLANNER_TEMPERATURE,
        top_p=1.0,
        max_tokens=300,
    )
    chain = prompt | planner_llm | sanitize_llm_json
    raw_slots = await chain.ainvoke(
        {
            "format_instructions": parser.get_format_instructions(),
            "question": question,
            "locked_strategy": json.dumps(locked_strategy.to_prompt_payload(), ensure_ascii=False),
            "hard_contract": json.dumps(hard_contract.model_dump(), ensure_ascii=False),
            "soft_strategy_hints": json.dumps(soft_strategy_hints.model_dump(), ensure_ascii=False),
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
    )
    slots = parser.parse(json.dumps(normalized_slots, ensure_ascii=False))
    slots = _sanitize_stage2_structured_filters(
        slots=slots,
        entity_role_plan=entity_role_plan,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    log_event(
        "PLANNER.STAGE2",
        request_id=request_id,
        conversation_id=conversation_id,
        confidence=round(slots.confidence, 3),
        planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
        retrieval_query=str(getattr(slots, "retrieval_query", "") or ""),
        filters=dict(getattr(slots, "filters", {}) or {}),
        limit=getattr(slots, "limit", None),
        display_limit=getattr(slots, "display_limit", None),
        hard_contract_project_id_label=int(bool(hard_contract.explicit_project_id_label)),
        hard_contract_project_no_label=int(bool(hard_contract.explicit_project_no_label)),
        hard_contract_ambiguous_project_key=int(bool(hard_contract.ambiguous_project_key_label)),
        hard_contract_unsupported_project_key_alias_count=len(hard_contract.unsupported_project_key_aliases),
        soft_hint_semantic_kind=soft_strategy_hints.semantic_kind,
        soft_hint_must_keep_term_count=len(soft_strategy_hints.must_keep_terms),
        soft_hint_has_prev_anchor=int(bool(soft_strategy_hints.has_prev_anchor)),
    )
    return slots


def assemble_question_analysis(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    stage1: Any,
    stage2: Any,
    signals: SurfaceSignals,
    entity_role_plan: PlannerEntityRolePlan,
    locked_strategy: DeterministicGateStrategy,
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
        schema_version=PLANNER_RUNTIME_SCHEMA_VERSION,
        locked=locked_strategy,
        slots={
            "ids_map": ids_map,
            "candidate_keys": candidate_keys,
            "project_key_policy": getattr(stage2, "project_key_policy", None),
            "join_resolution_policy": getattr(stage2, "join_resolution_policy", None),
            "filters": stage2.filters,
            "limit": min(stage2.limit, PLANNER_RUNTIME_MAX_TOP_K_SIZE),
            "display_limit": min(getattr(stage2, "display_limit", stage2.limit), min(stage2.limit, PLANNER_RUNTIME_MAX_TOP_K_SIZE)),
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
    hard_contract = _build_hard_contract(
        question=question,
        locked_strategy=locked_strategy,
        entity_role_plan=entity_role_plan,
        ids_map=dict(payload.get("ids_map") or {}),
        candidate_keys=dict(payload.get("candidate_keys") or {}),
        project_key_policy=payload.get("project_key_policy"),
        join_key_mode=payload.get("join_key_mode"),
    )
    soft_strategy_hints = _build_soft_strategy_hints(
        signals=signals,
        entity_role_plan=entity_role_plan,
        locked_strategy=locked_strategy,
        candidate_keys=dict(payload.get("candidate_keys") or {}),
    )
    payload["hard_contract"] = hard_contract.model_dump()
    payload["soft_strategy_hints"] = soft_strategy_hints.model_dump()
    qa = QuestionAnalysis.model_validate(payload)
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
        planner_stagewise_enabled=int(PLANNER_STAGEWISE_ENABLED),
        planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
        planner_stage15_prompt_version=PLANNER_STAGE15_PROMPT_VERSION,
        planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
        resolved_project_key_axis=qa.hard_contract.resolved_project_key_axis,
        unsupported_project_key_alias_count=len(qa.hard_contract.unsupported_project_key_aliases),
        candidate_project_key_count=qa.hard_contract.candidate_project_key_count,
        project_key_axis_locked=int(bool(qa.hard_contract.project_key_axis_locked)),
        soft_hint_semantic_kind=qa.soft_strategy_hints.semantic_kind,
        soft_hint_has_prev_anchor=int(bool(qa.soft_strategy_hints.has_prev_anchor)),
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
) -> Any:
    display_snapshot = getattr(view_state, "visible_answer_manifest", None)
    focus_entity = get_active_subject_entity(view_state)
    stage1_prompt_name = f"planner_stage1_{PLANNER_STAGE1_PROMPT_VERSION}"
    stage15_prompt_name = f"planner_stage15_{PLANNER_STAGE15_PROMPT_VERSION}"
    stage2_prompt_name = f"planner_stage2_{PLANNER_STAGE2_PROMPT_VERSION}"
    stage1_cards = await build_planner_domain_cards(prompt_name=stage1_prompt_name)
    stage15_cards = await build_planner_domain_cards(prompt_name=stage15_prompt_name)
    stage2_cards = await build_planner_domain_cards(prompt_name=stage2_prompt_name)
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
        cards=stage1_cards,
    )
    locked_strategy = determine_locked_strategy(
        question=question,
        stage1=stage1,
        normalized_intent=normalized_intent,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        display_snapshot=display_snapshot,
        focus_entity=focus_entity,
    )
    stage15 = await run_planner_stage15(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        locked_strategy=locked_strategy,
        signals=signals,
        cards=stage15_cards,
    )
    stage2_hard_contract = _build_hard_contract(
        question=question,
        locked_strategy=locked_strategy,
        entity_role_plan=stage15,
        ids_map=dict(getattr(normalized_intent, "ids_map", None) or {}) or dict(getattr(locked_strategy, "gate_seed_map", None) or {}),
        candidate_keys={},
        project_key_policy=None,
        join_key_mode=getattr(locked_strategy, "join_key_mode", None),
    )
    stage2_soft_strategy_hints = _build_soft_strategy_hints(
        signals=signals,
        entity_role_plan=stage15,
        locked_strategy=locked_strategy,
        candidate_keys={},
    )
    stage2 = await run_planner_stage2(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        locked_strategy=locked_strategy,
        signals=signals,
        entity_role_plan=stage15,
        hard_contract=stage2_hard_contract,
        soft_strategy_hints=stage2_soft_strategy_hints,
        cards=stage2_cards,
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
            reason_code="prompt_miss",
            errors=validation.errors,
            missing_must_keep_terms=validation.missing_must_keep_terms,
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
            hard_contract=stage2_hard_contract,
            soft_strategy_hints=stage2_soft_strategy_hints,
            cards=stage2_cards,
            validation_hints=_validation_hints_payload(validation),
            previous_output=previous_output,
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
                reason_code="prompt_miss",
                errors=validation.errors,
                missing_must_keep_terms=validation.missing_must_keep_terms,
                missing_people_terms=validation.missing_people_terms,
                missing_org_terms=validation.missing_org_terms,
                missing_years=validation.missing_years,
                missing_perf_types=validation.missing_perf_types,
            )
            repaired_stage2, repair_meta = _apply_deterministic_stage2_repair(
                stage2_slots=stage2,
                validation=validation,
                locked_strategy=locked_strategy,
                entity_role_plan=stage15,
                question=question,
                request_id=request_id,
                conversation_id=conversation_id,
            )
            repaired_validation = validate_stage2_slots(
                question=question,
                signals=signals,
                entity_role_plan=stage15,
                locked_strategy=locked_strategy,
                stage2_slots=repaired_stage2,
            )
            if repaired_validation.ok:
                stage2 = repaired_stage2
            else:
                explicit_count = signals.explicit_count or 20
                fallback_limit = max(1, min(int(explicit_count), PLANNER_RUNTIME_MAX_TOP_K_SIZE))
                fallback_payload = repaired_stage2.model_dump() if hasattr(repaired_stage2, "model_dump") else {}
                retrieval_query_before_fallback = str(fallback_payload.get("retrieval_query") or "").strip()
                fallback_payload["retrieval_query"] = question
                fallback_payload["limit"] = max(1, min(int(fallback_payload.get("limit") or fallback_limit), PLANNER_RUNTIME_MAX_TOP_K_SIZE))
                fallback_payload["display_limit"] = max(1, min(int(fallback_payload.get("display_limit") or fallback_payload["limit"]), fallback_payload["limit"]))
                fallback_payload["confidence"] = float(fallback_payload.get("confidence") or 0.0)
                log_event(
                    "PLANNER.STAGE2.RAW_QUERY_FALLBACK",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    reason_code="raw_query_fallback_applied",
                    errors=repaired_validation.errors,
                    missing_must_keep_terms=repaired_validation.missing_must_keep_terms,
                    injected_filter_keys=sorted((repair_meta.get("injected_filters") or {}).keys()),
                    retrieval_query_before=retrieval_query_before_fallback,
                    retrieval_query_after=question,
                )
                stage2 = PlannerStage2Slots.model_validate(fallback_payload)
    _enforce_runtime_legality(
        normalized_intent=normalized_intent,
        locked_strategy=locked_strategy,
        stage2=stage2,
    )
    locked_strategy = regate_locked_strategy(
        request_id=request_id,
        conversation_id=conversation_id,
        stage1=stage1,
        stage2=stage2,
        locked_strategy=locked_strategy,
        allowed_keys=PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS,
        log_event=log_event,
    )
    return assemble_question_analysis(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        stage1=stage1,
        stage2=stage2,
        signals=signals,
        entity_role_plan=stage15,
        locked_strategy=locked_strategy,
    )


