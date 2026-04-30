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
    DetailAnchorContext,
    FollowupRights,
    GroupAnchorContext,
    ProjectGroupAnchor,
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

# search_subject_activity의 subject_name으로 허용할 수 없는 지시어/대명사 패턴
_INVALID_SUBJECT_NAME_PHRASES = (
    "해당 과제의",
    "해당 과제",
    "그 과제의",
    "이 과제의",
    "해당 연구자",
    "해당 기관",
    "해당 항목",
    "앞의 과제",
    "위 과제",
    "이전 과제",
    "참여 연구자",
    "참여연구자",
    "연구자들",
    "연구원들",
    "목록",
    "상세",
    "방금",
    "저 과제",
    "아까",
)

_SUBJECT_ACTION_WORDS = frozenset({"연구자", "과제", "목록", "상세", "기관", "참여"})

# bracket/quote 제목 추출용 패턴 — resolve_project_title pre-router에서 사용
_BRACKET_TITLE_PATTERNS = (
    r'\[([^\]]{5,})\]',    # [ ... ]
    r'「([^」]{5,})」',     # 「 ... 」
    r'"([^"]{10,})"',      # " ... "
    r"'([^']{10,})'",      # ' ... '
)

_PROJECT_CUES = ("과제", "연구", "프로젝트", "사업", "찾아", "알려", "보여")


def _extract_bracket_title(question: str) -> str | None:
    """[ ] 또는 「 」 또는 10자 이상 따옴표 내 문자열에서 제목 후보를 추출합니다."""
    text = str(question or "")
    for pattern in _BRACKET_TITLE_PATTERNS:
        m = re.search(pattern, text)
        if m:
            candidate = m.group(1).strip()
            if candidate:
                return candidate
    return None


def _has_project_cue(question: str) -> bool:
    return any(cue in question for cue in _PROJECT_CUES)


def _normalize_title_for_match(title: str) -> str:
    """비교용 제목 정규화: 공백 제거, 소문자, 특수문자 제거."""
    import unicodedata
    text = str(title or "").strip()
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\s ]+", " ", text).strip()
    text = re.sub(r"[^\w\s가-힣]", "", text, flags=re.UNICODE)
    return text.lower()


def _title_similarity(a: str, b: str) -> float:
    """두 정규화된 제목 문자열의 유사도를 반환합니다."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def _extract_lead_researcher_from_doc(doc: dict) -> str | None:
    for person in (doc.get("prtcp_mp") or []):
        if "연구책임자" in str(person.get("role_slct_nm") or ""):
            return str(person.get("hm_nm") or "").strip() or None
    return None


def _normalize_person_key(hm_nm: str | None, blng_org_nm: str | None, role: str | None) -> str:
    """hm_id 없을 때 인물 dedup 키 생성."""
    parts = [str(x or "").strip() for x in (hm_nm, blng_org_nm, role)]
    return "|".join(p for p in parts if p) or "unknown"


def _read_pjt_no_from_context(session_memory: Any) -> str | None:
    """현재 세션 컨텍스트에서 pjt_no를 읽습니다."""
    if session_memory is None:
        return None
    ctx = getattr(session_memory, "current_context", None)
    if ctx is None:
        return None
    ctx_type = getattr(ctx, "context_type", "")
    if ctx_type == "group_anchor":
        return str(getattr(ctx.anchor, "pjt_no", "") or "").strip() or None
    if ctx_type == "detail_anchor":
        return str(getattr(ctx.anchor, "pjt_no", "") or "").strip() or None
    return None


def _looks_like_valid_subject_name(name: str) -> bool:
    """subject_name이 실제 인명/기관명이 아닌 지시어/대명사인지 검사합니다."""
    text = str(name or "").strip()
    if not text:
        return False
    lower = text.lower()
    for phrase in _INVALID_SUBJECT_NAME_PHRASES:
        if phrase in lower:
            return False
    for token in _DEICTIC_TOKENS:
        if token in text:
            return False
    # 8자 초과 + 동작어 포함 → 이름이 아닐 가능성 높음
    if len(text) > 8 and any(w in text for w in _SUBJECT_ACTION_WORDS):
        return False
    return True


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


_TITLE_SIMILARITY_THRESHOLD = 0.80


async def _handle_resolve_project_title(title: str, state: Any) -> "AgentToolExecutionResult":
    """결정적 과제 제목 해결. 플래너/LLM 없이 벡터 검색 → pjt_no 그룹 확정 → GroupAnchorContext 발행."""
    from time import perf_counter

    from apps.retrieval.retrieval_workflow import direct_retrieve_for_title_resolution

    started_at = perf_counter()
    try:
        docs = direct_retrieve_for_title_resolution(state=state, title=title, limit=50)
    except Exception as exc:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="error",
                summary=f"resolve_project_title search failed: {type(exc).__name__}: {exc}",
                warnings=["retrieval_error"],
                structured_refs={"tool_name": "resolve_project_title"},
            )
        )
    latency_ms = (perf_counter() - started_at) * 1000.0

    if not docs:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="no_results",
                summary=f"과제 제목 '{title}'에 해당하는 결과가 없습니다.",
                warnings=["title_no_match"],
                structured_refs={"tool_name": "resolve_project_title", "title": title},
            )
        )

    # 1. 정확 일치 우선
    normalized = _normalize_title_for_match(title)
    exact = [
        d for d in docs
        if _normalize_title_for_match(d.get("title_text") or d.get("title1") or "") == normalized
        or _normalize_title_for_match(d.get("title2") or "") == normalized
    ]

    # 2. 유사도 기반 폴백
    if not exact:
        scored = sorted(
            [
                {
                    "doc": d,
                    "score": _title_similarity(
                        normalized,
                        _normalize_title_for_match(d.get("title_text") or d.get("title1") or ""),
                    ),
                }
                for d in docs
            ],
            key=lambda x: x["score"],
            reverse=True,
        )
        top_score = scored[0]["score"] if scored else 0.0
        if top_score < _TITLE_SIMILARITY_THRESHOLD:
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="clarification_required",
                    summary=f"제목 '{title}'에 신뢰도 높은 과제를 찾을 수 없습니다.",
                    warnings=["title_no_confident_match"],
                    structured_refs={
                        "tool_name": "resolve_project_title",
                        "title": title,
                        "candidates": [s["doc"].get("title_text") for s in scored[:5]],
                    },
                )
            )
        threshold = max(_TITLE_SIMILARITY_THRESHOLD, top_score * 0.90)
        exact = [s["doc"] for s in scored if s["score"] >= threshold]

    # 3. pjt_no 기준 그룹화
    groups: dict[str, list[dict]] = {}
    ungrouped = []
    for doc in exact:
        pno = str(doc.get("pjt_no") or "").strip()
        if pno:
            groups.setdefault(pno, []).append(doc)
        else:
            ungrouped.append(doc)

    # pjt_no 없는 경우 pjt_id fallback
    if not groups and ungrouped:
        fallback_id = str(ungrouped[0].get("pjt_id") or "").strip()
        if fallback_id:
            groups[fallback_id] = ungrouped

    if not groups:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="no_results",
                summary=f"과제 제목 '{title}'로 pjt_no를 확정할 수 없습니다.",
                warnings=["pjt_no_not_found"],
                structured_refs={"tool_name": "resolve_project_title", "title": title},
            )
        )

    if len(groups) > 1:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="clarification_required",
                summary=f"'{title}' 과제 그룹이 {len(groups)}개입니다. 어느 과제를 원하시나요?",
                warnings=["multiple_pjt_no_groups"],
                structured_refs={
                    "tool_name": "resolve_project_title",
                    "title": title,
                    "group_count": len(groups),
                    "group_titles": [list(v)[0].get("title_text") for v in groups.values()],
                },
            )
        )

    # 단일 그룹 확정
    pjt_no, instances = list(groups.items())[0]
    try:
        anchor = ProjectGroupAnchor(
            pjt_no=pjt_no,
            title=instances[0].get("title_text") or title,
            pjt_ids=[str(i.get("pjt_id") or "") for i in instances if i.get("pjt_id")],
            years=sorted(
                {int(i["stan_yr"]) for i in instances if i.get("stan_yr") and str(i["stan_yr"]).isdigit()}
            ),
            lead_researcher=_extract_lead_researcher_from_doc(instances[0]),
        )
    except ValueError as exc:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="error",
                summary=f"GroupAnchor 생성 실패: {exc}",
                warnings=["anchor_validation_error"],
                structured_refs={"tool_name": "resolve_project_title", "pjt_no": pjt_no},
            )
        )

    next_ctx = GroupAnchorContext(anchor=anchor)
    log_event(
        "AGENT.RESOLVE_PROJECT_TITLE.SUCCESS",
        pjt_no=pjt_no,
        title=anchor.title,
        instance_count=len(instances),
        latency_ms=round(latency_ms, 1),
        **_state_log_fields(state),
    )
    return AgentToolExecutionResult(
        observation=AgentObservation(
            observation_type="resolved_anchor",
            summary=f"과제 그룹 확정: {anchor.title} (pjt_no={pjt_no})",
            structured_refs={
                "tool_name": "resolve_project_title",
                "pjt_no": pjt_no,
                "title": anchor.title,
                "years": anchor.years,
                "lead_researcher": anchor.lead_researcher,
                "instance_count": len(anchor.pjt_ids),
            },
        ),
        intent_payload=None,
        question_analysis=None,
        next_current_context=next_ctx,
    )


async def _handle_extract_project_participants(pjt_no: str, state: Any) -> "AgentToolExecutionResult":
    """결정적 참여인력 추출. pjt_no 전체 인스턴스에서 prtcp_mp[] 합산."""
    from time import perf_counter

    from apps.retrieval.retrieval_workflow import direct_retrieve_for_participant_extraction

    started_at = perf_counter()
    try:
        docs = direct_retrieve_for_participant_extraction(state=state, pjt_no=pjt_no, limit=30)
    except Exception as exc:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="error",
                summary=f"extract_project_participants failed: {type(exc).__name__}: {exc}",
                warnings=["retrieval_error"],
                structured_refs={"tool_name": "extract_project_participants", "pjt_no": pjt_no},
            )
        )
    latency_ms = (perf_counter() - started_at) * 1000.0

    if not docs:
        return AgentToolExecutionResult(
            observation=AgentObservation(
                observation_type="no_results",
                summary=f"pjt_no={pjt_no}에 해당하는 과제 인스턴스를 찾을 수 없습니다.",
                warnings=["pjt_no_not_found"],
                structured_refs={"tool_name": "extract_project_participants", "pjt_no": pjt_no},
            )
        )

    # prtcp_mp[] union — hm_id 기반 dedup
    participants_by_key: dict[str, dict] = {}
    for doc in docs:
        year = str(doc.get("stan_yr") or "").strip()
        pjt_id_val = str(doc.get("pjt_id") or "").strip()
        for person in (doc.get("prtcp_mp") or []):
            hm_id = str(person.get("hm_id") or "").strip()
            hm_nm = str(person.get("hm_nm") or "").strip()
            blng_org_nm = str(person.get("blng_org_nm") or "").strip()
            role = str(person.get("role_slct_nm") or "").strip()
            key = hm_id or _normalize_person_key(hm_nm, blng_org_nm, role)
            row = participants_by_key.setdefault(
                key,
                {
                    "hm_nm": hm_nm,
                    "hm_id": hm_id,
                    "role_set": set(),
                    "affiliation_set": set(),
                    "years": set(),
                    "pjt_ids": set(),
                },
            )
            if not row["hm_nm"] and hm_nm:
                row["hm_nm"] = hm_nm
            if not row["hm_id"] and hm_id:
                row["hm_id"] = hm_id
            if role:
                row["role_set"].add(role)
            if blng_org_nm:
                row["affiliation_set"].add(blng_org_nm)
            if year:
                row["years"].add(year)
            if pjt_id_val:
                row["pjt_ids"].add(pjt_id_val)

    participants = [
        {
            "hm_nm": v["hm_nm"],
            "hm_id": v["hm_id"],
            "roles": sorted(v["role_set"] - {""}),
            "affiliations": sorted(v["affiliation_set"] - {""}),
            "years": sorted(v["years"]),
            "pjt_ids": sorted(v["pjt_ids"]),
        }
        for v in participants_by_key.values()
        if v["hm_nm"]
    ]

    project_title = (docs[0].get("title_text") or "") if docs else ""
    participant_count = len(participants)
    log_event(
        "AGENT.EXTRACT_PROJECT_PARTICIPANTS.SUCCESS",
        pjt_no=pjt_no,
        project_title=project_title,
        instances_checked=len(docs),
        participant_count=participant_count,
        latency_ms=round(latency_ms, 1),
        **_state_log_fields(state),
    )
    return AgentToolExecutionResult(
        observation=AgentObservation(
            observation_type="participant_extraction",
            summary=f"pjt_no={pjt_no} 참여인력 {participant_count}명 추출",
            structured_refs={
                "tool_name": "extract_project_participants",
                "pjt_no": pjt_no,
                "project_title": project_title,
                "instances_checked": len(docs),
                "participant_count": participant_count,
                "participants": participants,
            },
        ),
        intent_payload=None,
        question_analysis=None,
        next_current_context=None,
    )


async def execute_agent_tool(
    *,
    tool_name: str,
    tool_args: Dict[str, Any],
    state: Any,
) -> AgentToolExecutionResult:
    """Execute the Agent-selected tool through guarded planner/contract code."""

    name = str(tool_name or "").strip()
    args = dict(tool_args or {})

    # Pre-router: 대괄호/따옴표 제목 + 과제 단서 감지 → resolve_project_title 오버라이드
    _original_question = _first_text(_get_state_attr(state, "question")) or ""
    _bracket_title = _extract_bracket_title(_original_question)
    if _bracket_title and _has_project_cue(_original_question) and name != "resolve_project_title":
        log_event(
            "AGENT.PRE_ROUTER.EXPLICIT_TITLE",
            original_tool=name,
            title_chars=len(_bracket_title),
            **_state_log_fields(state),
        )
        name = "resolve_project_title"
        args = {"title": _bracket_title}

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
        _subject_name_raw = str(args.get("subject_name") or args.get("people_name") or "").strip()
        if _subject_name_raw and not _looks_like_valid_subject_name(_subject_name_raw):
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="contract_violation",
                    summary=(
                        f"search_subject_activity: subject_name '{_subject_name_raw}'은(는) "
                        "실제 인명/기관명이 아닌 지시어입니다. "
                        "ask_clarification으로 실제 이름을 확인하세요."
                    ),
                    warnings=["invalid_subject_name_deictic"],
                    structured_refs={"tool_name": name, "rejected_subject_name": _subject_name_raw},
                )
            )
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
            # Recovery router: 원본 질문에 명시 제목이 있으면 resolve_project_title으로 직접 전환
            _recovery_title = _extract_bracket_title(_first_text(_get_state_attr(state, "question")) or "")
            if _recovery_title:
                log_event(
                    "AGENT.RECOVERY.TITLE_FALLBACK",
                    original_tool=name,
                    entity_ref=args.get("entity_ref"),
                    title_chars=len(_recovery_title),
                    **_state_log_fields(state),
                )
                return await _handle_resolve_project_title(_recovery_title, state)
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
    elif name == "resolve_project_title":
        _title_arg = str(args.get("title") or "").strip()
        if not _title_arg:
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="contract_violation",
                    summary="resolve_project_title: 'title' 인자가 필요합니다.",
                    warnings=["missing_title"],
                    structured_refs={"tool_name": name},
                )
            )
        return await _handle_resolve_project_title(_title_arg, state)
    elif name == "extract_project_participants":
        _pjt_no_arg = str(args.get("pjt_no") or "").strip()
        if not _pjt_no_arg:
            # 세션 컨텍스트에서 pjt_no 보충
            _session_mem_for_pjt = _get_state_attr(state, "session_memory")
            _pjt_no_arg = _read_pjt_no_from_context(_session_mem_for_pjt) or ""
        if not _pjt_no_arg:
            return AgentToolExecutionResult(
                observation=AgentObservation(
                    observation_type="contract_violation",
                    summary=(
                        "extract_project_participants: pjt_no 없음. "
                        "먼저 resolve_project_title로 GroupAnchor를 확정하십시오."
                    ),
                    warnings=["anchor_not_established"],
                    structured_refs={"tool_name": name},
                )
            )
        return await _handle_extract_project_participants(_pjt_no_arg, state)
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
