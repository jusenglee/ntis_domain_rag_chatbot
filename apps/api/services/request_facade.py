from __future__ import annotations

from dataclasses import dataclass, is_dataclass, replace
from typing import Any, Dict, List, Optional

from langchain_core.messages import BaseMessage

from apps.api.services.followup_anchor import anchor_to_seed_map, parse_display_limit, parse_ordinal_reference, resolve_followup_anchor
from apps.core.followup_resolution import resolve_reference_context_followup
from apps.core.settings import MAX_TOP_K_SIZE
from apps.api.services.view_state import ConversationViewState

_DISPLAY_LIMIT_SENTINEL = 10**9
_DEFAULT_RETRIEVAL_LIMIT = 20
_LIST_LIKE_OUTPUT_TYPES = {"list", "relation", "comparison", "series", "stats"}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _resolve_followup_locked_route(followup_resolution: dict[str, Any]) -> tuple[str | None, list[str] | None, str | None]:
    if not isinstance(followup_resolution, dict):
        return None, None, None

    status = str(followup_resolution.get("followup_resolution_status") or "").strip().lower()
    if status != "resolved":
        return None, None, None

    selected_prev_item = dict(followup_resolution.get("selected_prev_item") or {})
    focus_entity = dict(followup_resolution.get("focus_entity") or {})
    context_kind = _first_text(
        selected_prev_item.get("context_kind"),
        focus_entity.get("kind"),
        followup_resolution.get("selected_prev_context_kind"),
    ).lower()

    if context_kind == "perf":
        return "perf", ["ntis_perf_v1"], "followup_context_perf"
    if context_kind == "project":
        return "project", ["ntis_project_v1"], "followup_context_project"
    return None, None, None


def _apply_followup_context_lock(normalized_intent: Any, followup_resolution: dict[str, Any]) -> Any:
    locked_base_route, locked_target_cols, _ = _resolve_followup_locked_route(followup_resolution)
    if not locked_base_route or not locked_target_cols:
        return normalized_intent

    if isinstance(normalized_intent, dict):
        patched = dict(normalized_intent)
        patched["base_route"] = locked_base_route
        patched["target_cols"] = list(locked_target_cols)
        return patched

    if is_dataclass(normalized_intent):
        updates: dict[str, Any] = {}
        if hasattr(normalized_intent, "base_route"):
            updates["base_route"] = locked_base_route
        if hasattr(normalized_intent, "target_cols"):
            updates["target_cols"] = list(locked_target_cols)
        return replace(normalized_intent, **updates) if updates else normalized_intent

    if hasattr(normalized_intent, "base_route") or hasattr(normalized_intent, "target_cols"):
        try:
            if hasattr(normalized_intent, "base_route"):
                setattr(normalized_intent, "base_route", locked_base_route)
            if hasattr(normalized_intent, "target_cols"):
                setattr(normalized_intent, "target_cols", list(locked_target_cols))
        except Exception:
            pass
    return normalized_intent


def has_explicit_precheck_signals(precheck: dict[str, Any]) -> bool:
    ids_map = (precheck or {}).get("ids_map")
    if not isinstance(ids_map, dict):
        return False
    for values in ids_map.values():
        if isinstance(values, (list, tuple, set)):
            if any(str(value).strip() for value in values):
                return True
            continue
        if str(values or "").strip():
            return True
    return False


def _has_ids_map_values(ids_map: Any) -> bool:
    if not isinstance(ids_map, dict):
        return False
    for values in ids_map.values():
        if isinstance(values, (list, tuple, set)):
            if any(str(value).strip() for value in values):
                return True
            continue
        if str(values or "").strip():
            return True
    return False


def _merge_seed_into_ids_map(ids_map: Any, seed_map: dict[str, list[str]]) -> dict[str, list[str]]:
    merged = dict(ids_map or {}) if isinstance(ids_map, dict) else {}

    def _normalize(values: Any) -> list[str]:
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

    seed_pjt_ids = _normalize(seed_map.get("pjt_id"))
    seed_pjt_nos = _normalize(seed_map.get("pjt_no"))

    if seed_pjt_ids:
        merged.pop("pjt_no", None)
        merged["pjt_id"] = seed_pjt_ids
    elif seed_pjt_nos:
        merged.pop("pjt_id", None)
        merged["pjt_no"] = seed_pjt_nos

    for key, values in (seed_map or {}).items():
        if key in {"pjt_id", "pjt_no"}:
            continue
        normalized = _normalize(values)
        if not normalized:
            continue
        merged[key] = normalized
    return merged


def _apply_anchor_lock(normalized_intent: Any, seed_map: dict[str, list[str]]) -> Any:
    if not seed_map:
        return normalized_intent

    def _resolve_policy(ids_map: dict[str, list[str]]) -> Optional[str]:
        if ids_map.get("pjt_id"):
            return "anchor_locked_pjt_id"
        if ids_map.get("pjt_no"):
            return "anchor_locked_pjt_no"
        return None

    if isinstance(normalized_intent, dict):
        patched = dict(normalized_intent)
        merged_ids_map = _merge_seed_into_ids_map(patched.get("ids_map") or {}, seed_map)
        patched["ids_map"] = merged_ids_map
        policy = _resolve_policy(merged_ids_map)
        if policy is not None:
            patched["project_key_policy"] = policy
        return patched
    if is_dataclass(normalized_intent):
        merged_ids_map = _merge_seed_into_ids_map(getattr(normalized_intent, "ids_map", {}) or {}, seed_map)
        project_key_policy = getattr(normalized_intent, "project_key_policy", None)
        policy = _resolve_policy(merged_ids_map)
        if policy is not None:
            project_key_policy = policy
        return replace(normalized_intent, ids_map=merged_ids_map, project_key_policy=project_key_policy)
    if hasattr(normalized_intent, "ids_map"):
        try:
            merged_ids_map = _merge_seed_into_ids_map(getattr(normalized_intent, "ids_map", {}) or {}, seed_map)
            setattr(normalized_intent, "ids_map", merged_ids_map)
            policy = _resolve_policy(merged_ids_map)
            if policy is not None and hasattr(normalized_intent, "project_key_policy"):
                setattr(normalized_intent, "project_key_policy", policy)
        except Exception:
            pass
    return normalized_intent


def _get_field(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _replace_fields(source: Any, **updates: Any) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        patched = dict(source)
        patched.update(updates)
        return patched
    if is_dataclass(source):
        return replace(source, **updates)
    for key, value in updates.items():
        try:
            setattr(source, key, value)
        except Exception:
            pass
    return source


def _normalize_org_role_hint(normalized_intent: Any, question: str) -> Optional[str]:
    org_role = str(_get_field(normalized_intent, "org_role", "") or "").strip().lower()
    if org_role in {"performer", "performing"}:
        return "lead"
    if org_role:
        return org_role
    text = str(question or "").strip()
    if any(token in text for token in ("참여기관", "협력기관")):
        return "participant"
    if any(token in text for token in ("수행기관", "주관기관")):
        return "lead"
    if any(token in text for token in ("소속기관", "소속")):
        return "affiliation"
    return None


def _has_project_anchor_seed(normalized_intent: Any) -> bool:
    ids_map = _get_field(normalized_intent, "ids_map", {}) or {}
    if not isinstance(ids_map, dict):
        return False
    return bool(ids_map.get("pjt_id") or ids_map.get("pjt_no"))


def _has_role_scoped_org_request(normalized_intent: Any, question: str) -> bool:
    if _normalize_org_role_hint(normalized_intent, question):
        return True
    for key in ("lead_org_terms", "participant_org_terms", "people_affiliation_org_terms"):
        values = _get_field(normalized_intent, key, None) or []
        if any(str(value or "").strip() for value in values):
            return True
    return False


def _coerce_project_anchor_role_followup(
    normalized_intent: Any,
    question_analysis: Any,
    *,
    question: str,
    followup_resolution: Dict[str, Any],
    log_event: Any,
    request_id: Optional[str],
    conversation_id: str,
) -> tuple[Any, Any]:
    status = str((followup_resolution or {}).get("followup_resolution_status") or "").strip().lower()
    if status != "resolved":
        return normalized_intent, question_analysis
    if not _has_project_anchor_seed(normalized_intent):
        return normalized_intent, question_analysis
    if not _has_role_scoped_org_request(normalized_intent, question):
        return normalized_intent, question_analysis

    role_hint = _normalize_org_role_hint(normalized_intent, question)
    org_terms = list(_get_field(normalized_intent, "org_terms", []) or [])
    lead_terms = list(_get_field(normalized_intent, "lead_org_terms", []) or [])
    participant_terms = list(_get_field(normalized_intent, "participant_org_terms", []) or [])
    affiliation_terms = list(_get_field(normalized_intent, "people_affiliation_org_terms", []) or [])

    intent_updates = {
        "base_route": "project",
        "action": "detail",
        "output_type": "detail",
    }
    if role_hint:
        intent_updates["org_role"] = role_hint
        if role_hint == "lead" and not lead_terms and org_terms:
            intent_updates["lead_org_terms"] = list(org_terms)
        elif role_hint == "participant" and not participant_terms and org_terms:
            intent_updates["participant_org_terms"] = list(org_terms)
        elif role_hint == "affiliation" and not affiliation_terms and org_terms:
            intent_updates["people_affiliation_org_terms"] = list(org_terms)

    normalized_intent = _replace_fields(normalized_intent, **intent_updates)

    if question_analysis is not None:
        qa_updates = {
            "head": "project",
            "action": "detail",
            "output_type": "detail",
            "limit": 1,
            "display_limit": 1,
            "retrieval_query": str(question or "").strip() or _get_field(question_analysis, "retrieval_query", None),
        }
        question_analysis = _replace_fields(question_analysis, **qa_updates)

    log_event(
        "FOLLOWUP.ROLE_DETAIL.COERCED",
        request_id=request_id,
        conversation_id=conversation_id,
        role_kind=role_hint,
        anchor_kind="project",
        has_pjt_id_seed=int(bool((_get_field(normalized_intent, "ids_map", {}) or {}).get("pjt_id"))),
        has_pjt_no_seed=int(bool((_get_field(normalized_intent, "ids_map", {}) or {}).get("pjt_no"))),
    )
    return normalized_intent, question_analysis


def _build_followup_resolution_from_anchor(anchor: Any, snapshot: Any, question: str) -> Dict[str, Any]:
    if anchor is None:
        return {
            "followup_resolution_status": "none",
            "selected_prev_item": None,
            "seed_map": {},
            "seed_source": None,
            "explicit_followup": False,
            "followup_reference_kind": None,
            "requested_token": None,
            "requested_index": None,
            "available_count": len(getattr(snapshot, "items", []) or []),
            "anchor_source": None,
            "focus_entity": None,
        }
    selected_prev_item = None
    if anchor.display_rank is not None:
        selected_prev_item = {
            "index": anchor.display_rank,
            "pjt_id": anchor.pjt_id,
            "pjt_no": anchor.pjt_no,
            "rst_id": anchor.rst_id,
            "person_no": anchor.person_no,
            "org_id": anchor.org_id,
            "org_code": anchor.org_code,
            "biz_no": anchor.biz_no,
            "doi": anchor.doi,
            "issn": anchor.issn,
            "doc_id": anchor.doc_id,
            "doc_type": anchor.doc_type,
            "title": anchor.title_text,
            "context_kind": anchor.kind,
            "view_id": anchor.view_id,
        }
    reference_kind = "deictic" if anchor.source == "display_snapshot" and parse_ordinal_reference(question) is None else "ordinal" if anchor.source == "display_snapshot" else "focus" if anchor.source != "explicit_id" else "explicit_id"
    return {
        "followup_resolution_status": "resolved",
        "selected_prev_item": selected_prev_item,
        "seed_map": anchor_to_seed_map(anchor),
        "seed_source": anchor.source,
        "explicit_followup": anchor.source != "explicit_id",
        "followup_reference_kind": reference_kind,
        "requested_token": None,
        "requested_index": anchor.display_rank,
        "available_count": len(getattr(snapshot, "items", []) or []),
        "anchor_source": anchor.source,
        "focus_entity": anchor.model_dump() if hasattr(anchor, "model_dump") else None,
    }


def _build_strategy_meta(normalized_intent: Any, question_analysis: Any, *, followup_resolution: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if isinstance(normalized_intent, dict):
        ids_map = normalized_intent.get("ids_map") or {}
        candidate_keys = normalized_intent.get("candidate_keys") or {}
        project_key_policy = normalized_intent.get("project_key_policy")
        join_resolution_policy = normalized_intent.get("join_resolution_policy")
        join_key_mode = normalized_intent.get("join_key_mode")
        is_exact_key_query = bool(normalized_intent.get("is_exact_key_query", False))
    else:
        ids_map = getattr(normalized_intent, "ids_map", None) or {}
        candidate_keys = getattr(normalized_intent, "candidate_keys", None) or {}
        project_key_policy = getattr(normalized_intent, "project_key_policy", None)
        join_resolution_policy = getattr(normalized_intent, "join_resolution_policy", None)
        join_key_mode = getattr(normalized_intent, "join_key_mode", None)
        is_exact_key_query = bool(getattr(normalized_intent, "is_exact_key_query", False))
    followup_resolution = dict(followup_resolution or {})
    selected_prev_item = dict(followup_resolution.get("selected_prev_item") or {})
    focus_entity = dict(followup_resolution.get("focus_entity") or {})
    return {
        "strategy_version": str(getattr(question_analysis, "strategy_version", "v3") or "v3"),
        "candidate_keys": dict(candidate_keys),
        "project_key_policy": project_key_policy,
        "anchor_locked": bool(str(project_key_policy or "").strip().lower() in {"anchor_locked_pjt_id", "anchor_locked_pjt_no"}),
        "anchor_locked_key_kind": "pjt_id" if str(project_key_policy or "").strip().lower() == "anchor_locked_pjt_id" else ("pjt_no" if str(project_key_policy or "").strip().lower() == "anchor_locked_pjt_no" else None),
        "join_resolution_policy": join_resolution_policy,
        "join_key_mode": join_key_mode,
        "ids_map": dict(ids_map),
        "has_project_candidate_key": bool((candidate_keys.get("project_key") or [])),
        "has_perf_candidate_key": bool((candidate_keys.get("perf_key") or [])),
        "is_exact_key_query": is_exact_key_query,
        "candidate_project_key_count": len(candidate_keys.get("project_key") or []),
        "candidate_perf_key_count": len(candidate_keys.get("perf_key") or []),
        "selected_prev_item": selected_prev_item or None,
        "selected_prev_context_kind": selected_prev_item.get("context_kind") if selected_prev_item else None,
        "selected_prev_index": selected_prev_item.get("index") if selected_prev_item else None,
        "selected_prev_pjt_id": selected_prev_item.get("pjt_id") if selected_prev_item else None,
        "selected_prev_pjt_no": selected_prev_item.get("pjt_no") if selected_prev_item else None,
        "selected_prev_rst_id": selected_prev_item.get("rst_id") if selected_prev_item else None,
        "selected_prev_person_no": selected_prev_item.get("person_no") if selected_prev_item else None,
        "selected_prev_org_id": selected_prev_item.get("org_id") if selected_prev_item else None,
        "followup_resolution_status": followup_resolution.get("followup_resolution_status") or "none",
        "followup_reference_kind": followup_resolution.get("followup_reference_kind"),
        "explicit_followup": bool(followup_resolution.get("explicit_followup")),
        "requested_token": followup_resolution.get("requested_token"),
        "requested_index": followup_resolution.get("requested_index"),
        "available_count": followup_resolution.get("available_count"),
        "seed_source": followup_resolution.get("seed_source"),
        "anchor_source": followup_resolution.get("anchor_source"),
        "focus_entity_key": focus_entity.get("pjt_id") or focus_entity.get("pjt_no") or focus_entity.get("rst_id") or focus_entity.get("person_no") or focus_entity.get("org_id") or focus_entity.get("org_code") or focus_entity.get("biz_no") or focus_entity.get("doi") or focus_entity.get("issn") or focus_entity.get("doc_id"),
        "focus_entity": focus_entity or None,
        "candidate_items": list(followup_resolution.get("candidate_items") or []),
        "display_view_id": selected_prev_item.get("view_id") if selected_prev_item else focus_entity.get("view_id"),
        "display_rank": focus_entity.get("display_rank"),
    }


def _build_intent_payload_object(intent_payload_cls: Any, normalized_intent: Any, question_analysis: Any, *, followup_resolution: Optional[Dict[str, Any]] = None) -> Any:
    strategy_meta = _build_strategy_meta(normalized_intent, question_analysis, followup_resolution=followup_resolution)
    try:
        return intent_payload_cls(
            intent_payload_version="v3",
            normalized_intent=normalized_intent,
            question_analysis=question_analysis,
            strategy_meta=strategy_meta,
        )
    except TypeError:
        payload = intent_payload_cls(normalized_intent=normalized_intent)
        if hasattr(payload, "intent_payload_version"):
            setattr(payload, "intent_payload_version", "v3")
        if hasattr(payload, "question_analysis"):
            setattr(payload, "question_analysis", question_analysis)
        if hasattr(payload, "strategy_meta"):
            setattr(payload, "strategy_meta", strategy_meta)
        return payload


def _coerce_positive_int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except Exception:
        return None
    return number if number >= 1 else None


def _resolve_question_analysis_count(question_analysis: Any, *, question: str, log_event: Any, request_id: Optional[str], conversation_id: str) -> None:
    """Trust planner counts first and use deterministic parsing only as a fallback."""
    if question_analysis is None:
        return

    action = str(getattr(question_analysis, "action", "") or "").strip().lower()
    output_type = str(getattr(question_analysis, "output_type", "") or "").strip().lower()
    is_list_like = action == "list" or output_type in _LIST_LIKE_OUTPUT_TYPES
    explicit_count = parse_display_limit(question, default=_DISPLAY_LIMIT_SENTINEL)
    has_explicit_count = explicit_count != _DISPLAY_LIMIT_SENTINEL

    planner_limit_raw = getattr(question_analysis, "limit", None)
    planner_display_limit_raw = getattr(question_analysis, "display_limit", None)
    planner_limit = _coerce_positive_int(planner_limit_raw)
    planner_display_limit = _coerce_positive_int(planner_display_limit_raw)
    planner_matches_explicit_count = not (is_list_like and has_explicit_count) or planner_display_limit == explicit_count
    planner_valid = (
        planner_limit is not None
        and planner_display_limit is not None
        and planner_display_limit <= planner_limit
        and planner_matches_explicit_count
    )

    if planner_valid:
        log_event(
            "PLANNER.COUNT_RESOLUTION",
            request_id=request_id,
            conversation_id=conversation_id,
            source="planner",
            action=action or None,
            output_type=output_type or None,
            planner_limit=planner_limit,
            planner_display_limit=planner_display_limit,
            final_limit=planner_limit,
            final_display_limit=planner_display_limit,
            explicit_count=None if not has_explicit_count else explicit_count,
        )
        return

    default_limit = 1 if output_type == "detail" or action == "detail" else _DEFAULT_RETRIEVAL_LIMIT
    fallback_limit = min(planner_limit or default_limit, MAX_TOP_K_SIZE)
    fallback_display_default = planner_display_limit or fallback_limit

    if has_explicit_count and is_list_like:
        clamped_explicit_count = min(explicit_count, MAX_TOP_K_SIZE)
        fallback_limit = min(max(fallback_limit, clamped_explicit_count), MAX_TOP_K_SIZE)
        fallback_display_limit = min(fallback_limit, clamped_explicit_count)
        if planner_limit is not None and planner_display_limit is not None and planner_display_limit <= planner_limit:
            fallback_reason = "planner_explicit_count_mismatch"
        else:
            fallback_reason = "invalid_planner_explicit_count"
    else:
        fallback_display_limit = min(
            fallback_limit,
            min(parse_display_limit(question, default=fallback_display_default), MAX_TOP_K_SIZE),
        )
        fallback_reason = "invalid_planner_no_explicit_count"

    question_analysis.limit = fallback_limit
    question_analysis.display_limit = fallback_display_limit
    log_event(
        "PLANNER.COUNT_RESOLUTION",
        request_id=request_id,
        conversation_id=conversation_id,
        source="planner_fallback",
        reason=fallback_reason,
        action=action or None,
        output_type=output_type or None,
        planner_limit=planner_limit_raw,
        planner_display_limit=planner_display_limit_raw,
        final_limit=fallback_limit,
        final_display_limit=fallback_display_limit,
        explicit_count=None if not has_explicit_count else explicit_count,
    )


@dataclass(frozen=True)
class RequestUnderstandingFacade:
    cheap_precheck: Any
    has_superlative_cue: Any
    extract_years: Any
    extract_perf_types: Any
    extract_title_terms: Any
    classify_query_intent: Any
    normalize_intent: Any
    run_question_analysis: Any
    apply_question_analysis_v3: Any
    log_event: Any
    intent_payload_cls: Any
    planner_stagewise_enabled: bool
    planner_stage1_prompt_version: str
    planner_stage2_prompt_version: str

    def _build_explicit_only_hint(self, question: str) -> Dict[str, Any]:
        return {
            "wants_rank": self.has_superlative_cue(question),
            "years": self.extract_years(question),
            "perf_types": self.extract_perf_types(question),
            "title_terms": self.extract_title_terms(question),
        }

    async def build_intent_payload(
        self,
        *,
        question: str,
        conversation_id: str,
        chat_history: List[BaseMessage],
        prev_context: List[Dict[str, Any]],
        canonical_evidence: Optional[List[Dict[str, Any]]] = None,
        view_state: Optional[ConversationViewState] = None,
        request_id: Optional[str] = None,
    ) -> tuple[Any, Any]:
        precheck = self.cheap_precheck(question)
        explicit_only_hint = self._build_explicit_only_hint(question)
        kws: List[str] = []
        raw_intent = self.classify_query_intent(question, kws, hint=explicit_only_hint)
        normalized_intent_base = self.normalize_intent(
            raw_intent,
            query=question,
            keywords=kws,
            hint_years=list(explicit_only_hint.get("years", [])),
            hint_perf_types=list(explicit_only_hint.get("perf_types", [])),
            hint_title_terms=list(explicit_only_hint.get("title_terms", [])),
        )
        active_view_state = view_state or ConversationViewState()
        latest_snapshot = active_view_state.latest_display_snapshot
        latest_focus_entity = active_view_state.latest_focus_entity
        question_analysis = None
        planner_failed = 0
        base_route = str((normalized_intent_base.get("base_route") if isinstance(normalized_intent_base, dict) else getattr(normalized_intent_base, "base_route", None)) or "project").strip().lower() or "project"
        base_ids_map = (normalized_intent_base.get("ids_map") if isinstance(normalized_intent_base, dict) else getattr(normalized_intent_base, "ids_map", None)) or {}
        anchor = None
        if not has_explicit_precheck_signals(precheck) and not _has_ids_map_values(base_ids_map):
            anchor = resolve_followup_anchor(
                question=question,
                normalized_intent=normalized_intent_base,
                latest_display_snapshot=latest_snapshot,
                latest_focus_entity=latest_focus_entity,
            )
        followup_resolution = _build_followup_resolution_from_anchor(anchor, latest_snapshot, question)
        if anchor is None and not has_explicit_precheck_signals(precheck) and not _has_ids_map_values(base_ids_map):
            followup_resolution = resolve_reference_context_followup(
                question=question,
                canonical_evidence=list(canonical_evidence or []),
                prev_context=prev_context,
                default_context_kind=base_route,
            )
            if str(followup_resolution.get("followup_resolution_status") or "") == "resolved":
                normalized_intent_base = _apply_anchor_lock(normalized_intent_base, followup_resolution.get("seed_map") or {})
                normalized_intent_base = _apply_followup_context_lock(normalized_intent_base, followup_resolution)
                normalized_intent_base, question_analysis = _coerce_project_anchor_role_followup(
                    normalized_intent_base,
                    question_analysis,
                    question=question,
                    followup_resolution=followup_resolution,
                    log_event=self.log_event,
                    request_id=request_id,
                    conversation_id=conversation_id,
                )
        elif anchor is not None:
            seed_map = anchor_to_seed_map(anchor)
            normalized_intent_base = _apply_anchor_lock(normalized_intent_base, seed_map)
            normalized_intent_base = _apply_followup_context_lock(normalized_intent_base, followup_resolution)
            self.log_event(
                "FOLLOWUP.ANCHOR.RESOLVED",
                request_id=request_id,
                conversation_id=conversation_id,
                source=anchor.source,
                view_id=getattr(anchor, "view_id", None),
                requested_ordinal=parse_ordinal_reference(question),
                resolved_display_rank=getattr(anchor, "display_rank", None),
                pjt_id=getattr(anchor, "pjt_id", None),
                pjt_no=getattr(anchor, "pjt_no", None),
                rst_id=getattr(anchor, "rst_id", None),
                person_no=getattr(anchor, "person_no", None),
                org_id=getattr(anchor, "org_id", None),
            )
            normalized_intent_base, question_analysis = _coerce_project_anchor_role_followup(
                normalized_intent_base,
                question_analysis,
                question=question,
                followup_resolution=followup_resolution,
                log_event=self.log_event,
                request_id=request_id,
                conversation_id=conversation_id,
            )
        if not has_explicit_precheck_signals(precheck):
            question_analysis = await self.run_question_analysis(
                question=question,
                conversation_id=conversation_id,
                chat_history=chat_history,
                prev_context=prev_context,
                canonical_evidence=list(canonical_evidence or []),
                view_state=active_view_state,
                request_id=request_id,
                normalized_intent_base=normalized_intent_base,
            )
            planner_failed = int(float(getattr(question_analysis, "confidence", 0.0) or 0.0) <= 0.0)
        if question_analysis is not None:
            normalized_intent_base, question_analysis = _coerce_project_anchor_role_followup(
                normalized_intent_base,
                question_analysis,
                question=question,
                followup_resolution=followup_resolution,
                log_event=self.log_event,
                request_id=request_id,
                conversation_id=conversation_id,
            )
            _resolve_question_analysis_count(
                question_analysis,
                question=question,
                log_event=self.log_event,
                request_id=request_id,
                conversation_id=conversation_id,
            )
        normalized_intent, planner_applied = self.apply_question_analysis_v3(
            normalized_intent_base,
            question_analysis,
            request_id=request_id,
            conversation_id=conversation_id,
        )
        before_base_route = normalized_intent.get("base_route") if isinstance(normalized_intent, dict) else getattr(normalized_intent, "base_route", None)
        before_target_cols = normalized_intent.get("target_cols") if isinstance(normalized_intent, dict) else getattr(normalized_intent, "target_cols", None)
        normalized_intent = _apply_anchor_lock(
            normalized_intent,
            followup_resolution.get("seed_map") or {},
        )
        normalized_intent = _apply_followup_context_lock(
            normalized_intent,
            followup_resolution,
        )
        after_base_route = normalized_intent.get("base_route") if isinstance(normalized_intent, dict) else getattr(normalized_intent, "base_route", None)
        after_target_cols = normalized_intent.get("target_cols") if isinstance(normalized_intent, dict) else getattr(normalized_intent, "target_cols", None)
        if self.log_event is not None and (
            before_base_route != after_base_route or list(before_target_cols or []) != list(after_target_cols or [])
        ):
            locked_base_route, locked_target_cols, lock_reason = _resolve_followup_locked_route(followup_resolution)
            self.log_event(
                "FOLLOWUP.CONTEXT.RESTORED",
                request_id=request_id,
                conversation_id=conversation_id,
                reason=lock_reason,
                before_base_route=before_base_route,
                after_base_route=after_base_route,
                before_target_cols=list(before_target_cols or []),
                after_target_cols=list(after_target_cols or []),
                locked_base_route=locked_base_route,
                locked_target_cols=list(locked_target_cols or []),
            )
        normalized_intent, question_analysis = _coerce_project_anchor_role_followup(
            normalized_intent,
            question_analysis,
            question=question,
            followup_resolution=followup_resolution,
            log_event=self.log_event,
            request_id=request_id,
            conversation_id=conversation_id,
        )
        self.log_event(
            "PLANNER.PIPELINE",
            request_id=request_id,
            conversation_id=conversation_id,
            step="intent_build",
            status="success",
            planner_applied=int(planner_applied),
            planner_failed=int(planner_failed),
            planner_stagewise_enabled=int(self.planner_stagewise_enabled),
            planner_stage1_prompt_version=self.planner_stage1_prompt_version,
            planner_stage2_prompt_version=self.planner_stage2_prompt_version,
            schema_fields=["intent_payload_version", "normalized_intent", "question_analysis", "strategy_meta"],
        )
        return _build_intent_payload_object(self.intent_payload_cls, normalized_intent, question_analysis, followup_resolution=followup_resolution), question_analysis


async def build_intent_payload(
    *,
    question: str,
    conversation_id: str,
    chat_history: List[BaseMessage],
    prev_context: List[Dict[str, Any]],
    request_id: Optional[str],
    canonical_evidence: Optional[List[Dict[str, Any]]] = None,
    view_state: Optional[ConversationViewState] = None,
    cheap_precheck: Any,
    has_superlative_cue: Any,
    extract_years: Any,
    extract_perf_types: Any,
    extract_title_terms: Any,
    classify_query_intent: Any,
    normalize_intent: Any,
    run_question_analysis: Any,
    apply_question_analysis_v3: Any,
    log_event: Any,
    intent_payload_cls: Any,
    planner_stagewise_enabled: bool,
    planner_stage1_prompt_version: str,
    planner_stage2_prompt_version: str,
) -> tuple[Any, Any]:
    facade = RequestUnderstandingFacade(
        cheap_precheck=cheap_precheck,
        has_superlative_cue=has_superlative_cue,
        extract_years=extract_years,
        extract_perf_types=extract_perf_types,
        extract_title_terms=extract_title_terms,
        classify_query_intent=classify_query_intent,
        normalize_intent=normalize_intent,
        run_question_analysis=run_question_analysis,
        apply_question_analysis_v3=apply_question_analysis_v3,
        log_event=log_event,
        intent_payload_cls=intent_payload_cls,
        planner_stagewise_enabled=planner_stagewise_enabled,
        planner_stage1_prompt_version=planner_stage1_prompt_version,
        planner_stage2_prompt_version=planner_stage2_prompt_version,
    )
    return await facade.build_intent_payload(
        question=question,
        conversation_id=conversation_id,
        chat_history=chat_history,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        view_state=view_state,
        request_id=request_id,
    )


