from __future__ import annotations

from dataclasses import dataclass, is_dataclass, replace
from typing import Any, Dict, List, Optional

from langchain_core.messages import BaseMessage

from apps.api.services.followup_anchor import anchor_to_seed_map, parse_display_limit, parse_ordinal_reference, resolve_followup_anchor
from apps.core.followup_resolution import resolve_reference_context_followup
from apps.api.services.view_state import ConversationViewState


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
    if seed_map.get("pjt_id"):
        merged.pop("pjt_no", None)
        merged["pjt_id"] = list(seed_map["pjt_id"])
    elif seed_map.get("pjt_no"):
        merged.pop("pjt_id", None)
        merged["pjt_no"] = list(seed_map["pjt_no"])
    return merged


def _inject_seed_into_normalized_intent(normalized_intent: Any, seed_map: dict[str, list[str]]) -> Any:
    if not seed_map:
        return normalized_intent
    if isinstance(normalized_intent, dict):
        patched = dict(normalized_intent)
        patched["ids_map"] = _merge_seed_into_ids_map(patched.get("ids_map") or {}, seed_map)
        return patched
    if is_dataclass(normalized_intent):
        return replace(normalized_intent, ids_map=_merge_seed_into_ids_map(getattr(normalized_intent, "ids_map", {}) or {}, seed_map))
    if hasattr(normalized_intent, "ids_map"):
        try:
            setattr(normalized_intent, "ids_map", _merge_seed_into_ids_map(getattr(normalized_intent, "ids_map", {}) or {}, seed_map))
        except Exception:
            pass
    return normalized_intent


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
        "followup_resolution_status": followup_resolution.get("followup_resolution_status") or "none",
        "followup_reference_kind": followup_resolution.get("followup_reference_kind"),
        "explicit_followup": bool(followup_resolution.get("explicit_followup")),
        "requested_token": followup_resolution.get("requested_token"),
        "requested_index": followup_resolution.get("requested_index"),
        "available_count": followup_resolution.get("available_count"),
        "seed_source": followup_resolution.get("seed_source"),
        "anchor_source": followup_resolution.get("anchor_source"),
        "focus_entity_key": focus_entity.get("pjt_id") or focus_entity.get("pjt_no") or focus_entity.get("doc_id"),
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
                normalized_intent_base = _inject_seed_into_normalized_intent(normalized_intent_base, followup_resolution.get("seed_map") or {})
        elif anchor is not None:
            seed_map = anchor_to_seed_map(anchor)
            normalized_intent_base = _inject_seed_into_normalized_intent(normalized_intent_base, seed_map)
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
            question_analysis.display_limit = min(
                int(getattr(question_analysis, "limit", 20) or 20),
                parse_display_limit(question, default=int(getattr(question_analysis, "display_limit", getattr(question_analysis, "limit", 20)) or 20)),
            )
        normalized_intent, planner_applied = self.apply_question_analysis_v3(
            normalized_intent_base,
            question_analysis,
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


