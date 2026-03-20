"""Planner merge helpers shared by runtime code and tests.

Keeping planner merge behavior here prevents the app entry module from becoming the source of truth
for strategy mutation rules.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional


def _planner_truthy_flag(value: Any) -> bool:
    """Normalize planner-emitted truthy flags without re-parsing the user query."""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def normalize_hint_terms(values: Any) -> list[str]:
    """Normalize planner hint values into a stable string list."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if not normalized or normalized.lower() in ("none", "null") or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def collect_researcher_name_terms(filters: dict[str, Any]) -> list[str]:
    """Collect only researcher-name-like filter values as `people_terms` seeds."""
    if not isinstance(filters, dict):
        return []

    researcher_keys = (
        "participant_researcher_name",
        "participant_researcher_names",
        "participant_researcher",
        "participant_researchers",
        "researcher_name",
        "researcher_names",
        "researcher",
        "people_name",
    )

    terms: list[str] = []
    for key in researcher_keys:
        terms.extend(normalize_hint_terms(filters.get(key)))
    return normalize_hint_terms(terms)


def merge_planner_hints(
    intent: Any,
    qa: Any,
    *,
    normalize_org_terms: Any,
    normalize_hint_terms: Any,
    collect_researcher_name_terms: Any,
) -> Any:
    """Merge non-strategy planner hints into the existing intent."""
    if qa is None:
        return intent

    confidence = float(getattr(qa, "confidence", 0.0) or 0.0)
    if confidence < 0.2:
        return intent

    filters = dict(getattr(qa, "filters", {}) or {})
    lead_org_terms = normalize_org_terms(filters.get("lead_org_name") or filters.get("performing_org_name"))
    participant_org_terms = normalize_org_terms(filters.get("participant_org_name"))
    people_affiliation_org_terms = normalize_org_terms(filters.get("people_affiliation_org_name"))
    org_terms = normalize_org_terms([
        *lead_org_terms,
        *participant_org_terms,
        *people_affiliation_org_terms,
        *(filters.get("org_name") or [] if isinstance(filters.get("org_name"), list) else [filters.get("org_name")] if filters.get("org_name") else []),
    ])

    planner_year_from = str(filters.get("year_from") or "").strip() or None
    planner_year_to = str(filters.get("year_to") or "").strip() or None
    planner_years = normalize_hint_terms(filters.get("years"))
    if not planner_year_from and planner_years:
        planner_year_from = planner_years[0]
    if not planner_year_to and planner_years:
        planner_year_to = planner_years[-1]

    planner_perf_types = normalize_hint_terms(filters.get("perf_types") or filters.get("performance_types"))
    planner_title_terms = normalize_hint_terms(filters.get("title_terms") or filters.get("title") or filters.get("name"))
    planner_keywords = normalize_hint_terms(filters.get("keywords"))
    planner_people_terms = collect_researcher_name_terms(filters)
    planner_org_role = str(filters.get("org_role") or getattr(intent, "org_role", "") or "").strip().lower() or None
    planner_reverse_trace_followup = _planner_truthy_flag(filters.get("reverse_trace_followup")) or bool(getattr(intent, "reverse_trace_followup", False))
    planner_followup_relation_hint = str(filters.get("followup_relation_hint") or getattr(intent, "followup_relation_hint", "") or "").strip().lower() or None
    planner_pattern_kind = str(filters.get("pattern_kind") or getattr(intent, "pattern_kind", "") or "").strip().lower() or None
    planner_bundle_kind = str(filters.get("bundle_mode") or filters.get("bundle_kind") or getattr(intent, "bundle_kind", "") or "").strip().lower() or None
    planner_bundle_targets = normalize_hint_terms(filters.get("bundle_targets") or getattr(intent, "bundle_targets", []) or [])
    planner_guidance_required = _planner_truthy_flag(filters.get("guidance_required")) or bool(getattr(intent, "guidance_required", False))

    if planner_org_role == "affiliation" and (people_affiliation_org_terms or org_terms) and not planner_people_terms:
        planner_people_terms = []

    return replace(
        intent,
        planner_limit=int(getattr(qa, "limit", 20) or 20),
        retrieval_query=getattr(qa, "retrieval_query", None),
        planner_confidence=confidence,
        org_role=planner_org_role,
        org_terms=org_terms or list(getattr(intent, "org_terms", []) or []),
        people_terms=planner_people_terms if planner_people_terms else list(getattr(intent, "people_terms", []) or []),
        lead_org_terms=lead_org_terms or list(getattr(intent, "lead_org_terms", []) or []),
        participant_org_terms=participant_org_terms or list(getattr(intent, "participant_org_terms", []) or []),
        people_affiliation_org_terms=people_affiliation_org_terms or list(getattr(intent, "people_affiliation_org_terms", []) or []),
        year_from=planner_year_from or getattr(intent, "year_from", None),
        year_to=planner_year_to or getattr(intent, "year_to", None),
        years=planner_years or list(getattr(intent, "years", []) or []),
        perf_types=planner_perf_types or list(getattr(intent, "perf_types", []) or []),
        keywords=planner_keywords or list(getattr(intent, "keywords", []) or []),
        title=planner_title_terms or list(getattr(intent, "title", []) or []),
        reverse_trace_followup=bool(planner_reverse_trace_followup),
        followup_relation_hint=planner_followup_relation_hint or getattr(intent, "followup_relation_hint", None),
        pattern_kind=planner_pattern_kind or getattr(intent, "pattern_kind", None),
        bundle_kind=planner_bundle_kind or getattr(intent, "bundle_kind", None),
        bundle_targets=planner_bundle_targets or list(getattr(intent, "bundle_targets", []) or []),
        guidance_required=bool(planner_guidance_required),
    )


def apply_planner_strategy(
    intent: Any,
    qa: Any,
    *,
    request_id: Optional[str],
    conversation_id: Optional[str],
    normalize_hint_terms: Any,
    log_event: Any,
    build_changed_fields: Any,
    changed_by_planner_merge: str,
    strategy_violation_cls: type[Exception],
) -> tuple[Any, bool]:
    """Apply planner-fixed strategy fields to the intent."""
    if qa is None:
        return intent, False

    action_mode_map = {
        "topic": "search",
        "list": "lookup",
        "detail": "lookup",
        "stats": "lookup",
        "download": "lookup",
        "id_exact": "lookup",
        "id_fuzzy": "lookup",
        "join": "join",
    }
    tracked_fields = ("mode", "base_route", "action", "relation", "join_key_mode", "target_cols", "ids_map", "candidate_keys", "project_key_policy", "join_resolution_policy")
    strategy_fields = ("mode", "base_route", "action", "relation", "join_key_mode", "target_cols", "project_key_policy", "join_resolution_policy")
    filter_fields = ("ids_map",)
    before_snapshot = {k: getattr(intent, k, None) for k in tracked_fields}

    confidence = float(getattr(qa, "confidence", 0.0) or 0.0)
    if confidence < 0.2:
        return intent, False

    planner_source = str(getattr(qa, "planner_source", "") or "").strip().lower() or None
    planner_action = str(getattr(qa, "action", "") or "").strip().lower()
    planner_mode = str(getattr(qa, "mode", getattr(intent, "mode", "")) or getattr(intent, "mode", "")).strip().lower() or None
    expected_mode = action_mode_map.get(planner_action)
    relation_raw = getattr(qa, "relation", None)
    if isinstance(relation_raw, (tuple, list)):
        has_join_relation = len(relation_raw) >= 2 and bool(str(relation_raw[0]).strip()) and bool(str(relation_raw[1]).strip())
    else:
        has_join_relation = bool(str(relation_raw or "").strip())

    if expected_mode and planner_mode and planner_mode != expected_mode:
        if not (planner_mode == "join" and has_join_relation):
            mismatch_reason = f"planner action/mode mismatch(action={planner_action}, mode={planner_mode}, expected_mode={expected_mode})"
            log_event(
                "RAG.STRATEGY.ACTION_MODE_MISMATCH",
                request_id=request_id,
                conversation_id=conversation_id,
                planner_source=planner_source,
                planner_action=planner_action,
                original_mode=planner_mode,
                expected_mode=expected_mode,
                error_code="PLANNER_ACTION_MODE_MISMATCH",
                reason=mismatch_reason,
            )
            raise strategy_violation_cls(error_code="PLANNER_ACTION_MODE_MISMATCH", reason=mismatch_reason)

    relation_map = {
        "project_perf": ("project", "perf"),
        "perf_project": ("perf", "project"),
    }
    relation = relation_map.get(getattr(qa, "relation", None), getattr(intent, "relation", None))

    def _merge_ids_map(base_ids: Any, planner_ids: Any) -> dict[str, list[str]]:
        """Merge the existing ids_map with planner-emitted ids_map values."""
        merged: dict[str, list[str]] = {}

        def _ingest(source: Any, *, overwrite: bool = False) -> None:
            if not isinstance(source, dict):
                return
            for key, raw_values in source.items():
                values = normalize_hint_terms(raw_values)
                if not values:
                    continue
                if overwrite or key not in merged:
                    merged[key] = list(values)
                else:
                    merged[key] = normalize_hint_terms([*merged[key], *values])

        _ingest(base_ids)
        _ingest(planner_ids, overwrite=True)
        return merged

    planner_target_cols = normalize_hint_terms(getattr(qa, "target_cols", None))
    planner_head = str(getattr(qa, "head", getattr(intent, "base_route", "project")) or getattr(intent, "base_route", "project")).strip().lower()
    if planner_mode == "join" and relation:
        planner_head = str(relation[1]).strip().lower()
    planner_output_type = str(getattr(qa, "output_type", "") or "").strip().lower() or None
    if planner_output_type not in {"summary", "list", "detail", "stats", "relation", "comparison", "series"}:
        qa_action = str(getattr(qa, "action", "") or "").strip().lower()
        if qa_action == "detail":
            planner_output_type = "detail"
        elif qa_action == "stats":
            planner_output_type = "stats"
        elif qa_action == "list":
            planner_output_type = "list"
        elif relation:
            planner_output_type = "relation"
        else:
            planner_output_type = getattr(intent, "output_type", None)

    merged_ids_map = _merge_ids_map(getattr(intent, "ids_map", {}) or {}, getattr(qa, "ids_map", {}) or {})
    merged_candidate_keys = dict(getattr(intent, "candidate_keys", {}) or {})
    merged_candidate_keys.update(dict(getattr(qa, "candidate_keys", {}) or {}))
    planner_join_key_mode = str(getattr(qa, "join_key_mode", "") or "").strip().lower() or None
    planner_project_key_policy = str(getattr(qa, "project_key_policy", "") or "").strip().lower() or None
    planner_join_resolution_policy = str(getattr(qa, "join_resolution_policy", "") or "").strip().lower() or None

    if planner_mode == "join":
        join_relation = relation if isinstance(relation, (list, tuple)) else None
        has_relation = bool(join_relation and len(join_relation) >= 2 and all(str(v or "").strip() for v in join_relation[:2]))
        if not has_relation or not (planner_join_key_mode or planner_project_key_policy == "ambiguous_or"):
            reason = (
                "planner join strategy requires non-empty relation and join_key_mode"
                f"(mode={planner_mode}, relation={relation}, join_key_mode={planner_join_key_mode or None})"
            )
            log_event(
                "RAG.STRATEGY.JOIN_FIELDS_MISSING",
                request_id=request_id,
                conversation_id=conversation_id,
                planner_source=planner_source,
                error_code="PLANNER_JOIN_FIELDS_MISSING",
                reason=reason,
                policy_mode="strict",
            )
            raise strategy_violation_cls(error_code="PLANNER_JOIN_FIELDS_MISSING", reason=reason)

        has_instance_seed = bool(normalize_hint_terms(merged_ids_map.get("pjt_id")))
        has_candidate_project_key = bool((merged_candidate_keys.get("project_key") or []))
        qa_filters = dict(getattr(qa, "filters", {}) or {})
        people_gate_terms = normalize_hint_terms(getattr(intent, "people_terms", None))
        if not people_gate_terms:
            people_gate_terms = collect_researcher_name_terms(qa_filters)
        org_gate_terms = normalize_hint_terms(getattr(intent, "org_terms", None))
        if not org_gate_terms:
            org_gate_terms = normalize_hint_terms([
                qa_filters.get("lead_org_name"),
                qa_filters.get("performing_org_name"),
                qa_filters.get("participant_org_name"),
                qa_filters.get("people_affiliation_org_name"),
                qa_filters.get("org_name"),
            ])
        planner_org_role = str(qa_filters.get("org_role") or getattr(intent, "org_role", "") or "").strip().lower() or None
        lead_org_gate_terms = normalize_hint_terms(getattr(intent, "lead_org_terms", None) or [qa_filters.get("lead_org_name"), qa_filters.get("performing_org_name")])
        participant_org_gate_terms = normalize_hint_terms(getattr(intent, "participant_org_terms", None) or qa_filters.get("participant_org_name"))
        affiliation_org_gate_terms = normalize_hint_terms(getattr(intent, "people_affiliation_org_terms", None) or qa_filters.get("people_affiliation_org_name"))
        has_people_org_gate = bool(people_gate_terms or org_gate_terms)
        unresolved_anchor_pair = bool(people_gate_terms and org_gate_terms and not (planner_org_role or lead_org_gate_terms or participant_org_gate_terms or affiliation_org_gate_terms))

        if planner_project_key_policy == "ambiguous_or" and has_candidate_project_key and not planner_join_key_mode:
            planner_join_key_mode = "deferred"
            planner_join_resolution_policy = planner_join_resolution_policy or "auto_resolve"

        if planner_join_key_mode == "instance" and not has_instance_seed and (not has_people_org_gate or unresolved_anchor_pair):
            downgraded_mode = str(getattr(intent, "mode", "") or "").strip().lower() or "lookup"
            if downgraded_mode == "join":
                downgraded_mode = "lookup"
            log_event(
                "RAG.STRATEGY.JOIN_DOWNGRADED",
                request_id=request_id,
                conversation_id=conversation_id,
                planner_source=planner_source,
                original_mode=planner_mode,
                downgraded_mode=downgraded_mode,
                relation=relation,
                original_join_key_mode=planner_join_key_mode,
                reason=("seedless_instance_join_with_unresolved_anchor_pair" if unresolved_anchor_pair else "seedless_instance_join_without_gate"),
                has_instance_seed=0,
                has_people_org_gate=int(has_people_org_gate),
                unresolved_anchor_pair=int(unresolved_anchor_pair),
            )
            planner_mode = downgraded_mode
            planner_join_key_mode = None

    patched = replace(
        intent,
        base_route=planner_head,
        action=str(getattr(qa, "action", getattr(intent, "action", "topic")) or getattr(intent, "action", "topic")).strip().lower(),
        mode=planner_mode,
        relation=relation,
        join_key_mode=planner_join_key_mode,
        candidate_keys=merged_candidate_keys,
        project_key_policy=planner_project_key_policy,
        join_resolution_policy=planner_join_resolution_policy,
        target_cols=planner_target_cols or list(getattr(intent, "target_cols", []) or []),
        ids_map=merged_ids_map,
        output_type=planner_output_type,
    )

    after_snapshot = {k: getattr(patched, k, None) for k in tracked_fields}
    changed_strategy_fields = build_changed_fields(before_snapshot, after_snapshot, strategy_fields, changed_by=changed_by_planner_merge)
    changed_filter_fields = build_changed_fields(before_snapshot, after_snapshot, filter_fields, changed_by=changed_by_planner_merge)
    log_event(
        "PLANNER.PIPELINE",
        request_id=request_id,
        conversation_id=conversation_id,
        step="intent_merge",
        status="success",
        applied=int(bool({**changed_strategy_fields, **changed_filter_fields})),
        confidence=round(confidence, 3),
        strategy_mutation_stage="planner_merge",
        changed_by=changed_by_planner_merge,
        changed_strategy_fields=changed_strategy_fields,
        changed_filter_fields=changed_filter_fields,
    )
    return patched, True


def apply_planner_v2(
    intent: Any,
    qa: Any,
    *,
    request_id: Optional[str],
    conversation_id: Optional[str],
    merge_planner_hints: Any,
    normalize_hint_terms: Any,
    apply_planner_strategy_fn: Any,
) -> tuple[Any, bool]:
    """Stagewise planner entry point used by runtime code."""
    hinted_intent = merge_planner_hints(intent, qa)

    return apply_planner_strategy_fn(
        hinted_intent,
        qa,
        request_id=request_id,
        conversation_id=conversation_id,
    )
