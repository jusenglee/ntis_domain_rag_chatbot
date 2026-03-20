from __future__ import annotations
from dataclasses import dataclass, is_dataclass, replace
from typing import Any, Dict, List, Optional
from langchain_core.messages import BaseMessage

from apps.core.followup_resolution import resolve_reference_context_followup
def has_explicit_precheck_signals(precheck: dict[str, Any]) -> bool:
    """cheap precheck 결과에 명시적 ID 신호가 있는지 검사한다.
    이 경로는 planner 호출을 건너뛸지 판단하는 최소 게이트로만 쓰며, 사람명·기관명 같은 의미 추론은 맡지 않는다.
    """
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


def _build_strategy_meta(normalized_intent: Any, question_analysis: Any, *, followup_resolution: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build the transport-visible v3 strategy metadata bundle."""
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
        "requested_token": followup_resolution.get("requested_token"),
        "requested_index": followup_resolution.get("requested_index"),
        "available_count": followup_resolution.get("available_count"),
        "seed_source": "reference_context_ordinal" if followup_resolution.get("followup_resolution_status") == "resolved" else None,
    }
def _build_intent_payload_object(intent_payload_cls: Any, normalized_intent: Any, question_analysis: Any, *, followup_resolution: Optional[Dict[str, Any]] = None) -> Any:
    """Instantiate the configured transport payload, keeping test doubles working."""
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
    """질문 이해 단계에서 precheck, intent 정규화, planner 적용을 묶는 얇은 facade다."""
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
        """질문 문자열에서 planner 이전에 확실히 읽을 수 있는 구조 신호만 추린다.
        year, perf type, title, wants_rank만 포함해 사람·기관 의미를 heuristic truth로 만들지 않는다.
        """
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
        request_id: Optional[str] = None,
    ) -> tuple[Any, Any]:
        """질문을 normalized_intent와 question_analysis로 분해하는 주 진입점이다.
        명시적 ID 신호가 없을 때만 planner를 호출하고, 최종 intent는 apply_question_analysis_v3를 거쳐 planner truth와 base intent를 함께 반영한다.
        """
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
        question_analysis = None
        planner_failed = 0
        followup_resolution = {"followup_resolution_status": "none", "selected_prev_item": None, "seed_map": {}}
        base_route = str((normalized_intent_base.get("base_route") if isinstance(normalized_intent_base, dict) else getattr(normalized_intent_base, "base_route", None)) or "project").strip().lower() or "project"
        base_ids_map = (normalized_intent_base.get("ids_map") if isinstance(normalized_intent_base, dict) else getattr(normalized_intent_base, "ids_map", None)) or {}
        if not has_explicit_precheck_signals(precheck) and not _has_ids_map_values(base_ids_map):
            followup_resolution = resolve_reference_context_followup(
                question=question,
                canonical_evidence=list(canonical_evidence or []),
                prev_context=prev_context,
                default_context_kind=base_route,
            )
            status = str(followup_resolution.get("followup_resolution_status") or "none")
            if status == "resolved":
                normalized_intent_base = _inject_seed_into_normalized_intent(normalized_intent_base, followup_resolution.get("seed_map") or {})
                self.log_event(
                    "FOLLOWUP.ORDINAL.RESOLVED",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    selected_prev_index=(followup_resolution.get("selected_prev_item") or {}).get("index"),
                    selected_prev_pjt_id=(followup_resolution.get("selected_prev_item") or {}).get("pjt_id"),
                    selected_prev_pjt_no=(followup_resolution.get("selected_prev_item") or {}).get("pjt_no"),
                    available_count=followup_resolution.get("available_count"),
                    requested_token=followup_resolution.get("requested_token"),
                    seed_source="reference_context_ordinal",
                )
            elif status == "out_of_range":
                self.log_event(
                    "FOLLOWUP.ORDINAL.OUT_OF_RANGE",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    available_count=followup_resolution.get("available_count"),
                    requested_index=followup_resolution.get("requested_index"),
                    requested_token=followup_resolution.get("requested_token"),
                )
            elif status in {"missing_context", "unresolved"}:
                self.log_event(
                    "FOLLOWUP.ORDINAL.UNRESOLVED",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    followup_resolution_status=status,
                    requested_token=followup_resolution.get("requested_token"),
                    available_count=followup_resolution.get("available_count"),
                )
        if not has_explicit_precheck_signals(precheck):
            question_analysis = await self.run_question_analysis(
                question=question,
                conversation_id=conversation_id,
                chat_history=chat_history,
                prev_context=prev_context,
                canonical_evidence=list(canonical_evidence or []),
                request_id=request_id,
                normalized_intent_base=normalized_intent_base,
            )
            planner_failed = int(float(getattr(question_analysis, "confidence", 0.0) or 0.0) <= 0.0)
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
    """RequestUnderstandingFacade를 즉석에서 조립해 동일한 intent build 절차를 실행하는 함수형 래퍼다."""
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
        request_id=request_id,
    )
