"""
대화 에이전트가 내린 결정을 실제 기술적 실행 계약(Intent Payload)으로 변환하고 실행하는 실행기입니다.

[설계 의도: L1 의도와 L2 실행 간의 Shock Absorber (ADR-0016)]
에이전트가 내린 전략적 의도(L1)를 시스템이 이해할 수 있는 구체적인 쿼리 파라미터(L2)로 
번역하는 역할을 합니다. 이 과정에서 에이전트의 실수를 보정하거나, 이전의 모호했던 문맥을 
현재의 답변과 결합하는 정교한 로직이 수행됩니다.

[주요 로직 설명]
1. Clarification Recovery (보정 복원): 
   - 왜 필요한가: 사용자가 "2023년"이라고만 답했을 때, 이전 질문인 "홍길동 연구자의 과제 알려줘"와 
     결합하여 "홍길동 연구자의 2023년 과제"라는 완전한 의도로 복원하기 위함입니다.
   - 활성화 시점: 세션 메모리에 ClarificationContext가 존재할 때, 새로운 입력을 이전 제약 조건과 병합합니다.
2. Direct Compile (직접 컴파일):
   - 왜 필요한가: 연구자 활동 기록 조회와 같이 빈번하고 명확한 패턴은 복잡한 Planner 단계를 
     거치지 않고 즉시 실행 계약을 만들어 성능을 최적화합니다.
3. Guarded Tool Execution: 에이전트가 선택한 도구의 인자가 유효한지 검증하고, Smart Coercion 원칙에 
   따라 파라미터를 정규화합니다.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from apps.api.runtime_helpers import log_event, merge_log_fields
from apps.conversation.agent_observation import AgentObservation, AgentToolExecutionResult
from apps.conversation.agent_tools import tool_spec_by_name
from apps.conversation.request_facade import (
    build_agent_current_subject_refinement_intent_payload,
    build_agent_intent_payload,
    build_agent_manifest_item_lookup_intent_payload,
    build_agent_people_activity_intent_payload,
    build_agent_subject_activity_intent_payload,
)
from apps.conversation.session_memory import (
    ClarificationContext,
    FollowupRights,
    SessionMemory,
    SubjectQueryContext,
    view_state_from_current_context,
)
from apps.conversation.view_state import ConversationViewState


_DEICTIC_TOKENS = (
    "해당 과제",
    "그 과제",
    "이 과제",
    "해당 항목",
    "그 항목",
    "이 항목",
    "해당 것",
    "그것",
    "이것",
    "그거",
    "이거",
)


def _substitute_deictic_with_title(question: str, title: str) -> str:
    """Replace the first deictic reference in question with the resolved title."""
    for token in _DEICTIC_TOKENS:
        if token in question:
            return question.replace(token, title, 1)
    return f"{title} {question}".strip()


def _resolve_entity_ref_from_manifest(entity_ref: str, session_memory: Any) -> Optional[Any]:
    """Resolve 'rank:N' or 'title:[text]' entity_ref against the current PublishedManifestContext."""
    from apps.conversation.session_memory import PublishedManifestContext
    from apps.conversation.followup_anchor import normalize_explicit_title_reference, _resolve_title_in_manifest

    ctx = getattr(session_memory, "current_context", None) if session_memory is not None else None
    if not isinstance(ctx, PublishedManifestContext):
        return None
    items = list(getattr(ctx.result_manifest, "items", None) or [])
    if not items:
        return None

    ref = str(entity_ref or "").strip()

    if ref.lower().startswith("rank:"):
        try:
            rank = int(ref.split(":", 1)[1].strip())
        except ValueError:
            return None
        for item in items:
            if item.display_rank == rank:
                return item
        if 1 <= rank <= len(items):
            return items[rank - 1]
        return None

    title_text = ref[len("title:"):].strip() if ref.lower().startswith("title:") else ref
    normalized = normalize_explicit_title_reference(title_text) or title_text
    return _resolve_title_in_manifest(normalized, items)


_PEOPLE_ACTIVITY_CUES = (
    "활동기록",
    "활동 기록",
    "활동내역",
    "활동 내역",
    "활동이력",
    "활동 이력",
    "참여이력",
    "참여 이력",
    "참여과제",
    "참여 과제",
    "참여성과",
    "참여 성과",
    "성과",
    "논문",
    "특허",
    "보고서",
    "activity",
    "history",
    "participation",
    "paper",
    "patent",
    "report",
)
_PEOPLE_AXIS_CUES = ("연구자", "연구원", "참여연구원", "참여자", "person", "people", "researcher")
_PEOPLE_ANCHOR_STOPWORDS = {"연구자", "연구원", "참여자", "참여연구원", "사람", "인물", "people", "person", "researcher"}
_PEOPLE_ANCHOR_RE = re.compile(
    r"(?P<name>[가-힣A-Za-z][가-힣A-Za-z0-9·.\-]{1,40})\s*(?:의\s*)?"
    r"(?:연구자|연구원|참여연구원|참여자)(?:의)?"
)
_LEADING_NAME_RE = re.compile(r"^\s*(?P<name>[가-힣]{2,6}|[A-Za-z][A-Za-z .\-]{1,40})\b")


def _get_state_attr(state: Any, key: str, default: Any = None) -> Any:
    return getattr(state, key, default)


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _first_arg_text(value: Any) -> Optional[str]:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return None
    return _first_text(value)


def _model_dump(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            return dict(value.model_dump())
        except Exception:
            return {}
    return dict(getattr(value, "__dict__", {}) or {})


def _coerce_positive_int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except Exception:
        return None
    return number if number >= 1 else None


def _append_filter_parts(parts: list[str], tool_args: Dict[str, Any]) -> None:
    people_name = _first_text(tool_args.get("people_name"))
    org_name = _first_text(tool_args.get("org_name"))
    role = _first_text(tool_args.get("role"))
    perf_type = _first_text(tool_args.get("perf_type"))
    target = _first_text(tool_args.get("target"))
    year_from = tool_args.get("year_from")
    year_to = tool_args.get("year_to")
    limit = tool_args.get("limit")

    for value in (people_name, org_name, target, role, perf_type):
        if value and value not in parts:
            parts.append(value)
    if year_from or year_to:
        parts.append(f"{year_from or ''}~{year_to or ''}")
    limit_value = _coerce_positive_int(limit)
    if limit_value is not None:
        parts.append(f"{limit_value}개")


def _question_from_search_args(tool_args: Dict[str, Any]) -> Optional[str]:
    query = _first_text(tool_args.get("query"))
    if not query:
        return None

    parts = [query]
    domain_head = _first_text(tool_args.get("domain_head"))
    if domain_head and domain_head != "auto":
        parts.append(domain_head)
    _append_filter_parts(parts, tool_args)
    return " ".join(parts)


def _question_from_subject_activity_args(tool_args: Dict[str, Any]) -> Optional[str]:
    subject_name = _first_text(tool_args.get("subject_name"))
    subject_kind = str(_first_text(tool_args.get("subject_kind")) or "").strip().lower()
    if not subject_name or subject_kind not in {"people", "org"}:
        return None
    query = _first_text(tool_args.get("query"))
    if query:
        parts = [query]
    else:
        label = "연구자" if subject_kind == "people" else "기관"
        parts = [f"{subject_name} {label} 활동기록"]
    _append_filter_parts(parts, {"people_name": subject_name if subject_kind == "people" else None, **tool_args})
    return " ".join(part for part in parts if str(part or "").strip())


def _query_has_people_activity_cue(query: str) -> bool:
    text = str(query or "").strip().lower()
    return any(cue.lower() in text for cue in _PEOPLE_ACTIVITY_CUES)


def _query_has_people_axis_cue(query: str) -> bool:
    text = str(query or "").strip().lower()
    return any(cue.lower() in text for cue in _PEOPLE_AXIS_CUES)


def _extract_people_activity_anchor(tool_args: Dict[str, Any]) -> Optional[str]:
    for key in (
        "people_name",
        "researcher_name",
        "participant_researcher_name",
        "participant_researcher",
        "subject_name",
    ):
        value = _first_arg_text(tool_args.get(key))
        if value:
            return value

    query = _first_text(tool_args.get("query")) or ""
    match = _PEOPLE_ANCHOR_RE.search(query)
    if match:
        name = str(match.group("name") or "").strip()
        if name and name.lower() not in _PEOPLE_ANCHOR_STOPWORDS:
            return name

    domain_head = str(_first_text(tool_args.get("domain_head")) or "").strip().lower()
    if domain_head == "people" and _query_has_people_activity_cue(query):
        leading = _LEADING_NAME_RE.search(query)
        if leading:
            name = str(leading.group("name") or "").strip()
            if name and name.lower() not in _PEOPLE_ANCHOR_STOPWORDS:
                return name
    return None


def _is_people_activity_tool_request(tool_args: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    query = _first_text(tool_args.get("query")) or ""
    if not _query_has_people_activity_cue(query):
        return False, None
    anchor = _extract_people_activity_anchor(tool_args)
    if not anchor:
        return False, None
    domain_head = str(_first_text(tool_args.get("domain_head")) or "").strip().lower()
    has_structured_people_arg = any(
        _first_arg_text(tool_args.get(key))
        for key in ("people_name", "researcher_name", "participant_researcher_name", "participant_researcher", "subject_name")
    )
    if domain_head == "people" or has_structured_people_arg or _query_has_people_axis_cue(query):
        return True, anchor
    return False, None


def _question_from_refine_args(tool_args: Dict[str, Any], state: Any) -> tuple[Optional[str], Optional[str]]:
    session_memory = _get_state_attr(state, "session_memory")
    if not isinstance(session_memory, SessionMemory):
        return None, "missing_session_memory"

    context = session_memory.current_context
    if not isinstance(context, SubjectQueryContext):
        return None, "missing_current_subject"

    if not bool(context.followup_rights.refinement_allowed):
        return None, "subject_refinement_not_allowed"

    parts = [context.subject_name]
    subject_kind = _first_text(context.subject_kind)
    if subject_kind:
        parts.append(subject_kind)
    _append_filter_parts(parts, tool_args)
    return " ".join(part for part in parts if part), None


def _is_internal_clarification_reason(reason: Any) -> bool:
    text = str(reason or "").strip().lower()
    return any(token in text for token in ("planner_error", "tool_error", "schema_error", "provider_error", "internal"))


def _first_constraint_value(constraints: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = constraints.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _looks_like_short_subject_name(value: Any) -> bool:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text or len(text) > 60:
        return False
    compact = text.replace(" ", "")
    if 2 <= len(compact) <= 6 and all("\uac00" <= char <= "\ud7a3" for char in compact):
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z .\-]{1,40}", text))


def _parse_short_subject_supplement(value: Any, constraints: Dict[str, Any]) -> Dict[str, str]:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text or len(text) > 90:
        return {}
    if any(token in text for token in ("?", "!", ",", ";", ":")):
        return {}

    name_text = text
    affiliation = ""
    if text.endswith(")") and "(" in text:
        name_text, affiliation_text = text.rsplit("(", 1)
        name_text = name_text.strip()
        affiliation = affiliation_text[:-1].strip()

    if not _looks_like_short_subject_name(name_text):
        return {}
    kind = str(constraints.get("subject_kind_hint") or "people").strip().lower() or "people"
    if kind not in {"people", "org"}:
        kind = "people"
    payload = {"subject_kind": kind, "subject_name": name_text}
    if affiliation:
        payload["affiliation_org_name"] = affiliation
    return payload


def _ids_map_from_question_analysis(question_analysis: Any) -> Dict[str, list[str]]:
    raw = getattr(question_analysis, "ids_map", None) or {}
    if not isinstance(raw, dict):
        return {}
    normalized: Dict[str, list[str]] = {}
    for key, value in raw.items():
        values = value if isinstance(value, (list, tuple, set)) else [value]
        cleaned = [str(item or "").strip() for item in values if str(item or "").strip()]
        if cleaned:
            normalized[str(key)] = cleaned
    return normalized


def _staged_identity_status(
    *,
    subject_kind: str,
    ids_map: Dict[str, list[str]],
    source_context: Optional[SubjectQueryContext],
) -> str:
    if isinstance(source_context, SubjectQueryContext) and source_context.identity_status == "resolved_with_org":
        return "resolved_with_org"
    if subject_kind == "people":
        return "resolved" if len(list(ids_map.get("person_no") or [])) == 1 else "ambiguous_name_only"
    org_candidate_count = max(
        len(list(ids_map.get("org_id") or [])),
        len(list(ids_map.get("org_code") or [])),
        len(list(ids_map.get("biz_no") or [])),
    )
    return "resolved" if org_candidate_count == 1 else "ambiguous_name_only"


def _staged_subject_context(
    *,
    subject_kind: Any,
    subject_name: Any,
    question_analysis: Any = None,
    source_context: Optional[SubjectQueryContext] = None,
) -> Optional[SubjectQueryContext]:
    kind = str(subject_kind or "").strip().lower()
    name = str(subject_name or "").strip()
    if kind not in {"people", "org"} or not name:
        return None
    ids_map = _ids_map_from_question_analysis(question_analysis)
    if source_context is not None:
        ids_map = {**dict(source_context.subject_ids_map or {}), **ids_map}
    return SubjectQueryContext(
        subject_kind=kind,
        subject_name=name,
        subject_ids_map=ids_map,
        identity_status=_staged_identity_status(
            subject_kind=kind,
            ids_map=ids_map,
            source_context=source_context,
        ),
        result_kind=str(getattr(source_context, "result_kind", None) or "project"),
        publication_status="clarification_pending",
        followup_rights=FollowupRights(refinement_allowed=True),
    )


def _apply_clarification_recovery_args(tool_args: Dict[str, Any], state: Any) -> tuple[Dict[str, Any], Dict[str, Any]]:
    session_memory = _get_state_attr(state, "session_memory")
    context = getattr(session_memory, "current_context", None) if isinstance(session_memory, SessionMemory) else None
    if not isinstance(context, ClarificationContext):
        return dict(tool_args or {}), {}
    if _is_internal_clarification_reason(context.reason):
        return dict(tool_args or {}), {}

    constraints = dict(context.unresolved_constraints or {})
    requested_refinement = dict(context.requested_refinement or {})
    if not constraints and not requested_refinement:
        return dict(tool_args or {}), {}

    recovered = dict(tool_args or {})
    years = constraints.get("years")
    if isinstance(years, (list, tuple)) and years:
        recovered.setdefault("year_from", int(years[0]) if str(years[0]).isdigit() else years[0])
        recovered.setdefault("year_to", int(years[-1]) if str(years[-1]).isdigit() else years[-1])
    for key in ("year_from", "year_to"):
        value = _first_constraint_value(constraints, key)
        if value not in (None, ""):
            recovered.setdefault(key, value)

    role = _first_constraint_value(constraints, "role", "researcher_role")
    if role:
        recovered.setdefault("role", role)

    perf_type = _first_constraint_value(constraints, "perf_type")
    if perf_type is None:
        perf_types = constraints.get("perf_types")
        if isinstance(perf_types, (list, tuple)) and perf_types:
            perf_type = perf_types[0]
    if perf_type:
        recovered.setdefault("perf_type", perf_type)

    target = _first_constraint_value(requested_refinement, "target", "result_kind") or _first_constraint_value(
        constraints, "target", "output_type"
    )
    if target:
        target_text = str(target).strip().lower()
        recovered.setdefault("target", "activity_history" if target_text in {"list", "summary"} else target)
    recovered.setdefault("target", "activity_history")

    supplement = _parse_short_subject_supplement(
        _first_text(recovered.get("subject_name"), recovered.get("people_name"), recovered.get("query")),
        constraints,
    )
    if supplement:
        recovered.setdefault("subject_kind", supplement["subject_kind"])
        recovered.setdefault("subject_name", supplement["subject_name"])
        if supplement["subject_kind"] == "people":
            recovered.setdefault("people_name", supplement["subject_name"])
        elif supplement["subject_kind"] == "org":
            recovered.setdefault("org_name", supplement["subject_name"])
        if supplement.get("affiliation_org_name"):
            recovered.setdefault("affiliation_org_name", supplement["affiliation_org_name"])

    meta = {
        "applied": True,
        "source": "clarification_context",
        "reason": context.reason,
        "unresolved_question": context.unresolved_question,
        "subject_kind": recovered.get("subject_kind"),
        "subject_name": recovered.get("subject_name"),
        "affiliation_org_name": recovered.get("affiliation_org_name"),
        "restored_fields": sorted(
            key
            for key in (
                "year_from",
                "year_to",
                "role",
                "perf_type",
                "target",
                "subject_kind",
                "subject_name",
                "affiliation_org_name",
            )
            if recovered.get(key) != dict(tool_args or {}).get(key)
        ),
    }
    return recovered, meta


def _state_log_fields(state: Any) -> Dict[str, Any]:
    return {
        "request_id": _first_text(_get_state_attr(state, "request_id")),
        "conversation_id": _first_text(_get_state_attr(state, "conversation_id")),
        "turn_id": _first_text(_get_state_attr(state, "turn_id")),
    }


async def execute_agent_tool(
    *,
    tool_name: str,
    tool_args: Dict[str, Any],
    state: Any,
) -> AgentToolExecutionResult:
    """Execute the Agent-selected tool through guarded planner/contract code."""

    name = str(tool_name or "").strip()
    args = dict(tool_args or {})
    spec = tool_spec_by_name().get(name)

    if spec is None or not spec.implemented:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="contract_violation",
                summary=f"Unsupported or unimplemented agent tool: {name}",
                warnings=["unknown_or_unimplemented_tool"],
                structured_refs={"tool_name": name},
            )
        )

    clarification_recovery_meta: Dict[str, Any] = {}
    if name in {"search_subject_activity", "refine_current_subject", "search_ntis_domain"}:
        args, clarification_recovery_meta = _apply_clarification_recovery_args(args, state)
        if name == "search_subject_activity" and str(args.get("target") or "").strip().lower() == "activity_history":
            args["target"] = "both"
        if clarification_recovery_meta:
            log_event(
                "AGENT.CLARIFICATION.RECOVERY",
                **merge_log_fields(
                    _state_log_fields(state),
                    clarification_recovery_meta,
                    tool_name=name,
                    current_context_type="clarification",
                    subject_kind=args.get("subject_kind"),
                    subject_name=args.get("subject_name") or args.get("people_name") or args.get("query"),
                ),
            )

    generated_question: Optional[str] = None
    if name == "search_ntis_domain":
        generated_question = _question_from_search_args(args)
        if not generated_question:
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="clarification_required",
                    summary="검색어가 없어 NTIS 검색을 안전하게 실행할 수 없습니다.",
                    warnings=["missing_query"],
                    structured_refs={"tool_name": name},
                )
            )
    elif name == "search_subject_activity":
        generated_question = _question_from_subject_activity_args(args)
        if not generated_question:
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="clarification_required",
                    summary="활동기록을 조회할 주체를 특정할 수 없습니다.",
                    warnings=["missing_subject_activity_args"],
                    structured_refs={"tool_name": name},
                )
            )
    elif name == "refine_current_subject":
        generated_question, error = _question_from_refine_args(args, state)
        if error:
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="clarification_required",
                    summary="현재 대화 주제를 특정할 수 없어 조건을 추가할 수 없습니다.",
                    warnings=[error],
                    structured_refs={"tool_name": name},
                )
            )
    elif name == "lookup_specific_entity":
        session_memory_for_lookup = _get_state_attr(state, "session_memory")
        manifest_item = _resolve_entity_ref_from_manifest(
            entity_ref=str(args.get("entity_ref") or ""),
            session_memory=session_memory_for_lookup,
        )
        if manifest_item is None:
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="contract_violation",
                    summary="lookup_specific_entity: 현재 발행된 목록에서 해당 항목을 찾을 수 없습니다.",
                    warnings=["manifest_item_not_found"],
                    structured_refs={"tool_name": name, "entity_ref": args.get("entity_ref")},
                )
            )
        original_question = _first_text(_get_state_attr(state, "question")) or ""
        request_clause = (
            _substitute_deictic_with_title(original_question, manifest_item.title_text)
            if original_question
            else f"{manifest_item.title_text} 상세 정보"
        )
        detail_level = str(args.get("detail_level") or "detail").strip().lower()
        try:
            intent_payload, question_analysis = await build_agent_manifest_item_lookup_intent_payload(
                item=manifest_item,
                retrieval_query=request_clause,
                conversation_id=str(_get_state_attr(state, "conversation_id", "")),
                request_id=_first_text(_get_state_attr(state, "request_id")),
                turn_id=_first_text(_get_state_attr(state, "turn_id")),
                detail_level=detail_level,
            )
        except Exception as exc:
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="error",
                    summary=f"lookup_specific_entity direct compile failed: {type(exc).__name__}: {exc}",
                    warnings=["planner_error"],
                    structured_refs={"tool_name": name, "entity_ref": args.get("entity_ref")},
                )
            )
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="planned_intent",
                summary=f"Agent tool '{name}' directly compiled a manifest item lookup.",
                structured_refs={
                    "tool_name": name,
                    "entity_ref": args.get("entity_ref"),
                    "entity_title": manifest_item.title_text,
                    "generated_question": request_clause,
                    "question_analysis": _model_dump(question_analysis),
                    "tool_execution_source": "agent_tool_manifest_item_lookup",
                },
            ),
            intent_payload=intent_payload,
            question_analysis=question_analysis,
            next_current_context=None,
        )
    elif name == "ask_user_for_clarification":
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="clarification_required",
                summary=str(args.get("question_to_user") or "").strip(),
                warnings=[],
                structured_refs={"tool_name": name, "reason": args.get("reason")},
            )
        )
    else:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="contract_violation",
                summary=f"No executor is registered for agent tool: {name}",
                warnings=["unregistered_tool_executor"],
                structured_refs={"tool_name": name},
            )
        )

    try:
        session_memory = _get_state_attr(state, "session_memory")
        view_state = _get_state_attr(state, "view_state")
        active_view_state = (
            view_state_from_current_context(session_memory)
            if session_memory
            else (view_state or ConversationViewState())
        )
        is_people_activity, people_anchor = _is_people_activity_tool_request(args) if name == "search_ntis_domain" else (False, None)
        recovery_subject_name = _first_text(args.get("subject_name"), clarification_recovery_meta.get("subject_name"))
        if name == "search_ntis_domain" and clarification_recovery_meta and recovery_subject_name:
            recovery_subject_kind = str(
                _first_text(args.get("subject_kind"), clarification_recovery_meta.get("subject_kind")) or "people"
            ).strip().lower()
            base_question = _first_text(clarification_recovery_meta.get("unresolved_question"), generated_question, args.get("query")) or ""
            materialized_question = (
                base_question
                if recovery_subject_name in base_question
                else f"{recovery_subject_name} {base_question}".strip()
            )
            intent_payload, question_analysis = await build_agent_subject_activity_intent_payload(
                question=str(materialized_question or recovery_subject_name),
                conversation_id=str(_get_state_attr(state, "conversation_id", "")),
                subject_kind=recovery_subject_kind,
                subject_name=recovery_subject_name,
                tool_args=args,
                request_id=_first_text(_get_state_attr(state, "request_id")),
                turn_id=_first_text(_get_state_attr(state, "turn_id")),
                tool_name="search_subject_activity",
                tool_execution_source="agent_clarification_recovery_subject_activity",
                default_limit=10,
                clarification_recovery=clarification_recovery_meta,
            )
            staged_context = _staged_subject_context(
                subject_kind=recovery_subject_kind,
                subject_name=recovery_subject_name,
                question_analysis=question_analysis,
            )
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="planned_intent",
                    summary="Agent clarification recovery directly compiled a subject activity lookup.",
                    structured_refs={
                        "tool_name": name,
                        "recovered_tool_name": "search_subject_activity",
                        "generated_question": materialized_question,
                        "question_analysis": _model_dump(question_analysis),
                        "tool_execution_source": "agent_clarification_recovery_subject_activity",
                    },
                ),
                intent_payload=intent_payload,
                question_analysis=question_analysis,
                next_current_context=staged_context,
            )
        if is_people_activity and people_anchor:
            materialized_question = _first_text(args.get("query")) or str(generated_question or "")
            intent_payload, question_analysis = await build_agent_people_activity_intent_payload(
                question=str(materialized_question or ""),
                conversation_id=str(_get_state_attr(state, "conversation_id", "")),
                subject_name=people_anchor,
                tool_args=args,
                request_id=_first_text(_get_state_attr(state, "request_id")),
                turn_id=_first_text(_get_state_attr(state, "turn_id")),
            )
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="planned_intent",
                    summary=f"Agent tool '{name}' directly compiled a people activity lookup.",
                    structured_refs={
                        "tool_name": name,
                        "generated_question": materialized_question,
                        "question_analysis": _model_dump(question_analysis),
                        "tool_execution_source": "agent_tool_people_fast_path",
                    },
                ),
                intent_payload=intent_payload,
                question_analysis=question_analysis,
                next_current_context=_staged_subject_context(
                    subject_kind="people",
                    subject_name=people_anchor,
                    question_analysis=question_analysis,
                ),
            )
        if name == "search_subject_activity":
            materialized_question = _first_text(args.get("query")) or str(generated_question or "")
            intent_payload, question_analysis = await build_agent_subject_activity_intent_payload(
                question=str(materialized_question or ""),
                conversation_id=str(_get_state_attr(state, "conversation_id", "")),
                subject_kind=str(args.get("subject_kind") or ""),
                subject_name=str(args.get("subject_name") or ""),
                tool_args=args,
                request_id=_first_text(_get_state_attr(state, "request_id")),
                turn_id=_first_text(_get_state_attr(state, "turn_id")),
                tool_name=name,
                tool_execution_source="agent_tool_subject_activity",
                default_limit=10,
                clarification_recovery=clarification_recovery_meta,
            )
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="planned_intent",
                    summary=f"Agent tool '{name}' directly compiled a subject activity lookup.",
                    structured_refs={
                        "tool_name": name,
                        "generated_question": materialized_question,
                        "question_analysis": _model_dump(question_analysis),
                        "tool_execution_source": "agent_tool_subject_activity",
                    },
                ),
                intent_payload=intent_payload,
                question_analysis=question_analysis,
                next_current_context=_staged_subject_context(
                    subject_kind=args.get("subject_kind"),
                    subject_name=args.get("subject_name"),
                    question_analysis=question_analysis,
                ),
            )
        if name == "refine_current_subject":
            session_memory = _get_state_attr(state, "session_memory")
            context = session_memory.current_context if isinstance(session_memory, SessionMemory) else None
            if not isinstance(context, SubjectQueryContext):
                return AgentToolExecutionResult(
                    observation=AgentObservation(
                        observation_type="clarification_required",
                        summary="현재 대화 주제를 특정할 수 없어 조건을 추가할 수 없습니다.",
                        warnings=["missing_current_subject"],
                        structured_refs={"tool_name": name},
                    )
                )
            intent_payload, question_analysis = await build_agent_current_subject_refinement_intent_payload(
                question=str(generated_question or context.subject_name),
                conversation_id=str(_get_state_attr(state, "conversation_id", "")),
                current_context=context,
                tool_args=args,
                request_id=_first_text(_get_state_attr(state, "request_id")),
                turn_id=_first_text(_get_state_attr(state, "turn_id")),
                clarification_recovery=clarification_recovery_meta,
            )
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="planned_intent",
                    summary=f"Agent tool '{name}' directly compiled a current subject refinement.",
                    structured_refs={
                        "tool_name": name,
                        "generated_question": generated_question,
                        "question_analysis": _model_dump(question_analysis),
                        "tool_execution_source": "agent_tool_refine_current_subject",
                    },
                ),
                intent_payload=intent_payload,
                question_analysis=question_analysis,
                next_current_context=_staged_subject_context(
                    subject_kind=context.subject_kind,
                    subject_name=context.subject_name,
                    question_analysis=question_analysis,
                    source_context=context,
                ),
            )
        intent_payload, question_analysis = await build_agent_intent_payload(
            question=str(generated_question or ""),
            conversation_id=str(_get_state_attr(state, "conversation_id", "")),
            chat_history=list(_get_state_attr(state, "chat_history", [])),
            prev_context=list(_get_state_attr(state, "prev_context", [])),
            canonical_evidence=list(_get_state_attr(state, "canonical_evidence", [])),
            view_state=active_view_state,
            session_memory=session_memory,
            request_id=_first_text(_get_state_attr(state, "request_id")),
            turn_id=_first_text(_get_state_attr(state, "turn_id")),
        )
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="planned_intent",
                summary=f"Agent tool '{name}' produced a guarded planner intent.",
                structured_refs={
                    "tool_name": name,
                    "generated_question": generated_question,
                    "question_analysis": _model_dump(question_analysis),
                },
            ),
            intent_payload=intent_payload,
            question_analysis=question_analysis,
        )
    except Exception as exc:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="error",
                summary=f"Agent tool planning failed: {type(exc).__name__}: {exc}",
                warnings=["planner_error"],
                structured_refs={"tool_name": name, "generated_question": generated_question},
            )
        )
