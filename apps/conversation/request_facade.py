"""
자연어 질문을 시스템 실행 계획(IntentPayloadV3)으로 변환하는 통합 패사드(Facade) 모듈입니다.

이 모듈은 대화 맥락 유지, 후속 질문 해석, 플래너 호출 등 복잡한 전처리 과정을 오케스트레이션합니다.
에이전틱 모드에서는 도구 실행기(AgentToolExecutor)가 이 모듈을 호출하여 에이전트의 의도를 구체적인 실행 계획으로 전환합니다.
"""

from __future__ import annotations

import os
import re

from dataclasses import is_dataclass, replace
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage
else:
    BaseMessage = Any

from apps.conversation.anchor_constraint_compiler import apply_anchor_lock as _apply_anchor_lock, apply_resolved_anchor_seed as _apply_resolved_anchor_seed
from apps.conversation.context_router import route_context, run_context_router
from apps.conversation.memory_observer import log_context_router_transition
from apps.conversation.followup_anchor import (
    anchor_to_seed_map,
    focus_entity_from_mention,
    is_child_anchor_source,
    parse_display_limit,
    parse_ordinal_reference,
    parse_source_reference,
    resolve_candidate_to_focus_entity,
)
from apps.conversation.scope_resolver import resolve_scope_decision
from apps.conversation.followup_resolution import resolve_reference_context_followup
from apps.conversation.turn_interpreter import (
    TurnInterpretationResult,
    build_clarification_payload,
    build_turn_candidates,
    match_anchor_to_candidates,
    resolve_hard_followup_signal,
    run_turn_interpreter,
)
from apps.conversation.turn_policy import TurnPolicyResult, resolve_turn_policy
from apps.conversation.turn_trigger import TurnTriggerResult, run_turn_trigger
from apps.conversation.session_memory import ClarificationContext, SessionMemory, SubjectQueryContext, view_state_from_current_context
from apps.platform.settings import MAX_TOP_K_SIZE
from apps.api.runtime_helpers import log_event, merge_log_fields
from apps.api.contracts.workflow_models import HardContractV1, QuestionAnalysis, SoftStrategyHintsV1
from apps.planner.planner_defaults import (
    PLANNER_STAGE1_PROMPT_VERSION,
    PLANNER_STAGE15_PROMPT_VERSION,
    PLANNER_STAGE2_PROMPT_VERSION,
)
from apps.planner.planner_service import apply_question_analysis_v3
from apps.planner.query_analysis import run_question_analysis
from apps.planner.query_intent import (
    _cheap_precheck,
    classify_query as classify_query_intent,
    extract_perf_types,
    extract_title_terms,
    extract_years,
)
from apps.platform.pipeline_steps import normalize_intent
from apps.platform.schemas import IntentPayloadV3
from apps.conversation.view_state import (
    ConversationViewState,
    clear_view_state_scope,
    get_active_subject_entity,
    get_recent_mentions,
    set_visible_answer_manifest,
)


_DISPLAY_LIMIT_SENTINEL = 10**9
_DEFAULT_RETRIEVAL_LIMIT = 20
_LIST_LIKE_OUTPUT_TYPES = {"list", "relation", "comparison", "series", "stats"}
_BROAD_HISTORY_CUES = (
    "다른 활동",
    "활동 이력",
    "활동이력",
    "활동 내역",
    "활동내역",
    "참여이력",
    "프로필",
    "소속",
    "현황",
    "이력",
    "업적",
)
_LIST_AXIS_CUES = (
    "목록",
    "리스트",
    "보여줘",
    "보여 줘",
    "알려줘",
    "알려 줘",
    "찾아줘",
    "찾아 줘",
    "있는지",
    "있어",
)
_SUBJECT_AXIS_CUES = (
    "연구자",
    "연구원",
    "사람",
    "기관",
    "회사",
    "조직",
    "성과",
    "논문",
    "특허",
    "보고서",
    "참여과제",
    "참여 과제",
    "참여성과",
    "참여 성과",
)

_NAMED_SUBJECT_CUES_BY_KIND: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("people", ("연구책임자", "참여연구원", "참여 연구원", "연구자", "연구원", "참여자", "사람")),
    ("org", ("소속기관", "수행기관", "주관기관", "참여기관", "협력기관", "기관", "회사", "조직")),
    ("project", ("과제", "프로젝트")),
    ("perf", ("성과", "논문", "특허", "보고서")),
)
_NAMED_SUBJECT_CUES = tuple(cue for _, cues in _NAMED_SUBJECT_CUES_BY_KIND for cue in cues)
_NAMED_SUBJECT_PREFIX_RE = re.compile(
    r"(?P<term>[가-힣A-Za-z][가-힣A-Za-z0-9·.\-&()\s]{1,60}?)\s*(?:의\s*)?"
    r"(?P<cue>연구책임자|참여\s*연구원|참여연구원|연구자|연구원|참여자|사람|"
    r"소속기관|수행기관|주관기관|참여기관|협력기관|기관|회사|조직|"
    r"과제|프로젝트|성과|논문|특허|보고서)"
)
_NAMED_SUBJECT_SUFFIX_RE = re.compile(
    r"(?P<cue>연구책임자|참여\s*연구원|참여연구원|연구자|연구원|참여자|사람|"
    r"소속기관|수행기관|주관기관|참여기관|협력기관|기관|회사|조직|"
    r"과제|프로젝트|성과|논문|특허|보고서)\s*(?:[:=은는이가]\s*)?"
    r"(?P<term>[가-힣A-Za-z][가-힣A-Za-z0-9·.\-&()]{1,60})"
)
_DEICTIC_SUBJECT_REFERENCE_RE = re.compile(
    r"^\s*(?:그|이|해당|저)\s*(?:"
    r"연구책임자|참여\s*연구원|참여연구원|연구자|연구원|참여자|사람|"
    r"소속기관|수행기관|주관기관|참여기관|협력기관|기관|회사|조직|"
    r"과제|프로젝트|성과|논문|특허|보고서)"
)
_SUBJECT_REFINEMENT_CUES = (
    "활동",
    "활동내역",
    "활동 내역",
    "활동이력",
    "활동 이력",
    "참여이력",
    "이력",
    "논문만",
    "성과만",
    "특허만",
    "보고서만",
    "다른 연도",
    "연도",
    "년도",
    "최근",
    "연구책임자",
    "책임자",
    "참여연구원",
    "참여 연구원",
    "참여자",
    "pi",
    "principal investigator",
    "역할",
    "최신순",
    "오래된순",
    "상위",
    "하위",
    "건만",
    "개만",
)
_SHORT_SUBJECT_WITH_AFFILIATION_RE = re.compile(
    r"^\s*(?P<name>[가-힣A-Za-z][가-힣A-Za-z0-9·.\-&\s]{1,30}?)\s*"
    r"\((?P<affiliation>[^()]{2,80})\)\s*$"
)
_SHORT_SUBJECT_BARE_RE = re.compile(r"^\s*(?P<name>[가-힣]{2,6}|[A-Za-z][A-Za-z .\-]{1,40})\s*$")
_SUBJECT_TERM_STOPWORDS = {
    "그",
    "그런",
    "해당",
    "이",
    "저",
    "어떤",
    "무슨",
    "몇",
    "다음",
    "이전",
    "직전",
    "동일",
}




def _first_text(*values: Any) -> str:

    for value in values:

        text = str(value or "").strip()

        if text:

            return text

    return ""


_CONTEXT_ROUTER_CLARIFICATION_TYPES = {
    "refinement_target_missing",
    "reference_ambiguity",
    "reference_missing_context",
}
_DETAIL_QUERY_HINTS = ("상세", "자세히", "자세한", "디테일")


def _context_router_allowed(scope_decision: Any) -> bool:
    clarification_payload = dict(getattr(scope_decision, "clarification_payload", None) or {})
    clarification_type = str(clarification_payload.get("clarification_type") or "").strip().lower()
    return bool(getattr(scope_decision, "needs_clarification", False) and clarification_type in _CONTEXT_ROUTER_CLARIFICATION_TYPES)


def _context_router_anchor_allowed(anchor: Any, *, question: str, normalized_intent_base: Any) -> bool:
    if anchor is None:
        return False
    kind = str(getattr(anchor, "kind", "") or "").strip().lower()
    if kind != "project":
        return True
    action = _first_text(_get_field(normalized_intent_base, "action", None))
    detail_requested = action.lower() == "detail" or any(token in str(question or "").strip() for token in _DETAIL_QUERY_HINTS)
    if not detail_requested:
        return True
    return bool(getattr(anchor, "pjt_id", None))


def _build_context_router_scope_summary(view_state: Optional[ConversationViewState]) -> Dict[str, Any]:
    active_scope = getattr(view_state, "active_scope", None) if view_state is not None else None
    focus = getattr(active_scope, "focus", None) if active_scope is not None else None
    child_anchor = getattr(active_scope, "child_anchor", None) if active_scope is not None else None
    return {
        "scope_kind": str(getattr(active_scope, "scope_kind", None) or "").strip().lower() or None,
        "focus_kind": str(getattr(focus, "kind", None) or "").strip().lower() or None,
        "focus_title": _first_text(getattr(focus, "title_text", None)) or None,
        "focus_pjt_id": _first_text(getattr(focus, "pjt_id", None)) or None,
        "focus_pjt_no": _first_text(getattr(focus, "pjt_no", None)) or None,
        "child_anchor_kind": str(getattr(child_anchor, "kind", None) or "").strip().lower() or None,
        "child_anchor_title": _first_text(getattr(child_anchor, "title_text", None)) or None,
    }


async def _apply_context_router_decision(
    *,
    question: str,
    request_id: Optional[str],
    conversation_id: str,
    active_view_state: Optional[ConversationViewState],
    recent_mentions: List[Any],
    has_explicit_seed: bool,
    scope_decision: Any,
    normalized_intent_base: Any,
    context_router_meta: Dict[str, Any],
    deterministic_only: bool = False,
) -> tuple[Any, Optional[Any], Dict[str, Any]]:
    """Context Router 호출·scope_decision 패치·observability 방출을 한 곳에서 처리.

    ADR-0013 후속 정리에 따라 build_intent_payload 내부의 분기를 단일 헬퍼로 추출했다.
    본 헬퍼는 라우팅 행위를 변경하지 않고, 관측과 scope_decision 패치만 수행한다.

    Returns:
        (updated_scope_decision, context_router_anchor, context_router_meta)
    """
    context_router_anchor: Optional[Any] = None
    if not (
        not has_explicit_seed
        and recent_mentions
        and _context_router_allowed(scope_decision)
    ):
        return scope_decision, context_router_anchor, context_router_meta

    context_router_meta["invoked"] = True
    active_scope_summary = _build_context_router_scope_summary(active_view_state)
    if deterministic_only:
        context_router_decision = route_context(
            question=question,
            recent_mentions=recent_mentions,
            active_scope_summary=active_scope_summary,
        )
        context_router_meta["llm_skipped_reason"] = "llm_first_resolved"
    else:
        context_router_result = run_context_router(
            question=question,
            recent_mentions=recent_mentions,
            active_scope_summary=active_scope_summary,
        )
        context_router_decision = (
            await context_router_result if isawaitable(context_router_result) else context_router_result
        )
    context_router_meta.update(
        {
            "status": context_router_decision.status,
            "source": context_router_decision.source,
            "confidence": round(float(context_router_decision.confidence or 0.0), 3),
            "rewritten_query_hint": context_router_decision.rewritten_query_hint,
            "reason": context_router_decision.reason,
        }
    )
    selected_index = context_router_decision.selected_candidate_index
    clarification_avoided = 0
    if (
        context_router_decision.status == "resolved"
        and selected_index is not None
        and 0 <= int(selected_index) < len(recent_mentions)
    ):
        router_anchor = focus_entity_from_mention(recent_mentions[int(selected_index)])
        if _context_router_anchor_allowed(router_anchor, question=question, normalized_intent_base=normalized_intent_base):
            context_router_anchor = router_anchor
            clarification_avoided = 1
            scope_decision = scope_decision.model_copy(
                update={
                    "followup_type": "reference_followup",
                    "resolved_anchor": router_anchor,
                    "needs_clarification": False,
                    "clarification_payload": None,
                }
            )
        else:
            context_router_meta["status"] = "unresolved"
            context_router_meta["reason"] = "detail_requires_instance_project_id"
    log_event(
        "CONTEXT.ROUTER",
        request_id=request_id,
        conversation_id=conversation_id,
        context_router_invoked=1,
        context_router_status=context_router_meta.get("status"),
        context_router_source=context_router_meta.get("source"),
        context_router_confidence=context_router_meta.get("confidence"),
        recent_mentions_count=context_router_meta.get("recent_mention_count"),
        rewritten_query_hint=context_router_meta.get("rewritten_query_hint"),
        llm_skipped_reason=context_router_meta.get("llm_skipped_reason"),
        clarification_avoided=clarification_avoided,
    )
    # ADR-0013: heuristic↔LLM fallback 전환 경로를 별도 이벤트로 추적한다.
    router_source = str(context_router_meta.get("source") or "").strip().lower()
    router_reason = str(context_router_meta.get("reason") or "").strip().lower()
    final_status = context_router_meta.get("status")
    final_confidence = context_router_meta.get("confidence")
    recent_count = context_router_meta.get("recent_mention_count")
    if router_source == "llm_recent_mentions":
        log_context_router_transition(
            request_id=request_id,
            conversation_id=conversation_id,
            from_source="heuristic",
            to_source="llm",
            reason=router_reason or "heuristic_non_resolved",
            final_status=final_status,
            final_confidence=final_confidence,
            recent_mention_count=recent_count,
        )
    elif router_source == "deterministic_recent_mentions" and router_reason.startswith(
        ("low_confidence_fallback", "llm_error_fallback")
    ):
        log_context_router_transition(
            request_id=request_id,
            conversation_id=conversation_id,
            from_source="llm",
            to_source="heuristic",
            reason=router_reason,
            final_status=final_status,
            final_confidence=final_confidence,
            recent_mention_count=recent_count,
        )
    return scope_decision, context_router_anchor, context_router_meta





def _resolve_followup_owner_lock(followup_resolution: dict[str, Any]) -> tuple[str | None, str | None]:
    if not isinstance(followup_resolution, dict):
        return None, None

    status = str(followup_resolution.get("followup_resolution_status") or "").strip().lower()
    if status != "resolved":
        return None, None

    selected_prev_item = dict(followup_resolution.get("selected_prev_item") or {})
    focus_entity = dict(followup_resolution.get("focus_entity") or {})
    context_kind = _first_text(

        selected_prev_item.get("context_kind"),

        focus_entity.get("kind"),

        followup_resolution.get("selected_prev_context_kind"),

    ).lower()

    if context_kind == "perf":
        return "perf", "followup_context_perf"
    if context_kind == "project":
        return "project", "followup_context_project"
    return None, None


def _apply_followup_context_lock(normalized_intent: Any, followup_resolution: dict[str, Any]) -> Any:
    locked_owner_kind, lock_reason = _resolve_followup_owner_lock(followup_resolution)
    if not locked_owner_kind:
        return normalized_intent

    if isinstance(normalized_intent, dict):
        patched = dict(normalized_intent)
        patched["context_owner_lock"] = locked_owner_kind
        patched["context_owner_lock_reason"] = lock_reason
        return patched

    if is_dataclass(normalized_intent):
        updates: dict[str, Any] = {}
        if hasattr(normalized_intent, "context_owner_lock"):
            updates["context_owner_lock"] = locked_owner_kind
        if hasattr(normalized_intent, "context_owner_lock_reason"):
            updates["context_owner_lock_reason"] = lock_reason
        return replace(normalized_intent, **updates) if updates else normalized_intent

    if hasattr(normalized_intent, "context_owner_lock") or hasattr(normalized_intent, "context_owner_lock_reason"):
        try:
            if hasattr(normalized_intent, "context_owner_lock"):
                setattr(normalized_intent, "context_owner_lock", locked_owner_kind)
            if hasattr(normalized_intent, "context_owner_lock_reason"):
                setattr(normalized_intent, "context_owner_lock_reason", lock_reason)
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


def _subject_kind_for_cue(cue: str) -> str | None:
    compact = re.sub(r"\s+", "", str(cue or "").strip())
    if not compact:
        return None
    for kind, cues in _NAMED_SUBJECT_CUES_BY_KIND:
        if any(compact == re.sub(r"\s+", "", item) for item in cues):
            return kind
    return None


def _clean_explicit_subject_term(term: Any) -> str:
    text = re.sub(r"\s+", " ", str(term or "")).strip(" \t\r\n,.:;!?()[]{}'\"")
    text = re.sub(r"^(?:그리고|또|이번엔|이번에는|그럼|그러면)\s+", "", text).strip()
    if not text:
        return ""
    if text in _SUBJECT_TERM_STOPWORDS:
        return ""
    if re.fullmatch(r"(?:19|20)\d{2}(?:\s*[~\-]\s*(?:19|20)\d{2})?\s*년?도?", text):
        return ""
    if re.fullmatch(r"\d+\s*(?:번|번째|차|건|개|명)?", text):
        return ""
    if len(text) < 2:
        return ""
    return text


def _extract_named_subject_from_question(question: str) -> tuple[str | None, str | None]:
    text = str(question or "").strip()
    if not text:
        return None, None
    for pattern in (_NAMED_SUBJECT_PREFIX_RE, _NAMED_SUBJECT_SUFFIX_RE):
        for match in pattern.finditer(text):
            cue = str(match.group("cue") or "").strip()
            subject_kind = _subject_kind_for_cue(cue)
            term = _clean_explicit_subject_term(match.group("term"))
            if subject_kind and term:
                return subject_kind, term
    return None, None


def _intent_subject_terms(normalized_intent: Any) -> tuple[str | None, str | None]:
    field_kind_pairs = (
        ("people_terms", "people"),
        ("org_terms", "org"),
        ("lead_org_terms", "org"),
        ("participant_org_terms", "org"),
        ("people_affiliation_org_terms", "org"),
        ("title", "perf"),
    )
    for field, kind in field_kind_pairs:
        values = _get_field(normalized_intent, field, None) or []
        iterable = values if isinstance(values, (list, tuple, set)) else [values]
        for value in iterable:
            term = _clean_explicit_subject_term(value)
            if term:
                return kind, term
    return None, None


def _has_explicit_named_subject_seed(question: str, normalized_intent_base: Any) -> bool:
    if parse_source_reference(question) is not None or parse_ordinal_reference(question) is not None:
        return False
    text = str(question or "").strip()
    if not text:
        return False
    if _DEICTIC_SUBJECT_REFERENCE_RE.search(text):
        return False
    has_axis_cue = any(cue in text for cue in _NAMED_SUBJECT_CUES)
    intent_kind, intent_term = _intent_subject_terms(normalized_intent_base)
    question_kind, question_term = _extract_named_subject_from_question(text)
    return bool((has_axis_cue and question_kind and question_term) or (has_axis_cue and intent_kind and intent_term))


def _subject_seed_from_focus(view_state: Optional[ConversationViewState]) -> tuple[str | None, str | None]:
    focus = get_active_subject_entity(view_state)
    kind = str(getattr(focus, "kind", "") or "").strip().lower()
    if kind not in {"people", "org"}:
        return None, None
    title = _clean_explicit_subject_term(getattr(focus, "title_text", None))
    return (kind, title) if title else (None, None)


def _subject_seed_from_previous_contract(previous_turn_contract: Dict[str, Any]) -> tuple[str | None, str | None]:
    kind = str(previous_turn_contract.get("subject_kind") or previous_turn_contract.get("current_subject_kind") or "").strip().lower()
    title = _clean_explicit_subject_term(
        previous_turn_contract.get("subject_name") or previous_turn_contract.get("current_subject_name")
    )
    if kind in {"people", "org"} and title:
        return kind, title
    return None, None


def _has_subject_refinement_signal(question: str, normalized_intent_base: Any) -> bool:
    if parse_source_reference(question) is not None or parse_ordinal_reference(question) is not None:
        return False
    if _has_explicit_named_subject_seed(question, normalized_intent_base):
        return False
    if _get_field(normalized_intent_base, "years", None) or _get_field(normalized_intent_base, "year_from", None) or _get_field(normalized_intent_base, "year_to", None):
        return True
    for field in ("perf_types", "title", "project_tag_filters", "perf_tag_filters"):
        values = _get_field(normalized_intent_base, field, None) or []
        iterable = values if isinstance(values, (list, tuple, set)) else [values]
        if any(str(value or "").strip() for value in iterable):
            return True
    text = str(question or "").strip()
    lowered = text.lower()
    return bool(text and any(cue in lowered for cue in _SUBJECT_REFINEMENT_CUES))


def _apply_subject_context_seed(normalized_intent: Any, *, subject_kind: str, subject_name: str) -> Any:
    kind = str(subject_kind or "").strip().lower()
    title = _clean_explicit_subject_term(subject_name)
    if not kind or not title:
        return normalized_intent
    if kind == "people":
        people_terms = _merge_unique_terms(_get_field(normalized_intent, "people_terms", []) or [], title)
        return _replace_fields(normalized_intent, people_terms=people_terms)
    if kind == "org":
        org_terms = _merge_unique_terms(_get_field(normalized_intent, "org_terms", []) or [], title)
        return _replace_fields(normalized_intent, org_terms=org_terms)
    return normalized_intent


def _resolve_subject_refinement_seed(
    *,
    question: str,
    normalized_intent_base: Any,
    view_state: Optional[ConversationViewState],
    previous_turn_contract: Dict[str, Any],
) -> tuple[str | None, str | None]:
    if not _has_subject_refinement_signal(question, normalized_intent_base):
        return None, None
    kind, title = _subject_seed_from_focus(view_state)
    if kind and title:
        return kind, title
    return _subject_seed_from_previous_contract(previous_turn_contract)




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


def _clear_active_view_scope(view_state: ConversationViewState) -> ConversationViewState:
    return clear_view_state_scope(view_state)


def _merge_unique_terms(values: Any, *terms: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    iterable = values if isinstance(values, (list, tuple, set)) else [values]
    for value in iterable:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
    for term in terms:
        text = str(term or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
    return merged


def _has_field_value(source: Any, field: str) -> bool:
    values = _get_field(source, field, None)
    if isinstance(values, (list, tuple, set)):
        return any(str(value or "").strip() for value in values)
    return bool(str(values or "").strip())


def _parse_short_subject_supplement(question: str, constraints: Dict[str, Any]) -> Dict[str, str]:
    text = re.sub(r"\s+", " ", str(question or "").strip())
    if not text or len(text) > 90:
        return {}
    if any(token in text for token in ("?", "？", "보여", "알려", "정리", "찾아", "만", "까지", "부터")):
        return {}

    match = _SHORT_SUBJECT_WITH_AFFILIATION_RE.fullmatch(text)
    if match:
        name = _clean_explicit_subject_term(match.group("name"))
        affiliation = _clean_explicit_subject_term(match.group("affiliation"))
        if name:
            return {
                "kind": str(constraints.get("subject_kind_hint") or "people").strip().lower() or "people",
                "name": name,
                "affiliation": affiliation,
            }

    match = _SHORT_SUBJECT_BARE_RE.fullmatch(text)
    if match:
        name = _clean_explicit_subject_term(match.group("name"))
        if name:
            return {
                "kind": str(constraints.get("subject_kind_hint") or "people").strip().lower() or "people",
                "name": name,
            }

    return {}


def _apply_unresolved_constraints_to_intent(
    normalized_intent: Any,
    *,
    constraints: Dict[str, Any],
    current_question: str,
    unresolved_question: Optional[str],
) -> Any:
    updates: Dict[str, Any] = {}
    for field in ("years", "perf_types", "title", "project_tag_filters", "perf_tag_filters", "tag_filters"):
        values = constraints.get(field)
        if values and not _has_field_value(normalized_intent, field):
            updates[field] = _merge_unique_terms([], *(values if isinstance(values, (list, tuple, set)) else [values]))

    if updates.get("years"):
        if not _has_field_value(normalized_intent, "year_from"):
            updates["year_from"] = updates["years"][0]
        if not _has_field_value(normalized_intent, "year_to"):
            updates["year_to"] = updates["years"][-1]
    else:
        for field in ("year_from", "year_to"):
            value = str(constraints.get(field) or "").strip()
            if value and not _has_field_value(normalized_intent, field):
                updates[field] = value

    for field in ("base_route", "action", "output_type"):
        value = str(constraints.get(field) or "").strip().lower()
        current_value = str(_get_field(normalized_intent, field, "") or "").strip().lower()
        if value and (not current_value or current_value in {"topic", "summary"}):
            updates[field] = value

    role = str(constraints.get("researcher_role") or "").strip()
    if role:
        updates["keywords"] = _merge_unique_terms(_get_field(normalized_intent, "keywords", []) or [], role)

    current_retrieval_query = str(_get_field(normalized_intent, "retrieval_query", "") or "").strip()
    previous_query = str(unresolved_question or "").strip()
    if previous_query and previous_query not in current_retrieval_query:
        combined = f"{str(current_question or '').strip()} {previous_query}".strip()
        if combined:
            updates["retrieval_query"] = combined

    return _replace_fields(normalized_intent, **updates) if updates else normalized_intent


def _recover_clarification_subject_supplement(
    *,
    question: str,
    normalized_intent_base: Any,
    session_memory: Optional[SessionMemory],
) -> tuple[Any, Dict[str, Any]]:
    context = getattr(session_memory, "current_context", None) if session_memory is not None else None
    if not isinstance(context, ClarificationContext):
        return normalized_intent_base, {}

    constraints = dict(context.unresolved_constraints or {})
    if not constraints:
        return normalized_intent_base, {}

    supplement = _parse_short_subject_supplement(question, constraints)
    if not supplement:
        return normalized_intent_base, {}

    kind = str(supplement.get("kind") or constraints.get("subject_kind_hint") or "people").strip().lower()
    name = _clean_explicit_subject_term(supplement.get("name"))
    affiliation = _clean_explicit_subject_term(supplement.get("affiliation"))
    if not name or kind not in {"people", "org"}:
        return normalized_intent_base, {}

    recovered = normalized_intent_base
    if kind == "people":
        recovered = _replace_fields(
            recovered,
            people_terms=_merge_unique_terms(_get_field(recovered, "people_terms", []) or [], name),
        )
        if affiliation:
            recovered = _replace_fields(
                recovered,
                people_affiliation_org_terms=_merge_unique_terms(
                    _get_field(recovered, "people_affiliation_org_terms", []) or [],
                    affiliation,
                ),
                org_role=str(_get_field(recovered, "org_role", "") or "").strip().lower() or "affiliation",
            )
    else:
        recovered = _replace_fields(
            recovered,
            org_terms=_merge_unique_terms(_get_field(recovered, "org_terms", []) or [], name),
        )

    recovered = _apply_unresolved_constraints_to_intent(
        recovered,
        constraints=constraints,
        current_question=question,
        unresolved_question=context.unresolved_question,
    )

    return recovered, {
        "applied": True,
        "source": "clarification_context",
        "reason": context.reason,
        "subject_kind": kind,
        "subject_name": name,
        "affiliation": affiliation or None,
        "restored_fields": sorted(
            key
            for key in (
                "years",
                "year_from",
                "year_to",
                "perf_types",
                "output_type",
                "action",
                "base_route",
                "researcher_role",
            )
            if constraints.get(key)
        ),
    }


def _has_subject_axis_request(question: str, normalized_intent: Any) -> bool:
    for field in (
        "people_terms",
        "org_terms",
        "lead_org_terms",
        "participant_org_terms",
        "people_affiliation_org_terms",
        "perf_types",
    ):
        values = _get_field(normalized_intent, field, None) or []
        if any(str(value or "").strip() for value in (values if isinstance(values, (list, tuple, set)) else [values])):
            return True
    if str(_get_field(normalized_intent, "org_role", "") or "").strip():
        return True
    text = str(question or "").strip()
    return bool(text and any(token in text for token in _SUBJECT_AXIS_CUES))


def _has_broad_history_or_list_cue(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    return any(token in text for token in [*_BROAD_HISTORY_CUES, *_LIST_AXIS_CUES])


def _resolve_followup_subject_hint(anchor: Any = None, *, followup_resolution: Optional[Dict[str, Any]] = None) -> tuple[str | None, str | None]:
    anchor_kind = str(getattr(anchor, "kind", "") or "").strip().lower()
    anchor_title = str(getattr(anchor, "title_text", "") or "").strip()
    if anchor_kind and anchor_title:
        return anchor_kind, anchor_title

    resolution = dict(followup_resolution or {})
    selected_prev_item = dict(resolution.get("selected_prev_item") or {})
    focus_entity = dict(resolution.get("focus_entity") or {})
    kind = _first_text(
        selected_prev_item.get("context_kind"),
        focus_entity.get("kind"),
    ).lower()
    if not kind:
        return None, None
    title = _first_text(
        selected_prev_item.get("person_name"),
        selected_prev_item.get("title"),
        focus_entity.get("title_text"),
    )
    return kind, title or None


def _apply_followup_subject_hint(
    normalized_intent: Any,
    *,
    anchor: Any = None,
    followup_resolution: Optional[Dict[str, Any]] = None,
) -> Any:
    kind, title = _resolve_followup_subject_hint(anchor, followup_resolution=followup_resolution)
    if not kind or not title:
        return normalized_intent

    if kind == "people":
        people_terms = _merge_unique_terms(_get_field(normalized_intent, "people_terms", []) or [], title)
        return _replace_fields(normalized_intent, people_terms=people_terms)
    if kind == "org":
        org_terms = _merge_unique_terms(_get_field(normalized_intent, "org_terms", []) or [], title)
        return _replace_fields(normalized_intent, org_terms=org_terms)
    if kind == "perf":
        title_terms = _merge_unique_terms(_get_field(normalized_intent, "title", []) or [], title)
        return _replace_fields(normalized_intent, title=title_terms)
    return normalized_intent


def _should_force_planner_for_explicit_seed(
    *,
    question: str,
    normalized_intent: Any,
    anchor: Any = None,
    followup_resolution: Optional[Dict[str, Any]] = None,
) -> bool:
    if is_child_anchor_source(getattr(anchor, "source", None)):
        return True
    followup_kind = str((followup_resolution or {}).get("followup_reference_kind") or "").strip().lower()
    if followup_kind == "child_entity":
        return True
    if _has_subject_axis_request(question, normalized_intent):
        return True
    if _has_broad_history_or_list_cue(question):
        return True
    return False




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





def _resolve_child_detail_anchor(

    anchor: Any,

    *,

    followup_resolution: Optional[Dict[str, Any]] = None,

) -> tuple[str | None, str | None]:

    resolution = dict(followup_resolution or {})

    status = str(resolution.get("followup_resolution_status") or "").strip().lower()

    if status != "resolved":

        return None, None

    anchor_kind = str(getattr(anchor, "kind", "") or "").strip().lower()

    anchor_source = str(getattr(anchor, "source", "") or "").strip().lower()

    if is_child_anchor_source(anchor_source) and anchor_kind in {"people", "org", "perf"}:

        return anchor_kind, anchor_source or None

    followup_kind = str(resolution.get("followup_reference_kind") or "").strip().lower()

    if followup_kind != "child_entity":

        return None, None

    focus_entity = dict(resolution.get("focus_entity") or {})

    selected_prev_item = dict(resolution.get("selected_prev_item") or {})

    resolved_kind = _first_text(

        focus_entity.get("kind"),

        selected_prev_item.get("context_kind"),

    ).lower()

    if resolved_kind not in {"people", "org", "perf"}:

        return None, None

    resolved_source = _first_text(

        resolution.get("anchor_source"),

        resolution.get("seed_source"),

    ).lower()

    return resolved_kind, resolved_source or None



def _coerce_child_anchor_detail_followup(

    normalized_intent: Any,

    question_analysis: Any,

    *,

    anchor: Any = None,

    question: str,

    followup_resolution: Dict[str, Any],

    request_id: Optional[str],

    conversation_id: str,

) -> tuple[Any, Any]:

    anchor_kind, anchor_source = _resolve_child_detail_anchor(

        anchor,

        followup_resolution=followup_resolution,

    )

    if not anchor_kind:

        return normalized_intent, question_analysis

    intent_needs_update = (

        str(_get_field(normalized_intent, "base_route", "") or "").strip().lower() != anchor_kind

        or str(_get_field(normalized_intent, "action", "") or "").strip().lower() != "detail"

        or str(_get_field(normalized_intent, "output_type", "") or "").strip().lower() != "detail"

    )

    if intent_needs_update:

        normalized_intent = _replace_fields(

            normalized_intent,

            base_route=anchor_kind,

            action="detail",

            output_type="detail",

        )

    qa_needs_update = bool(

        question_analysis is not None

        and (

            str(_get_field(question_analysis, "head", "") or "").strip().lower() != anchor_kind

            or str(_get_field(question_analysis, "action", "") or "").strip().lower() != "detail"

            or str(_get_field(question_analysis, "output_type", "") or "").strip().lower() != "detail"

            or _coerce_positive_int(_get_field(question_analysis, "limit", None)) != 1

            or _coerce_positive_int(_get_field(question_analysis, "display_limit", None)) != 1

        )

    )

    if qa_needs_update:

        question_analysis = _replace_fields(

            question_analysis,

            head=anchor_kind,

            action="detail",

            output_type="detail",

            limit=1,

            display_limit=1,

            retrieval_query=str(question or "").strip() or _get_field(question_analysis, "retrieval_query", None),

        )

    if intent_needs_update or qa_needs_update:

        log_event(

            "FOLLOWUP.CHILD_DETAIL.COERCED",

            request_id=request_id,

            conversation_id=conversation_id,

            anchor_kind=anchor_kind,

            anchor_source=anchor_source,

            followup_reference_kind=(followup_resolution or {}).get("followup_reference_kind"),

        )

    return normalized_intent, question_analysis



def _coerce_project_anchor_role_followup(

    normalized_intent: Any,

    question_analysis: Any,

    *,

    question: str,

    followup_resolution: Dict[str, Any],

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
    if anchor is not None and any(
        [
            anchor.display_rank is not None,
            anchor.pjt_id,
            anchor.pjt_no,
            anchor.rst_id,
            anchor.person_no,
            anchor.org_id,
            anchor.org_code,
            anchor.biz_no,
            anchor.doi,
            anchor.issn,
            anchor.doc_id,
            anchor.title_text,
        ]
    ):
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
            "person_name": anchor.title_text if str(getattr(anchor, "kind", "") or "").strip().lower() == "people" else None,
            "context_kind": anchor.kind,
            "view_id": anchor.view_id,
        }
    source_reference = parse_source_reference(question)
    ordinal_reference = parse_ordinal_reference(question)
    reference_kind = (
        "child_entity"
        if is_child_anchor_source(getattr(anchor, "source", None))
        else
        "source_reference"
        if anchor.source == "display_snapshot" and source_reference is not None
        else "deictic"
        if anchor.source == "display_snapshot" and ordinal_reference is None
        else "ordinal"
        if anchor.source == "display_snapshot"
        else "focus"
        if anchor.source != "explicit_id"
        else "explicit_id"
    )
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


def _build_turn_contract(
    *,
    normalized_intent: Any,
    question_analysis: Any,
    count_validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    count_validation = dict(count_validation or {})
    action = str(getattr(question_analysis, "action", "") or "").strip().lower()
    output_type = str(getattr(question_analysis, "output_type", "") or "").strip().lower()
    if not action and isinstance(normalized_intent, dict):
        action = str(normalized_intent.get("action") or "").strip().lower()
    elif not action:
        action = str(getattr(normalized_intent, "action", "") or "").strip().lower()
    if not output_type and isinstance(normalized_intent, dict):
        output_type = str(normalized_intent.get("output_type") or "").strip().lower()
    elif not output_type:
        output_type = str(getattr(normalized_intent, "output_type", "") or "").strip().lower()
    turn_kind = output_type or action or "summary"
    planner_explicit_count = _coerce_positive_int(count_validation.get("planner_explicit_count"))
    explicit_count_requested = planner_explicit_count is not None
    requested_count = _coerce_positive_int(getattr(question_analysis, "display_limit", None))
    if requested_count is None:
        requested_count = _coerce_positive_int(getattr(question_analysis, "limit", None))
    if output_type in {"relation", "comparison", "series"}:
        count_contract = "exact"
    elif action == "list" or output_type == "list":
        count_contract = "exact" if explicit_count_requested else "partial_ok"
    else:
        count_contract = "n/a"
    if count_contract == "exact" and turn_kind in _LIST_LIKE_OUTPUT_TYPES:
        answer_publishability_policy = "publishable_if_supported"
        followup_rights = "source_allowed"
    elif count_contract == "partial_ok":
        answer_publishability_policy = "withhold_on_partial"
        followup_rights = "none"
    else:
        answer_publishability_policy = "never_publish"
        followup_rights = "none"
    return {
        "turn_kind": turn_kind,
        "count_contract": count_contract,
        "requested_count": requested_count,
        "explicit_count_requested": bool(explicit_count_requested),
        "answer_publishability_policy": answer_publishability_policy,
        "followup_rights": followup_rights,
        "planner_count_source": str(count_validation.get("planner_count_source") or "").strip().lower() or None,
        "planner_explicit_count": planner_explicit_count,
        "validation_status": str(count_validation.get("validation_status") or "valid").strip().lower() or "valid",
        "invalid_reason": str(count_validation.get("invalid_reason") or "").strip().lower() or None,
    }


def _build_policy_clarification_resolution(
    *,
    policy: TurnPolicyResult | Dict[str, Any],
    trigger: TurnTriggerResult | Dict[str, Any],
    snapshot: Any,
    question: str,
) -> Dict[str, Any]:
    policy_payload = policy if isinstance(policy, dict) else policy.model_dump()
    trigger_payload = trigger if isinstance(trigger, dict) else trigger.model_dump()
    clarification_payload = dict(policy_payload.get("clarification_payload") or {})
    requested_index = parse_source_reference(question)
    if requested_index is None:
        requested_index = parse_ordinal_reference(question)
    reference_style = str(trigger_payload.get("reference_style") or "").strip().lower()
    followup_reference_kind = "source_reference" if reference_style == "source_reference" else "entity_reference"
    return {
        "followup_resolution_status": "clarification_required",
        "selected_prev_item": None,
        "seed_map": {},
        "seed_source": None,
        "explicit_followup": True,
        "followup_reference_kind": followup_reference_kind,
        "requested_token": question,
        "requested_index": requested_index,
        "available_count": len(getattr(snapshot, "items", []) or []),
        "anchor_source": None,
        "focus_entity": None,
        "clarification_type": clarification_payload.get("clarification_type") or "followup_policy_block",
        "clarification_reason": clarification_payload.get("reason") or policy_payload.get("blocked_reason"),
        "clarification_payload": clarification_payload,
    }





def _build_strategy_meta(
    normalized_intent: Any,
    question_analysis: Any,
    *,
    followup_resolution: Optional[Dict[str, Any]] = None,
    turn_id: Optional[str] = None,
    turn_trigger: Optional[Dict[str, Any]] = None,
    turn_interpretation: Optional[Dict[str, Any]] = None,
    turn_contract: Optional[Dict[str, Any]] = None,
    turn_policy: Optional[Dict[str, Any]] = None,
    turn_candidates: Optional[List[Dict[str, Any]]] = None,
    count_validation: Optional[Dict[str, Any]] = None,
    context_router: Optional[Dict[str, Any]] = None,
    clarification_recovery: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:

    def _dump_meta_model(value: Any) -> Dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "model_dump"):
            return dict(value.model_dump())
        return dict(getattr(value, "__dict__", {}) or {})

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
    turn_trigger = dict(turn_trigger or {})
    turn_interpretation = dict(turn_interpretation or {})
    turn_contract = dict(turn_contract or {})
    turn_policy = dict(turn_policy or {})
    turn_candidates = list(turn_candidates or [])
    count_validation = dict(count_validation or {})
    clarification_recovery = dict(clarification_recovery or {})
    context_router = dict(context_router or {})
    hard_contract = _dump_meta_model(_get_field(question_analysis, "hard_contract", None))
    soft_strategy_hints = _dump_meta_model(_get_field(question_analysis, "soft_strategy_hints", None))

    selected_prev_item = dict(followup_resolution.get("selected_prev_item") or {})

    focus_entity = dict(followup_resolution.get("focus_entity") or {})

    return {

        "strategy_version": str(getattr(question_analysis, "strategy_version", "v3") or "v3"),
        "hard_contract": hard_contract,
        "soft_strategy_hints": soft_strategy_hints,
        "resolved_project_key_axis": hard_contract.get("resolved_project_key_axis"),
        "unsupported_project_key_aliases": list(hard_contract.get("unsupported_project_key_aliases") or []),
        "project_key_axis_locked": bool(hard_contract.get("project_key_axis_locked")),

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
        "anchor_reference_kind": followup_resolution.get("followup_reference_kind"),
        "clarification_type": followup_resolution.get("clarification_type"),
        "clarification_reason": followup_resolution.get("clarification_reason"),
        "clarification_payload": dict(followup_resolution.get("clarification_payload") or {}),
        "focus_entity_key": focus_entity.get("pjt_id") or focus_entity.get("pjt_no") or focus_entity.get("rst_id") or focus_entity.get("person_no") or focus_entity.get("org_id") or focus_entity.get("org_code") or focus_entity.get("biz_no") or focus_entity.get("doi") or focus_entity.get("issn") or focus_entity.get("doc_id"),
        "focus_entity": focus_entity or None,
        "candidate_items": list(followup_resolution.get("candidate_items") or []),
        "display_view_id": selected_prev_item.get("view_id") if selected_prev_item else focus_entity.get("view_id"),

        "display_rank": focus_entity.get("display_rank"),

        "turn_id": str(turn_id or "").strip() or None,
        "turn_trigger": turn_trigger,
        "turn_trigger_intent": turn_trigger.get("turn_intent"),
        "turn_reference_style": turn_trigger.get("reference_style"),
        "turn_interpretation": turn_interpretation,
        "turn_interpretation_action": turn_interpretation.get("chosen_action"),
        "turn_interpretation_confidence": turn_interpretation.get("confidence"),
        "turn_candidate_count": len(turn_candidates),
        "turn_candidates": turn_candidates,
        "turn_contract": turn_contract,
        "turn_kind": turn_contract.get("turn_kind"),
        "count_contract": turn_contract.get("count_contract"),
        "requested_count": turn_contract.get("requested_count"),
        "explicit_count_requested": bool(turn_contract.get("explicit_count_requested")),
        "planner_count_source": turn_contract.get("planner_count_source") or count_validation.get("planner_count_source"),
        "planner_explicit_count": turn_contract.get("planner_explicit_count"),
        "count_contract_validation_status": turn_contract.get("validation_status") or count_validation.get("validation_status") or "valid",
        "count_contract_invalid_reason": turn_contract.get("invalid_reason") or count_validation.get("invalid_reason"),
        "count_contract_clarification_payload": dict(count_validation.get("clarification_payload") or {}),
        "turn_policy": turn_policy,
        "turn_execution_path": turn_policy.get("execution_path"),
        "turn_policy_blocked_reason": turn_policy.get("blocked_reason"),
        "context_router_status": context_router.get("status"),
        "context_router_source": context_router.get("source"),
        "context_router_confidence": context_router.get("confidence"),
        "context_router_invoked": bool(context_router.get("invoked")),
        "recent_mention_count": context_router.get("recent_mention_count"),
        "rewritten_query_hint": context_router.get("rewritten_query_hint"),
        "clarification_recovery": clarification_recovery,
        "clarification_recovery_applied": bool(clarification_recovery.get("applied")),

    }





def _build_intent_payload_object(
    normalized_intent: Any,
    question_analysis: Any,
    *,
    followup_resolution: Optional[Dict[str, Any]] = None,
    turn_id: Optional[str] = None,
    turn_trigger: Optional[Dict[str, Any]] = None,
    turn_interpretation: Optional[Dict[str, Any]] = None,
    turn_contract: Optional[Dict[str, Any]] = None,
    turn_policy: Optional[Dict[str, Any]] = None,
    turn_candidates: Optional[List[Dict[str, Any]]] = None,
    count_validation: Optional[Dict[str, Any]] = None,
    context_router: Optional[Dict[str, Any]] = None,
    clarification_recovery: Optional[Dict[str, Any]] = None,
) -> Any:

    strategy_meta = _build_strategy_meta(
        normalized_intent,
        question_analysis,
        followup_resolution=followup_resolution,
        turn_id=turn_id,
        turn_trigger=turn_trigger,
        turn_interpretation=turn_interpretation,
        turn_contract=turn_contract,
        turn_policy=turn_policy,
        turn_candidates=turn_candidates,
        count_validation=count_validation,
        context_router=context_router,
        clarification_recovery=clarification_recovery,
    )

    try:

        return IntentPayloadV3(

            intent_payload_version="v3",

            normalized_intent=normalized_intent,

            question_analysis=question_analysis,

            strategy_meta=strategy_meta,

        )

    except TypeError:

        payload = IntentPayloadV3(normalized_intent=normalized_intent)

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


def _set_question_analysis_count(question_analysis: Any, *, limit: int, display_limit: int) -> bool:
    try:
        setattr(question_analysis, "limit", int(limit))
        setattr(question_analysis, "display_limit", int(display_limit))
        return True
    except Exception:
        pass
    try:
        object.__setattr__(question_analysis, "limit", int(limit))
        object.__setattr__(question_analysis, "display_limit", int(display_limit))
        return True
    except Exception:
        return False





def _derive_planner_count_source(*, action: str, output_type: str, has_explicit_count: bool) -> str:
    if output_type == "detail" or action == "detail":
        return "detail_forced"
    if has_explicit_count and (action == "list" or output_type in _LIST_LIKE_OUTPUT_TYPES):
        return "explicit_count"
    return "planner_default"


def _build_count_contract_clarification_payload(
    *,
    invalid_reason: str,
    planner_limit: Optional[int],
    planner_display_limit: Optional[int],
    planner_explicit_count: Optional[int],
) -> Dict[str, Any]:
    reason = str(invalid_reason or "").strip().lower()
    if reason == "explicit_count_mismatch":
        message = "질문에서 요청한 개수와 검색 계획의 표시 개수가 맞지 않아 답변을 진행하지 않습니다. 대상이나 개수를 다시 지정해 주세요."
    elif reason == "detail_count_contract_violation":
        message = "상세 조회는 한 대상을 기준으로만 진행할 수 있는데 검색 계획의 개수 계약이 맞지 않아 답변을 보류합니다. 대상을 하나로 지정해 다시 질문해 주세요."
    else:
        message = "요청한 표시 개수를 안정적으로 확인하지 못해 답변을 진행하지 않습니다. 대상이나 개수를 더 구체적으로 지정해 다시 질문해 주세요."
    return {
        "status": "clarification_required",
        "clarification_type": "planner_count_contract",
        "reason": reason,
        "message": message,
        "planner_limit": planner_limit,
        "planner_display_limit": planner_display_limit,
        "planner_explicit_count": planner_explicit_count,
    }


def _resolve_question_analysis_count(question_analysis: Any, *, question: str, request_id: Optional[str], conversation_id: str) -> Dict[str, Any]:

    """Validate and narrowly normalize the planner-assembled count contract."""

    if question_analysis is None:

        return {
            "validation_status": "invalid",
            "invalid_reason": "missing_question_analysis",
            "planner_limit": None,
            "planner_display_limit": None,
            "planner_explicit_count": None,
            "planner_count_source": "planner_default",
            "clarification_payload": _build_count_contract_clarification_payload(
                invalid_reason="missing_question_analysis",
                planner_limit=None,
                planner_display_limit=None,
                planner_explicit_count=None,
            ),
        }



    action = str(getattr(question_analysis, "action", "") or "").strip().lower()

    output_type = str(getattr(question_analysis, "output_type", "") or "").strip().lower()

    is_list_like = action == "list" or output_type in _LIST_LIKE_OUTPUT_TYPES

    explicit_count = parse_display_limit(question, default=_DISPLAY_LIMIT_SENTINEL)

    has_explicit_count = explicit_count != _DISPLAY_LIMIT_SENTINEL



    planner_limit_raw = getattr(question_analysis, "limit", None)

    planner_display_limit_raw = getattr(question_analysis, "display_limit", None)
    original_planner_limit_raw = planner_limit_raw
    original_planner_display_limit_raw = planner_display_limit_raw

    planner_limit = _coerce_positive_int(planner_limit_raw)

    planner_display_limit = _coerce_positive_int(planner_display_limit_raw)

    planner_count_source = _derive_planner_count_source(
        action=action,
        output_type=output_type,
        has_explicit_count=has_explicit_count,
    )
    invalid_reason: Optional[str] = None
    is_detail = output_type == "detail" or action == "detail"
    count_coerced = False
    if is_detail:
        count_coerced = planner_limit != 1 or planner_display_limit != 1
        if not _set_question_analysis_count(question_analysis, limit=1, display_limit=1):
            invalid_reason = "detail_count_normalization_failed"
        else:
            planner_limit = 1
            planner_display_limit = 1
            planner_limit_raw = 1
            planner_display_limit_raw = 1
    elif planner_limit is None:
        invalid_reason = "missing_limit"
    elif planner_display_limit is None:
        invalid_reason = "missing_display_limit"
    elif planner_limit > MAX_TOP_K_SIZE or planner_display_limit > MAX_TOP_K_SIZE:
        invalid_reason = "max_top_k_exceeded"
    elif planner_display_limit > planner_limit:
        invalid_reason = "display_gt_limit"
    elif is_list_like and has_explicit_count and planner_display_limit != explicit_count:
        invalid_reason = "explicit_count_mismatch"
    validation_status = "invalid" if invalid_reason else "valid"
    clarification_payload = (
        _build_count_contract_clarification_payload(
            invalid_reason=invalid_reason,
            planner_limit=planner_limit,
            planner_display_limit=planner_display_limit,
            planner_explicit_count=explicit_count if has_explicit_count else None,
        )
        if invalid_reason
        else None
    )
    log_event(
        "PLANNER.COUNT_CONTRACT",
        request_id=request_id,
        conversation_id=conversation_id,
        source="planner_invalid" if invalid_reason else ("planner_coerced" if count_coerced else "planner_validated"),
        action=action or None,
        output_type=output_type or None,
        planner_limit=planner_limit,
        planner_display_limit=planner_display_limit,
        original_planner_limit=original_planner_limit_raw,
        original_planner_display_limit=original_planner_display_limit_raw,
        count_coerced=int(bool(count_coerced and not invalid_reason)),
        planner_explicit_count=None if not has_explicit_count else explicit_count,
        planner_count_source=planner_count_source,
        validation_status=validation_status,
        invalid_reason=invalid_reason,
    )
    return {
        "validation_status": validation_status,
        "invalid_reason": invalid_reason,
        "planner_limit": planner_limit,
        "planner_display_limit": planner_display_limit,
        "original_planner_limit": original_planner_limit_raw,
        "original_planner_display_limit": original_planner_display_limit_raw,
        "count_coerced": bool(count_coerced and not invalid_reason),
        "planner_explicit_count": explicit_count if has_explicit_count else None,
        "planner_count_source": planner_count_source,
        "clarification_payload": clarification_payload,
    }





def _build_explicit_only_hint(question: str) -> Dict[str, Any]:
    return {
        "years": extract_years(question),
        "perf_types": extract_perf_types(question),
        "title_terms": extract_title_terms(question),
    }


def _normalize_text_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    out: List[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _coerce_direct_compile_limit(
    *,
    question: str,
    tool_args: Dict[str, Any],
    default_limit: int = _DEFAULT_RETRIEVAL_LIMIT,
) -> int:
    explicit_count = parse_display_limit(question, default=_DISPLAY_LIMIT_SENTINEL)
    limit = _coerce_positive_int(tool_args.get("limit"))
    if limit is None and explicit_count != _DISPLAY_LIMIT_SENTINEL:
        limit = explicit_count
    if limit is None:
        limit = default_limit
    return max(1, min(MAX_TOP_K_SIZE, int(limit)))


def _first_tool_text(tool_args: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        values = _normalize_text_list(tool_args.get(key))
        if values:
            return values[0]
    return ""


def _normalize_subject_activity_kind(subject_kind: Any) -> str:
    kind = str(subject_kind or "").strip().lower()
    if kind in {"person", "researcher"}:
        return "people"
    if kind in {"organization", "institution"}:
        return "org"
    if kind not in {"people", "org"}:
        raise ValueError("subject activity direct compile requires subject_kind people or org")
    return kind


def _target_cols_for_subject_activity(target: Any, perf_type: Any = None) -> List[str]:
    target_norm = str(target or "").strip().lower() or "both"
    if target_norm == "activity_history":
        target_norm = "both"
    if target_norm == "project":
        return ["ntis_project_v1"]
    if target_norm == "perf":
        return ["ntis_perf_v1"]
    if target_norm == "both":
        return ["ntis_project_v1", "ntis_perf_v1"]
    if str(perf_type or "").strip():
        return ["ntis_perf_v1"]
    return ["ntis_project_v1", "ntis_perf_v1"]


def _direct_compile_year_bounds(*, question: str, tool_args: Dict[str, Any]) -> tuple[list[str], Optional[str], Optional[str]]:
    explicit_only_hint = _build_explicit_only_hint(question)
    years = _normalize_text_list(explicit_only_hint.get("years"))
    raw_year_from = tool_args.get("year_from")
    raw_year_to = tool_args.get("year_to")
    year_from = str(raw_year_from or "").strip() or None
    year_to = str(raw_year_to or "").strip() or None
    # Only fall back to years-list when the agent supplied neither bound explicitly.
    # Mixing an explicit year_from with years[-1] as year_to collapses open-range
    # semantics like "2020년 이후" (gte=2020) into an exact-year filter (gte=2020, lte=2020).
    if year_from is None and year_to is None:
        year_from = years[0] if years else None
        year_to = years[-1] if years else None
    return years, year_from, year_to


def _subject_activity_filters(
    *,
    subject_kind: str,
    subject_name: str,
    tool_args: Dict[str, Any],
    years: List[str],
) -> Dict[str, Any]:
    filters: Dict[str, Any] = {}
    if subject_kind == "people":
        filters["participant_researcher_name"] = [subject_name]
        affiliation = _first_tool_text(
            tool_args,
            "people_affiliation_org_name",
            "affiliation_org_name",
            "affiliation",
            "org_name",
        )
        if affiliation:
            filters["people_affiliation_org_name"] = [affiliation]
    else:
        # Organization activity is anchored to participation records.
        filters["participant_org_name"] = [subject_name]
        filters["org_role"] = "participant"

    raw_year_from = tool_args.get("year_from")
    raw_year_to = tool_args.get("year_to")
    if raw_year_from not in (None, ""):
        filters["year_from"] = raw_year_from
    if raw_year_to not in (None, ""):
        filters["year_to"] = raw_year_to
    if raw_year_from in (None, "") and raw_year_to in (None, "") and years:
        filters["years"] = years

    role = _first_tool_text(tool_args, "role", "researcher_role")
    if role:
        filters["researcher_role"] = role
        filters["participant_researcher_role"] = [role]

    perf_type = _first_tool_text(tool_args, "perf_type", "performance_type")
    if perf_type:
        filters["perf_types"] = [perf_type]

    return filters


async def build_agent_subject_activity_intent_payload(
    *,
    question: str,
    conversation_id: str,
    subject_kind: str,
    subject_name: str,
    tool_args: Optional[Dict[str, Any]] = None,
    subject_ids_map: Optional[Dict[str, List[str]]] = None,
    request_id: Optional[str],
    turn_id: Optional[str],
    tool_name: str = "search_subject_activity",
    tool_execution_source: str = "agent_tool_subject_activity",
    default_limit: int = 10,
    current_context_type: Optional[str] = None,
    publication_status: Optional[str] = None,
    clarification_recovery: Optional[Dict[str, Any]] = None,
) -> tuple[Any, Any]:
    """Direct-compile an Agent-selected people/org activity lookup.

    The Dialogue Agent has already selected the tool and the subject axis. For a
    structured subject activity request, the backend should compile the execution
    contract directly instead of re-entering stagewise planner LLMs.
    """

    args = dict(tool_args or {})
    kind = _normalize_subject_activity_kind(subject_kind)
    subject = str(subject_name or "").strip()
    if not subject:
        raise ValueError("subject activity direct compile requires subject_name")

    materialized_question = str(question or "").strip() or subject
    explicit_only_hint = _build_explicit_only_hint(materialized_question)
    years, year_from, year_to = _direct_compile_year_bounds(question=materialized_question, tool_args=args)
    limit = _coerce_direct_compile_limit(question=materialized_question, tool_args=args, default_limit=default_limit)
    affiliation = _first_tool_text(args, "people_affiliation_org_name", "affiliation_org_name", "affiliation", "org_name")
    role = _first_tool_text(args, "role", "researcher_role")
    perf_type = _first_tool_text(args, "perf_type", "performance_type")
    target_cols = _target_cols_for_subject_activity(args.get("target"), perf_type)
    filters = _subject_activity_filters(subject_kind=kind, subject_name=subject, tool_args=args, years=years)
    ids_map = {
        str(key): _normalize_text_list(value)
        for key, value in dict(subject_ids_map or {}).items()
        if _normalize_text_list(value)
    }

    kws: List[str] = []
    raw_intent = classify_query_intent(materialized_question, kws, hint=explicit_only_hint)
    normalized_intent_base = normalize_intent(
        raw_intent,
        query=materialized_question,
        keywords=kws,
        hint_years=list(explicit_only_hint.get("years", [])),
        hint_perf_types=list(explicit_only_hint.get("perf_types", [])),
        hint_title_terms=list(explicit_only_hint.get("title_terms", [])),
    )
    normalized_intent_base = replace(
        normalized_intent_base,
        action="list",
        base_route=kind,
        relation=None,
        mode="lookup",
        output_type="list",
        retrieval_query=materialized_question,
        planner_limit=limit,
        years=years,
        year_from=year_from,
        year_to=year_to,
        people_terms=[subject] if kind == "people" else [],
        org_terms=[subject] if kind == "org" else [],
        participant_org_terms=[subject] if kind == "org" else [],
        org_role=("participant" if kind == "org" else ("affiliation" if affiliation else None)),
        people_affiliation_org_terms=[affiliation] if kind == "people" and affiliation else [],
        perf_types=[perf_type] if perf_type else list(explicit_only_hint.get("perf_types", [])),
        keywords=[*list(kws or []), *([role] if role else [])],
        ids_map=ids_map,
        ids_flat=[value for values in ids_map.values() for value in values],
        target_cols=target_cols,
        lookup_filter_policy="name_must",
    )
    question_analysis = QuestionAnalysis(
        mode="LOOKUP",
        head=kind,
        action="list",
        relation=None,
        join_key_mode=None,
        output_type="list",
        ids_map=ids_map,
        candidate_keys={},
        filters=filters,
        target_cols=target_cols,
        limit=limit,
        display_limit=limit,
        retrieval_query=materialized_question,
        confidence=0.95,
        hard_contract=HardContractV1(),
        soft_strategy_hints=SoftStrategyHintsV1(
            years=years,
            people_terms=[subject] if kind == "people" else [],
            org_terms=([subject] if kind == "org" else ([affiliation] if affiliation else [])),
            must_keep_terms=[subject],
            semantic_kind="subject_activity",
            org_role_hint="participant_org" if kind == "org" else ("affiliation_org" if affiliation else None),
        ),
        planner_source="direct_compile",
    )
    count_validation = _resolve_question_analysis_count(
        question_analysis,
        question=materialized_question,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    normalized_intent, planner_applied = apply_question_analysis_v3(
        normalized_intent_base,
        question_analysis,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    turn_contract = _build_turn_contract(
        normalized_intent=normalized_intent,
        question_analysis=question_analysis,
        count_validation=count_validation,
    )
    log_event(
        "AGENT.TOOL_DIRECT_COMPILE",
        request_id=request_id,
        conversation_id=conversation_id,
        tool_name=tool_name,
        subject_kind=kind,
        subject_name=subject,
        publication_status=publication_status,
        current_context_type=current_context_type,
        tool_execution_source=tool_execution_source,
        withheld_but_subject_retained=bool(publication_status == "answer_withheld_subject_retained"),
        planner_llm_skipped=1,
        mode="LOOKUP",
        action="list",
        target_cols=target_cols,
        limit=limit,
    )
    log_event(
        "PLANNER.PIPELINE",
        request_id=request_id,
        conversation_id=conversation_id,
        step="agent_subject_activity_direct_compile",
        status="success",
        front_controller="agent",
        planner_applied=int(planner_applied),
        planner_failed=0,
        planner_stagewise_enabled=0,
        planner_llm_skipped=1,
        schema_fields=["intent_payload_version", "normalized_intent", "question_analysis", "strategy_meta"],
    )
    payload = _build_intent_payload_object(
        normalized_intent,
        question_analysis,
        followup_resolution={
            "followup_resolution_status": "none",
            "explicit_followup": False,
            "followup_reference_kind": None,
        },
        turn_id=turn_id,
        turn_contract=turn_contract,
        count_validation=count_validation,
        context_router={
            "invoked": False,
            "status": "not_applicable",
            "source": tool_execution_source,
            "confidence": 0.0,
        },
        clarification_recovery=clarification_recovery,
    )
    if hasattr(payload, "strategy_meta") and isinstance(payload.strategy_meta, dict):
        payload.strategy_meta.update(
            {
                "tool_execution_source": tool_execution_source,
                "planner_llm_skipped": True,
                "direct_compile_subject_kind": kind,
                "direct_compile_subject_name": subject,
                "direct_compile_publication_status": publication_status,
            }
        )
    return payload, question_analysis


async def build_agent_people_activity_intent_payload(
    *,
    question: str,
    conversation_id: str,
    subject_name: str,
    tool_args: Optional[Dict[str, Any]] = None,
    request_id: Optional[str],
    turn_id: Optional[str],
) -> tuple[Any, Any]:
    """Backward-compatible wrapper for the legacy search_ntis_domain fast path."""

    return await build_agent_subject_activity_intent_payload(
        question=question,
        conversation_id=conversation_id,
        subject_kind="people",
        subject_name=subject_name,
        tool_args=tool_args,
        request_id=request_id,
        turn_id=turn_id,
        tool_name="search_ntis_domain",
        tool_execution_source="agent_tool_people_fast_path",
        default_limit=_DEFAULT_RETRIEVAL_LIMIT,
    )


async def build_agent_current_subject_refinement_intent_payload(
    *,
    question: str,
    conversation_id: str,
    current_context: SubjectQueryContext,
    tool_args: Optional[Dict[str, Any]] = None,
    request_id: Optional[str],
    turn_id: Optional[str],
    clarification_recovery: Optional[Dict[str, Any]] = None,
) -> tuple[Any, Any]:
    """Direct-compile a refinement against SessionMemory.current_context."""

    if not isinstance(current_context, SubjectQueryContext):
        raise ValueError("refine_current_subject requires SubjectQueryContext")
    if not bool(current_context.followup_rights.refinement_allowed):
        raise ValueError("current subject refinement is not allowed")

    args = dict(tool_args or {})
    args.setdefault("target", "activity_history")
    log_event(
        "AGENT.REFINE_CURRENT_SUBJECT",
        request_id=request_id,
        conversation_id=conversation_id,
        tool_name="refine_current_subject",
        subject_kind=current_context.subject_kind,
        subject_name=current_context.subject_name,
        identity_status=current_context.identity_status,
        publication_status=current_context.publication_status,
        current_context_type=current_context.context_type,
        tool_execution_source="agent_tool_refine_current_subject",
        withheld_but_subject_retained=bool(current_context.publication_status == "answer_withheld_subject_retained"),
        identity_ambiguous=bool(current_context.identity_status == "ambiguous_name_only"),
        planner_llm_skipped=1,
        year_from=args.get("year_from"),
        year_to=args.get("year_to"),
        affiliation_org_name=args.get("affiliation_org_name"),
        role=args.get("role"),
        target=args.get("target"),
    )
    return await build_agent_subject_activity_intent_payload(
        question=question,
        conversation_id=conversation_id,
        subject_kind=current_context.subject_kind,
        subject_name=current_context.subject_name,
        subject_ids_map=dict(current_context.subject_ids_map or {}),
        tool_args=args,
        request_id=request_id,
        turn_id=turn_id,
        tool_name="refine_current_subject",
        tool_execution_source="agent_tool_refine_current_subject",
        default_limit=10,
        current_context_type=current_context.context_type,
        publication_status=current_context.publication_status,
        clarification_recovery=clarification_recovery,
    )


async def build_agent_intent_payload(
    *,
    question: str,
    conversation_id: str,
    chat_history: List[BaseMessage],
    prev_context: List[Dict[str, Any]],
    request_id: Optional[str],
    turn_id: Optional[str],
    canonical_evidence: Optional[List[Dict[str, Any]]] = None,
    view_state: Optional[ConversationViewState] = None,
    session_memory: Optional[SessionMemory] = None,
) -> tuple[Any, Any]:
    """Build guarded planner outputs after the Dialogue Agent has selected a tool.

    This path intentionally skips the old turn front-controller. The agent already
    decided whether this turn is a new search, a current-subject refinement, or a
    clarification. The backend only compiles planner/contract truth.
    """

    explicit_only_hint = _build_explicit_only_hint(question)
    kws: List[str] = []
    raw_intent = classify_query_intent(question, kws, hint=explicit_only_hint)
    normalized_intent_base = normalize_intent(
        raw_intent,
        query=question,
        keywords=kws,
        hint_years=list(explicit_only_hint.get("years", [])),
        hint_perf_types=list(explicit_only_hint.get("perf_types", [])),
        hint_title_terms=list(explicit_only_hint.get("title_terms", [])),
    )
    active_view_state = (
        view_state_from_current_context(session_memory)
        if session_memory is not None
        else (view_state or ConversationViewState())
    )

    question_analysis = await run_question_analysis(
        question=question,
        conversation_id=conversation_id,
        chat_history=chat_history,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence or [],
        view_state=active_view_state,
        request_id=request_id,
        normalized_intent_base=normalized_intent_base,
    )
    count_validation = _resolve_question_analysis_count(
        question_analysis,
        question=question,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    normalized_intent, planner_applied = apply_question_analysis_v3(
        normalized_intent_base,
        question_analysis,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    turn_contract = _build_turn_contract(
        normalized_intent=normalized_intent,
        question_analysis=question_analysis,
        count_validation=count_validation,
    )
    log_event(
        "PLANNER.PIPELINE",
        request_id=request_id,
        conversation_id=conversation_id,
        step="agent_intent_build",
        status="success",
        front_controller="agent",
        planner_applied=int(planner_applied),
        planner_failed=0,
        planner_stagewise_enabled=int(True),
        planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
        planner_stage15_prompt_version=PLANNER_STAGE15_PROMPT_VERSION,
        planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
        schema_fields=["intent_payload_version", "normalized_intent", "question_analysis", "strategy_meta"],
    )
    payload = _build_intent_payload_object(
        normalized_intent,
        question_analysis,
        followup_resolution={
            "followup_resolution_status": "none",
            "explicit_followup": False,
            "followup_reference_kind": None,
        },
        turn_id=turn_id,
        turn_contract=turn_contract,
        count_validation=count_validation,
        context_router={
            "invoked": False,
            "status": "not_applicable",
            "source": "agent_front_controller",
            "confidence": 0.0,
        },
    )
    if hasattr(payload, "strategy_meta") and isinstance(payload.strategy_meta, dict):
        payload.strategy_meta.update(
            {
                "tool_execution_source": "agent_tool_stagewise",
                "planner_llm_skipped": False,
            }
        )
    return payload, question_analysis


async def build_intent_payload(
    *,
    question: str,
    conversation_id: str,
    chat_history: List[BaseMessage],
    prev_context: List[Dict[str, Any]],
    request_id: Optional[str],
    turn_id: Optional[str],
    canonical_evidence: Optional[List[Dict[str, Any]]] = None,
    view_state: Optional[ConversationViewState] = None,
    session_memory: Optional[SessionMemory] = None,
) -> tuple[Any, Any]:
    """DEPRECATED (ADR-0015, 2026-04-22).

    Legacy deterministic intent-building pipeline (turn_trigger → turn_interpreter →
    context_router → turn_policy). Production workflow (apps/api/workflow_builder.py)
    no longer reaches this path; the Agent front-controller uses
    `build_agent_intent_payload` instead.

    This function is preserved SOLELY because the existing test suite
    (tests/ conversation/*) still calls it and patches the underlying
    `run_turn_trigger` / `run_turn_interpreter` / `run_context_router` hooks.
    Do NOT call it from new production code. Deletion requires migrating
    those tests first (tracked as separate work).
    """
    precheck = _cheap_precheck(question)

    explicit_only_hint = _build_explicit_only_hint(question)

    kws: List[str] = []

    raw_intent = classify_query_intent(question, kws, hint=explicit_only_hint)

    normalized_intent_base = normalize_intent(

        raw_intent,

        query=question,

        keywords=kws,

        hint_years=list(explicit_only_hint.get("years", [])),

        hint_perf_types=list(explicit_only_hint.get("perf_types", [])),

        hint_title_terms=list(explicit_only_hint.get("title_terms", [])),

    )
    active_view_state = (
        view_state_from_current_context(session_memory)
        if session_memory is not None
        else (view_state or ConversationViewState())
    )
    question_analysis = None
    planner_failed = 0
    previous_turn_contract = dict(getattr(active_view_state, "last_query_contract", {}) or {})
    normalized_intent_base, clarification_recovery_meta = _recover_clarification_subject_supplement(
        question=question,
        normalized_intent_base=normalized_intent_base,
        session_memory=session_memory,
    )
    base_route = str((normalized_intent_base.get("base_route") if isinstance(normalized_intent_base, dict) else getattr(normalized_intent_base, "base_route", None)) or "project").strip().lower() or "project"
    base_ids_map = (normalized_intent_base.get("ids_map") if isinstance(normalized_intent_base, dict) else getattr(normalized_intent_base, "ids_map", None)) or {}
    source_reference_requested = parse_source_reference(question) is not None
    ordinal_reference_requested = parse_ordinal_reference(question) is not None
    has_explicit_id_seed = has_explicit_precheck_signals(precheck) or _has_ids_map_values(base_ids_map)
    has_named_subject_seed = _has_explicit_named_subject_seed(question, normalized_intent_base) or bool(clarification_recovery_meta)
    explicit_seed_kind = "named_subject" if has_named_subject_seed else ("id" if has_explicit_id_seed else None)
    has_explicit_seed = bool(has_explicit_id_seed or has_named_subject_seed)
    if clarification_recovery_meta:
        log_event(
            "TURN.CLARIFICATION_RECOVERY",
            **merge_log_fields(
                clarification_recovery_meta,
                request_id=request_id,
                conversation_id=conversation_id,
            ),
        )
    subject_refinement_kind, subject_refinement_name = (None, None)
    if not has_explicit_seed:
        subject_refinement_kind, subject_refinement_name = _resolve_subject_refinement_seed(
            question=question,
            normalized_intent_base=normalized_intent_base,
            view_state=active_view_state,
            previous_turn_contract=previous_turn_contract,
        )
        if subject_refinement_kind and subject_refinement_name:
            normalized_intent_base = _apply_subject_context_seed(
                normalized_intent_base,
                subject_kind=subject_refinement_kind,
                subject_name=subject_refinement_name,
            )
            log_event(
                "TURN.SUBJECT_REFINEMENT.SEED",
                request_id=request_id,
                conversation_id=conversation_id,
                subject_kind=subject_refinement_kind,
                subject_name=subject_refinement_name,
            )
    turn_candidates = [] if has_explicit_seed else build_turn_candidates(view_state=active_view_state)
    log_event(
        "TURN.CANDIDATES",
        request_id=request_id,
        conversation_id=conversation_id,
        candidate_count=len(turn_candidates),
        candidate_sources=[candidate.source for candidate in turn_candidates[:12]],
        has_explicit_seed=int(has_explicit_seed),
        explicit_seed_kind=explicit_seed_kind,
    )

    hard_reference_resolution: Optional[Dict[str, Any]] = None
    hard_result = resolve_hard_followup_signal(
        question=question,
        normalized_intent=normalized_intent_base,
        view_state=active_view_state,
    ) if not has_explicit_seed else None
    if not has_explicit_seed and source_reference_requested:
        reference_resolution = resolve_reference_context_followup(
            question=question,
            canonical_evidence=list(canonical_evidence or []),
            prev_context=prev_context,
            default_context_kind=base_route,
        )
        if str(reference_resolution.get("followup_resolution_status") or "").strip().lower() not in {"missing_context", "none"}:
            hard_reference_resolution = reference_resolution
            log_event(
                "TURN.HARD_SIGNAL",
                request_id=request_id,
                conversation_id=conversation_id,
                hard_signal_kind="source_reference",
                matched=1,
                action="reuse_manifest",
                reason="reference_context_source_reference",
            )
        elif hard_result is not None:
            log_event(
                "TURN.HARD_SIGNAL",
                request_id=request_id,
                conversation_id=conversation_id,
                hard_signal_kind="source_reference",
                matched=int(bool(hard_result.matched)),
                action=hard_result.action,
                reason=hard_result.reason,
            )
    elif hard_result is not None and hard_result.matched:
        log_event(
            "TURN.HARD_SIGNAL",
            request_id=request_id,
            conversation_id=conversation_id,
            hard_signal_kind=(
                "ordinal"
                if ordinal_reference_requested or str(hard_result.reason or "").startswith("relative_")
                else "explicit_id"
            ),
            matched=1,
            action=hard_result.action,
            reason=hard_result.reason,
        )

    turn_interpretation: Optional[TurnInterpretationResult]
    if hard_reference_resolution is not None:
        turn_trigger = TurnTriggerResult(
            turn_intent="followup",
            reference_style="source_reference",
            confidence=1.0,
            reason="hard_reference_context",
        )
        turn_interpretation = TurnInterpretationResult(
            chosen_action="reuse_manifest",
            selected_candidate_ids=[],
            confidence=1.0,
            reason="hard_reference_context",
            rewritten_user_intent=question,
        )
    elif hard_result is not None and hard_result.matched:
        hard_reference_style = "source_reference" if source_reference_requested else "ordinal"
        turn_trigger = TurnTriggerResult(
            turn_intent="followup",
            reference_style=hard_reference_style,
            confidence=1.0,
            reason=f"hard_signal:{hard_result.reason}",
        )
        turn_interpretation = TurnInterpretationResult(
            chosen_action=(
                "clarification"
                if str(hard_result.action or "").strip().lower() == "clarification"
                else "reuse_manifest"
                if str(hard_result.action or "").strip().lower() == "reuse_manifest"
                else "reuse_anchor"
            ),
            selected_candidate_ids=match_anchor_to_candidates(anchor=hard_result.anchor, candidates=turn_candidates),
            target_entity_kind=getattr(hard_result.anchor, "kind", None),
            confidence=1.0,
            reason=f"hard_signal:{hard_result.reason}",
            ambiguity_reason=hard_result.reason if str(hard_result.action or "").strip().lower() == "clarification" else None,
            rewritten_user_intent=question,
        )
    else:
        turn_trigger_result = run_turn_trigger(
            question=question,
            view_state=active_view_state,
            session_memory=session_memory,
            has_explicit_seed=has_explicit_seed,
            explicit_seed_kind=explicit_seed_kind,
            request_id=request_id,
            conversation_id=conversation_id,
        )
        turn_trigger = await turn_trigger_result if isawaitable(turn_trigger_result) else turn_trigger_result
        turn_interpretation_result = run_turn_interpreter(
            question=question,
            view_state=active_view_state,
            session_memory=session_memory,
            candidates=turn_candidates,
            trigger=turn_trigger,
            has_explicit_seed=has_explicit_seed,
            request_id=request_id,
            conversation_id=conversation_id,
        )
        turn_interpretation = (
            await turn_interpretation_result if isawaitable(turn_interpretation_result) else turn_interpretation_result
        )
    turn_policy = resolve_turn_policy(
        turn_trigger=turn_trigger,
        interpretation=turn_interpretation,
        view_state=active_view_state,
        session_memory=session_memory,
        has_explicit_seed=has_explicit_seed,
        explicit_seed_kind=explicit_seed_kind,
        candidates=turn_candidates,
    )
    log_event(
        "TURN.POLICY_DECISION",
        request_id=request_id,
        conversation_id=conversation_id,
        turn_intent=turn_trigger.turn_intent,
        reference_style=turn_trigger.reference_style,
        chosen_action=(turn_interpretation.chosen_action if turn_interpretation is not None else None),
        selected_candidate_ids=list((turn_interpretation.selected_candidate_ids if turn_interpretation is not None else []) or []),
        confidence=(round(float(turn_interpretation.confidence or 0.0), 3) if turn_interpretation is not None else None),
        execution_path=turn_policy.execution_path,
        blocked_reason=turn_policy.blocked_reason,
        final_execution_path=turn_policy.execution_path,
        allow_manifest_reuse=int(turn_policy.allow_manifest_reuse),
        skip_followup_resolution=int(turn_policy.skip_followup_resolution),
        previous_answer_publishability=previous_turn_contract.get("answer_publishability"),
        previous_followup_rights=previous_turn_contract.get("followup_rights"),
        memory_source=("current_context" if session_memory is not None else "view_state"),
    )
    resolution_view_state = active_view_state
    if turn_policy.execution_path == "reuse_anchor":
        resolution_view_state = set_visible_answer_manifest(active_view_state, snapshot=None)
    elif turn_policy.execution_path in {"fresh_retrieval", "clarification"}:
        resolution_view_state = _clear_active_view_scope(active_view_state)
    followup_snapshot = getattr(resolution_view_state, "visible_answer_manifest", None)
    recent_mentions = get_recent_mentions(resolution_view_state)
    context_router_meta: Dict[str, Any] = {
        "invoked": False,
        "status": "not_invoked",
        "source": "none",
        "confidence": 0.0,
        "rewritten_query_hint": None,
        "recent_mention_count": len(recent_mentions),
    }
    llm_first_resolved = bool(
        turn_interpretation is not None
        and str(turn_interpretation.reason or "").strip().lower() == "llm_first_validator_ok"
        and str(turn_interpretation.chosen_action or "").strip().lower() in {"reuse_manifest", "reuse_anchor"}
        and list(turn_interpretation.selected_candidate_ids or [])
    )
    scope_decision = resolve_scope_decision(
        question=question,
        view_state=resolution_view_state,
        normalized_intent_base=normalized_intent_base,
        strategy_meta={
            "turn_policy": turn_policy.model_dump(),
            "turn_trigger": turn_trigger.model_dump(),
            "turn_interpretation": (turn_interpretation.model_dump() if turn_interpretation is not None else {}),
            "previous_turn_contract": previous_turn_contract,
        },
    )
    scope_decision, context_router_anchor, context_router_meta = await _apply_context_router_decision(
        question=question,
        request_id=request_id,
        conversation_id=conversation_id,
        active_view_state=active_view_state,
        recent_mentions=recent_mentions,
        has_explicit_seed=has_explicit_seed,
        scope_decision=scope_decision,
        normalized_intent_base=normalized_intent_base,
        context_router_meta=context_router_meta,
        deterministic_only=llm_first_resolved,
    )
    log_event(
        "SCOPE.DECISION",
        request_id=request_id,
        conversation_id=conversation_id,
        followup_type=scope_decision.followup_type,
        turn_execution_path=turn_policy.execution_path,
        reset_requested=int(bool(scope_decision.reset_requested)),
        needs_clarification=int(bool(scope_decision.needs_clarification)),
        resolved_anchor_source=getattr(scope_decision.resolved_anchor, "source", None),
        resolved_anchor_kind=getattr(scope_decision.resolved_anchor, "kind", None),
        clarification_type=((scope_decision.clarification_payload or {}).get("clarification_type") if scope_decision.clarification_payload else None),
        clarification_reason=((scope_decision.clarification_payload or {}).get("reason") if scope_decision.clarification_payload else None),
        recent_mentions_count=context_router_meta.get("recent_mention_count"),
        context_router_status=context_router_meta.get("status"),
    )
    if scope_decision.reset_requested:
        active_view_state = _clear_active_view_scope(active_view_state)
        resolution_view_state = active_view_state
        followup_snapshot = None
        log_event(
            "ANCHOR.ESCAPED",
            request_id=request_id,
            conversation_id=conversation_id,
            reason="scope_reset",
        )
    if scope_decision.followup_type == "refinement_followup":
        log_event(
            "REFINEMENT.APPLIED",
            request_id=request_id,
            conversation_id=conversation_id,
            filters=scope_decision.refinement_filters,
        )
    anchor = None if (turn_policy.skip_followup_resolution and context_router_anchor is None) else (context_router_anchor or scope_decision.resolved_anchor)
    if (
        anchor is None
        and not turn_policy.skip_followup_resolution
        and not has_explicit_seed
        and turn_policy.selected_candidate_ids
    ):
        selected_candidate = next(
            (candidate for candidate in turn_candidates if candidate.candidate_id == turn_policy.selected_candidate_ids[0]),
            None,
        )
        if selected_candidate is not None:
            materialized_anchor = resolve_candidate_to_focus_entity(
                candidate=selected_candidate,
                view_state=active_view_state,
            )
            if materialized_anchor is not None:
                anchor = materialized_anchor
    if context_router_anchor is not None and anchor is not None:
        followup_resolution = _build_followup_resolution_from_anchor(anchor, followup_snapshot, question)
    elif turn_policy.execution_path == "clarification":
        followup_resolution = _build_policy_clarification_resolution(
            policy=turn_policy,
            trigger=turn_trigger,
            snapshot=followup_snapshot,
            question=question,
        )
    elif hard_reference_resolution is not None:
        followup_resolution = hard_reference_resolution
        anchor = None
    elif hard_result is not None and hard_result.matched and str(hard_result.action or "").strip().lower() == "clarification":
        clarification_payload = build_clarification_payload(
            question=question,
            candidates=turn_candidates,
            interpretation=turn_interpretation,
            blocked_reason=str(hard_result.reason or "hard_signal_block").strip() or "hard_signal_block",
            view_state=active_view_state,
        )
        followup_resolution = {
            "followup_resolution_status": "clarification_required",
            "selected_prev_item": None,
            "seed_map": {},
            "seed_source": None,
            "explicit_followup": True,
            "followup_reference_kind": "source_reference" if source_reference_requested else "ordinal",
            "requested_token": question,
            "requested_index": parse_source_reference(question) or parse_ordinal_reference(question),
            "available_count": len(getattr(followup_snapshot, "items", []) or []),
            "anchor_source": None,
            "focus_entity": None,
            "candidate_items": list(clarification_payload.get("candidates") or []),
            "clarification_type": clarification_payload.get("clarification_type"),
            "clarification_reason": clarification_payload.get("reason"),
            "clarification_payload": clarification_payload,
        }
    else:
        followup_resolution = _build_followup_resolution_from_anchor(anchor, followup_snapshot, question)
    followup_resolution_status = str(followup_resolution.get("followup_resolution_status") or "").strip().lower()
    if (
        anchor is None
        and not has_explicit_seed
        and not turn_policy.skip_followup_resolution
        and turn_policy.execution_path in {"reuse_manifest", "reuse_anchor"}
        and hard_reference_resolution is None
        and not (
            hard_result is not None
            and hard_result.matched
            and str(hard_result.action or "").strip().lower() == "clarification"
        )
        and followup_resolution_status not in {"resolved", "clarification_required"}
    ):
        blocked_reason = str(turn_policy.blocked_reason or "").strip() or "selected_candidate_not_materialized"
        clarification_payload = build_clarification_payload(
            question=question,
            candidates=turn_candidates,
            interpretation=turn_interpretation,
            blocked_reason=blocked_reason,
            view_state=active_view_state,
        )
        followup_resolution = {
            "followup_resolution_status": "clarification_required",
            "selected_prev_item": None,
            "seed_map": {},
            "seed_source": None,
            "explicit_followup": True,
            "followup_reference_kind": turn_trigger.reference_style,
            "requested_token": question,
            "requested_index": parse_source_reference(question) or parse_ordinal_reference(question),
            "available_count": len(getattr(followup_snapshot, "items", []) or []),
            "anchor_source": None,
            "focus_entity": None,
            "candidate_items": list(clarification_payload.get("candidates") or []),
            "clarification_type": clarification_payload.get("clarification_type"),
            "clarification_reason": clarification_payload.get("reason"),
            "clarification_payload": clarification_payload,
        }
    if (
        anchor is None
        and scope_decision.needs_clarification
        and str(followup_resolution.get("followup_resolution_status") or "").strip().lower() in {"none", "missing_context"}
    ):
        clarification_payload = dict(scope_decision.clarification_payload or {})
        followup_resolution = {
            "followup_resolution_status": "clarification_required",
            "selected_prev_item": None,
            "seed_map": {},
            "seed_source": None,
            "explicit_followup": True,
            "followup_reference_kind": "child_entity" if scope_decision.followup_type == "child_entity_followup" else scope_decision.followup_type,
            "requested_token": None,
            "requested_index": None,
            "available_count": len(getattr(followup_snapshot, "items", []) or []),
            "anchor_source": None,
            "focus_entity": None,
            "candidate_items": list(clarification_payload.get("candidates") or []),
            "clarification_type": clarification_payload.get("clarification_type"),
            "clarification_reason": clarification_payload.get("reason"),
            "clarification_payload": clarification_payload,
        }
    if (
        anchor is None
        and not has_explicit_seed
        and str(followup_resolution.get("followup_resolution_status") or "").strip().lower() != "resolved"
        and scope_decision.needs_clarification
    ):
        log_event(
            "ANCHOR.AMBIGUOUS",
            request_id=request_id,
            conversation_id=conversation_id,
            followup_type=scope_decision.followup_type,
            reason=((scope_decision.clarification_payload or {}).get("reason") if scope_decision.clarification_payload else None),
        )
    if str(followup_resolution.get("followup_resolution_status") or "").strip().lower() == "clarification_required":
        clarification_payload = dict(followup_resolution.get("clarification_payload") or {})
        log_event(
            "TURN.CLARIFICATION",
            request_id=request_id,
            conversation_id=conversation_id,
            blocked_reason=followup_resolution.get("clarification_reason") or clarification_payload.get("reason"),
            candidate_count=len(clarification_payload.get("candidates") or []),
            final_execution_path=turn_policy.execution_path,
        )

    if anchor is not None:
        normalized_intent_base = _apply_resolved_anchor_seed(normalized_intent_base, anchor)
        normalized_intent_base = _apply_followup_context_lock(normalized_intent_base, followup_resolution)
        normalized_intent_base = _apply_followup_subject_hint(
            normalized_intent_base,
            anchor=anchor,
            followup_resolution=followup_resolution,
        )
        log_event(
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
        if str(getattr(anchor, "source", "") or "").strip().lower() == "detail_participant_match":
            log_event(
                "FOLLOWUP.PARTICIPANT_ANCHOR.RESOLVED",
                request_id=request_id,
                conversation_id=conversation_id,
                source=anchor.source,
                person_no=getattr(anchor, "person_no", None),
                person_name=getattr(anchor, "title_text", None),
                source_project_pjt_id=getattr(anchor, "pjt_id", None),
                source_project_pjt_no=getattr(anchor, "pjt_no", None),
            )
        normalized_intent_base, question_analysis = _coerce_project_anchor_role_followup(
            normalized_intent_base,
            question_analysis,
            question=question,
            followup_resolution=followup_resolution,
            request_id=request_id,
            conversation_id=conversation_id,
        )
        normalized_intent_base, question_analysis = _coerce_child_anchor_detail_followup(
            normalized_intent_base,
            question_analysis,
            anchor=anchor,
            question=question,
            followup_resolution=followup_resolution,
            request_id=request_id,
            conversation_id=conversation_id,
        )
    elif not has_explicit_seed and str(followup_resolution.get("followup_resolution_status") or "") == "resolved":
        normalized_intent_base = _apply_anchor_lock(normalized_intent_base, followup_resolution.get("seed_map") or {})
        normalized_intent_base = _apply_followup_context_lock(normalized_intent_base, followup_resolution)
        normalized_intent_base = _apply_followup_subject_hint(
            normalized_intent_base,
            followup_resolution=followup_resolution,
        )
        normalized_intent_base, question_analysis = _coerce_project_anchor_role_followup(
            normalized_intent_base,
            question_analysis,
            question=question,
            followup_resolution=followup_resolution,
            request_id=request_id,
            conversation_id=conversation_id,
        )
        normalized_intent_base, question_analysis = _coerce_child_anchor_detail_followup(
            normalized_intent_base,
            question_analysis,
            question=question,
            followup_resolution=followup_resolution,
            request_id=request_id,
            conversation_id=conversation_id,
        )
    force_planner_for_explicit_seed = bool(
        has_explicit_seed
        and (
            bool(clarification_recovery_meta)
            or _should_force_planner_for_explicit_seed(
                question=question,
                normalized_intent=normalized_intent_base,
                anchor=anchor,
                followup_resolution=followup_resolution,
            )
        )
    )
    if force_planner_for_explicit_seed:
        log_event(
            "PLANNER.EXPLICIT_SEED.OVERRIDE",
            request_id=request_id,
            conversation_id=conversation_id,
            reason="subject_axis_or_broad_history",
            followup_reference_kind=followup_resolution.get("followup_reference_kind"),
            anchor_source=getattr(anchor, "source", None),
            has_subject_axis_request=int(_has_subject_axis_request(question, normalized_intent_base)),
            has_broad_history_or_list_cue=int(_has_broad_history_or_list_cue(question)),
        )
    if not has_explicit_seed or force_planner_for_explicit_seed:
        question_analysis = await run_question_analysis(
            question=question,
            conversation_id=conversation_id,
            chat_history=chat_history,

            prev_context=prev_context,

            canonical_evidence=list(canonical_evidence or []),

            view_state=resolution_view_state,

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


            request_id=request_id,

            conversation_id=conversation_id,

        )
        normalized_intent_base, question_analysis = _coerce_child_anchor_detail_followup(

            normalized_intent_base,

            question_analysis,

            anchor=anchor,

            question=question,

            followup_resolution=followup_resolution,


            request_id=request_id,

            conversation_id=conversation_id,

        )

        count_validation = _resolve_question_analysis_count(

            question_analysis,

            question=question,


            request_id=request_id,

            conversation_id=conversation_id,

        )
    else:
        count_validation = {}

    normalized_intent, planner_applied = apply_question_analysis_v3(
        normalized_intent_base,
        question_analysis,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    # Planner merge is the only final writer of route truth.
    # Post-merge facade code may preserve anchor ids, but must not relock base_route/target_cols.
    normalized_intent = (
        _apply_resolved_anchor_seed(normalized_intent, anchor)
        if anchor is not None
        else _apply_anchor_lock(
            normalized_intent,
            followup_resolution.get("seed_map") or {},
        )
    )
    normalized_intent = _apply_followup_context_lock(normalized_intent, followup_resolution)
    normalized_intent = _apply_followup_subject_hint(
        normalized_intent,
        anchor=anchor,
        followup_resolution=followup_resolution,
    )
    normalized_intent, question_analysis = _coerce_project_anchor_role_followup(
        normalized_intent,
        question_analysis,
        question=question,
        followup_resolution=followup_resolution,


        request_id=request_id,

        conversation_id=conversation_id,

    )
    normalized_intent, question_analysis = _coerce_child_anchor_detail_followup(
        normalized_intent,
        question_analysis,
        anchor=anchor,
        question=question,
        followup_resolution=followup_resolution,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    turn_contract = _build_turn_contract(
        normalized_intent=normalized_intent,
        question_analysis=question_analysis,
        count_validation=count_validation,
    )

    log_event(

        "PLANNER.PIPELINE",

        request_id=request_id,

        conversation_id=conversation_id,

        step="intent_build",

        status="success",

        planner_applied=int(planner_applied),

        planner_failed=int(planner_failed),

        planner_stagewise_enabled=int(True),
        planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
        planner_stage15_prompt_version=PLANNER_STAGE15_PROMPT_VERSION,
        planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
        schema_fields=["intent_payload_version", "normalized_intent", "question_analysis", "strategy_meta"],
    )
    return (
        _build_intent_payload_object(
            normalized_intent,
            question_analysis,
            followup_resolution=followup_resolution,
            turn_id=turn_id,
            turn_trigger=turn_trigger.model_dump(),
            turn_interpretation=(turn_interpretation.model_dump() if turn_interpretation is not None else {}),
            turn_contract=turn_contract,
            turn_policy=turn_policy.model_dump(),
            turn_candidates=[
                {
                    "candidate_id": candidate.candidate_id,
                    "source": candidate.source,
                    "entity_kind": candidate.entity_kind,
                    "display_name": candidate.display_name,
                    "display_rank": candidate.display_rank,
                }
                for candidate in turn_candidates[:12]
            ],
            count_validation=count_validation,
            context_router=context_router_meta,
            clarification_recovery=clarification_recovery_meta,
        ),
        question_analysis,
    )
