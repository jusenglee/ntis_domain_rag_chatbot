from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from langchain_core.messages import BaseMessage


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
    apply_planner_v2: Any
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

        명시적 ID 신호가 없을 때만 planner를 호출하고, 최종 intent는 apply_planner_v2를 거쳐 planner truth와 base intent를 함께 반영한다.
        """
        precheck = self.cheap_precheck(question)
        explicit_only_hint = self._build_explicit_only_hint(question)

        kws: List[str] = []
        raw_intent = self.classify_query_intent(question, kws, hint=explicit_only_hint)
        normalized_intent_base = self.normalize_intent(
            raw_intent,
            query=question,
            keywords=kws,
            allow_strategy_fallback=False,
            hint_years=list(explicit_only_hint.get("years", [])),
            hint_perf_types=list(explicit_only_hint.get("perf_types", [])),
            hint_title_terms=list(explicit_only_hint.get("title_terms", [])),
        )

        question_analysis = None
        planner_failed = 0
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

        normalized_intent, planner_applied = self.apply_planner_v2(
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
            schema_fields=["normalized_intent"],
        )
        return self.intent_payload_cls(normalized_intent=normalized_intent), question_analysis


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
    apply_planner_v2: Any,
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
        apply_planner_v2=apply_planner_v2,
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
