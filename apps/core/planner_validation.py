from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_EXPLICIT_PERF_ID_KEYS = ("rst_id", "doi", "issn", "perf_id", "paper_id", "patent_reg_no", "patent_app_no")


@dataclass(frozen=True)
class Stage2ValidationResult:
    ok: bool
    errors: list[str]
    missing_people_terms: list[str]
    missing_org_terms: list[str]
    missing_years: list[str]
    missing_perf_types: list[str]


def _flatten(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        out: list[str] = []
        for key, nested in value.items():
            out.append(str(key))
            out.extend(_flatten(nested))
        return out
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_flatten(item))
        return out
    return [str(value)]


def _string_pool_from_stage2(stage2_slots: Any) -> str:
    filters = getattr(stage2_slots, "filters", {}) or {}
    retrieval_query = str(getattr(stage2_slots, "retrieval_query", "") or "")
    ids_map = getattr(stage2_slots, "ids_map", {}) or {}
    candidate_keys = getattr(stage2_slots, "candidate_keys", {}) or {}
    payload = [retrieval_query]
    payload.extend(_flatten(filters))
    payload.extend(_flatten(ids_map))
    payload.extend(_flatten(candidate_keys))
    return " ".join(part for part in payload if str(part).strip())


def _has_explicit_perf_seed(stage2_slots: Any) -> bool:
    ids_map = getattr(stage2_slots, "ids_map", {}) or {}
    return any(ids_map.get(key) for key in _EXPLICIT_PERF_ID_KEYS)


def _locked_field(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def validate_stage2_slots(
    *,
    question: str,
    signals: Any,
    entity_role_plan: Any,
    locked_strategy: Any,
    stage2_slots: Any,
) -> Stage2ValidationResult:
    haystack = _string_pool_from_stage2(stage2_slots)

    missing_people = [value for value in getattr(entity_role_plan, "people_terms_to_keep", []) if value and value not in haystack]
    missing_orgs = [value for value in getattr(entity_role_plan, "org_terms_to_keep", []) if value and value not in haystack]
    missing_years = [value for value in getattr(signals, "years", []) if value and value not in haystack]
    missing_perf = [value for value in getattr(entity_role_plan, "perf_type_hints", []) if value and value not in haystack]
    missing_must_keep = [value for value in getattr(entity_role_plan, "must_keep_terms", []) if value and value not in haystack]

    errors: list[str] = []
    if missing_must_keep:
        errors.append("missing_must_keep_terms")
    if missing_people:
        errors.append("missing_people_terms")
    if missing_orgs:
        errors.append("missing_org_terms")
    if missing_years:
        errors.append("missing_years")
    if missing_perf:
        errors.append("missing_perf_types")

    locked_mode = str(_locked_field(locked_strategy, "mode") or "").strip().lower()
    locked_head = str(_locked_field(locked_strategy, "head") or "").strip().lower()
    locked_action = str(_locked_field(locked_strategy, "action") or "").strip().lower()
    locked_relation = str(_locked_field(locked_strategy, "relation") or "").strip().lower() or None
    locked_join_key_mode = str(_locked_field(locked_strategy, "join_key_mode") or "").strip().lower() or None
    locked_prev_context_seed = _locked_field(locked_strategy, "prev_context_seed") or {}

    if locked_mode == "join" and not locked_join_key_mode:
        errors.append("join_without_join_key_mode")
    if getattr(entity_role_plan, "anchor_required", False) and locked_relation and not locked_prev_context_seed:
        errors.append("join_without_anchor")

    people_terms = list(getattr(signals, "people_terms", []) or [])
    org_terms = list(getattr(signals, "org_terms", []) or [])
    broad_people_org_query = bool(people_terms or org_terms) and not _has_explicit_perf_seed(stage2_slots) and not getattr(entity_role_plan, "anchor_required", False)
    if broad_people_org_query and locked_head == "perf" and locked_action == "detail":
        errors.append("broad_query_collapsed_to_perf_detail")
    if locked_head == "perf" and locked_action == "detail" and not _has_explicit_perf_seed(stage2_slots):
        errors.append("perf_detail_without_explicit_perf_id")

    return Stage2ValidationResult(
        ok=not errors,
        errors=errors,
        missing_people_terms=missing_people,
        missing_org_terms=missing_orgs,
        missing_years=missing_years,
        missing_perf_types=missing_perf,
    )
