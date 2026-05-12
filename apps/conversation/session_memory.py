"""
멀티턴 대화의 핵심 상태인 '세션 메모리'와 '대화 문맥(Context)'을 정의하고 관리하는 모듈입니다.

[설계 의도: ADR-0015 주제 연속성(Subject Continuity) 보호]
본 시스템은 사용자의 질문이 모호해지거나 생략되었을 때, 이전 대화에서 언급된 '주제'를 
바탕으로 이를 복원합니다. 이를 위해 현재 대화의 성격에 따라 5가지 Context 타입을 운영합니다.

[주요 Context 타입별 상태 관리 의미]
1. PublishedManifestContext (목록 기반): 
   - 왜 저장하는가: 사용자에게 검색 결과 리스트를 보여준 상태를 기억하기 위함입니다.
   - 활성화 시점: 검색 결과 목록이 성공적으로 출력되었을 때. "3번 과제 상세 정보 보여줘"와 같은 순서 기반 요청 처리가 가능해집니다.
2. DetailAnchorContext (상세 정보 기반):
   - 왜 저장하는가: 특정 엔티티(과제, 논문 등)의 상세 페이지를 보고 있는 상태를 기억합니다.
   - 활성화 시점: 특정 항목의 상세 조회가 수행되었을 때. "이 과제의 참여 연구원은?"과 같은 후속 질문에 대응합니다.
3. SubjectQueryContext (주제 중심 기반):
   - 왜 저장하는가: 특정 연구자나 기관 등 '대화의 주인공'을 기억합니다. 결과가 없더라도 "다른 연도로 다시 찾아봐"와 같은 재시도를 지원합니다.
   - 활성화 시점: 연구자나 기관에 대한 조회가 발생했을 때. 답변 생성 실패 시에도 '주제'는 유지하여 대화의 맥락이 끊기지 않게 합니다.
4. ClarificationContext (질문 보정 기반):
   - 왜 저장하는가: 시스템이 사용자에게 추가 정보를 요청한 상태임을 기억합니다.
   - 활성화 시점: 질문이 모호하여 시스템이 되물었을 때. 사용자가 추가 정보를 주면 이전의 미완성 질문과 결합하여 실행합니다.
5. EmptyContext: 초기 상태 또는 문맥이 만료된 상태입니다.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

_logger = logging.getLogger(__name__)

from apps.conversation.view_state import (
    ActiveScope,
    ConversationViewState,
    DetailCacheEntry,
    DisplaySnapshot,
    FocusEntity,
    recent_mention_from_display_item,
    recent_mention_from_focus_entity,
)


class FollowupRights(BaseModel):
    ordinal_allowed: bool = False
    source_allowed: bool = False
    refinement_allowed: bool = False


class EmptyContext(BaseModel):
    context_type: Literal["empty"] = "empty"
    followup_rights: FollowupRights = Field(default_factory=FollowupRights)


class PublishedManifestContext(BaseModel):
    context_type: Literal["published_manifest"] = "published_manifest"
    result_kind: str = "project"
    result_manifest: DisplaySnapshot
    followup_rights: FollowupRights = Field(
        default_factory=lambda: FollowupRights(ordinal_allowed=True, source_allowed=True)
    )


class DetailAnchorContext(BaseModel):
    context_type: Literal["detail_anchor"] = "detail_anchor"
    anchor: FocusEntity
    followup_rights: FollowupRights = Field(default_factory=FollowupRights)


SubjectPublicationStatus = Literal[
    "answer_published",
    "answer_withheld_subject_retained",
    "clarification_pending",
]
SubjectIdentityStatus = Literal[
    "resolved",
    "ambiguous_name_only",
    "resolved_with_org",
]


_LEGACY_PUBLICATION_STATUS_MIGRATION: Dict[str, SubjectPublicationStatus] = {
    # 2026-04-22 이전 코드는 "publishable" 값을 썼다. ADR-0015 어휘로 정렬.
    "publishable": "answer_published",
}


class SubjectQueryContext(BaseModel):
    context_type: Literal["subject_query"] = "subject_query"
    subject_kind: str
    subject_name: str
    subject_ids_map: Dict[str, List[str]] = Field(default_factory=dict)
    identity_status: SubjectIdentityStatus = "resolved"
    result_kind: str = "project"
    result_manifest: Optional[DisplaySnapshot] = None
    # Dialogue continuity status is tracked separately from answer publication truth.
    publication_status: SubjectPublicationStatus = "answer_published"
    followup_rights: FollowupRights = Field(
        default_factory=lambda: FollowupRights(refinement_allowed=True)
    )

    @field_validator("publication_status", mode="before")
    @classmethod
    def _migrate_legacy_publication_status(cls, value: Any) -> Any:
        """구버전 SessionMemory 에 저장된 'publishable' 값을 신규 어휘로 이관."""
        if value in (None, ""):
            return "answer_published"
        if isinstance(value, str):
            return _LEGACY_PUBLICATION_STATUS_MIGRATION.get(value, value)
        return value


class ClarificationContext(BaseModel):
    context_type: Literal["clarification"] = "clarification"
    reason: Optional[str] = None
    unresolved_question: Optional[str] = None
    unresolved_constraints: Dict[str, Any] = Field(default_factory=dict)
    requested_refinement: Dict[str, Any] = Field(default_factory=dict)
    followup_rights: FollowupRights = Field(default_factory=FollowupRights)


class ProjectGroupAnchor(BaseModel):
    """pjt_no 기준 동일 과제 그룹 앵커. 확정된 pjt_no 없이는 생성 불가."""
    pjt_no: str
    title: str
    pjt_ids: List[str] = Field(default_factory=list)
    years: List[int] = Field(default_factory=list)
    lead_researcher: Optional[str] = None

    @model_validator(mode="after")
    def require_pjt_no(self) -> "ProjectGroupAnchor":
        if not self.pjt_no:
            raise ValueError("ProjectGroupAnchor requires non-empty pjt_no")
        return self


class GroupAnchorContext(BaseModel):
    """resolve_project_title 성공 후 발행되는 pjt_no 그룹 앵커 컨텍스트."""
    context_type: Literal["group_anchor"] = "group_anchor"
    anchor: ProjectGroupAnchor
    followup_rights: FollowupRights = Field(
        default_factory=lambda: FollowupRights(refinement_allowed=True)
    )


CurrentContext = Annotated[
    Union[
        EmptyContext,
        PublishedManifestContext,
        DetailAnchorContext,
        SubjectQueryContext,
        ClarificationContext,
        GroupAnchorContext,
    ],
    Field(discriminator="context_type"),
]

_CURRENT_CONTEXT_ADAPTER = TypeAdapter(CurrentContext)


class SessionMemory(BaseModel):
    schema_version: int = 3
    history_log: List[Dict[str, str]] = Field(default_factory=list)
    current_context: CurrentContext = Field(default_factory=EmptyContext)
    entity_memory: Dict[str, Any] = Field(default_factory=dict)
    detail_cache: Dict[str, DetailCacheEntry] = Field(default_factory=dict)
    turn_journal_tail: List[Dict[str, Any]] = Field(default_factory=list)
    canonical_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    render_profile: Dict[str, Any] = Field(default_factory=dict)


def empty_session_memory() -> SessionMemory:
    return SessionMemory()


def load_session_memory(payload: Any) -> SessionMemory:
    if isinstance(payload, SessionMemory):
        return payload
    if isinstance(payload, dict):
        try:
            return SessionMemory.model_validate(payload)
        except Exception as exc:
            _logger.warning("SESSION_MEMORY.PARSE_FAILED: invalid session payload, resetting to empty. error=%s", exc)
            return empty_session_memory()
    return empty_session_memory()


def load_current_context(payload: Any) -> CurrentContext:
    if payload is None:
        return EmptyContext()
    try:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        return _CURRENT_CONTEXT_ADAPTER.validate_python(payload)
    except Exception:
        return EmptyContext()


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, (list, tuple, set)):
            nested = _first_text(*list(value))
            if nested:
                return nested
            continue
        text = str(value or "").strip()
        if text:
            return text
    return None


def _as_payload(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _normalized_intent_payload(intent_payload: Any) -> Dict[str, Any]:
    normalized_intent = (
        intent_payload.get("normalized_intent")
        if isinstance(intent_payload, dict)
        else getattr(intent_payload, "normalized_intent", None)
    )
    return _as_payload(normalized_intent)


def _question_analysis_payload(intent_payload: Any) -> Dict[str, Any]:
    question_analysis = (
        intent_payload.get("question_analysis")
        if isinstance(intent_payload, dict)
        else getattr(intent_payload, "question_analysis", None)
    )
    return _as_payload(question_analysis)


def _strategy_meta_payload(intent_payload: Any) -> Dict[str, Any]:
    strategy_meta = (
        intent_payload.get("strategy_meta")
        if isinstance(intent_payload, dict)
        else getattr(intent_payload, "strategy_meta", None)
    )
    return _as_payload(strategy_meta)


def _clean_list(values: Any) -> List[str]:
    if values is None:
        return []
    iterable = values if isinstance(values, (list, tuple, set)) else [values]
    out: List[str] = []
    seen: set[str] = set()
    for value in iterable:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _normalized_subject_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split()).lower()


def _subject_name_matches(lhs: Any, rhs: Any) -> bool:
    left = _normalized_subject_text(lhs)
    right = _normalized_subject_text(rhs)
    return bool(left and right and left == right)


def _identity_keys_for_subject_kind(subject_kind: Any) -> tuple[str, ...]:
    kind = str(subject_kind or "").strip().lower()
    if kind == "people":
        return ("person_no",)
    if kind == "org":
        return ("org_id", "org_code", "biz_no")
    return ()


def _identity_candidate_count(subject_kind: Any, ids_map: Optional[Dict[str, List[str]]]) -> int:
    kind = str(subject_kind or "").strip().lower()
    normalized_ids = dict(ids_map or {})
    if kind == "people":
        return len(_clean_list(normalized_ids.get("person_no")))
    counts = [len(_clean_list(normalized_ids.get(key))) for key in _identity_keys_for_subject_kind(kind)]
    counts = [count for count in counts if count > 0]
    return max(counts) if counts else 0


def _subject_identity_resolved(identity_status: Any) -> bool:
    return str(identity_status or "").strip() in {"resolved", "resolved_with_org"}


def _merge_subject_ids_maps(*maps: Any) -> Dict[str, List[str]]:
    merged: Dict[str, List[str]] = {}
    for raw_map in maps:
        if not isinstance(raw_map, dict):
            continue
        for key, value in raw_map.items():
            cleaned = _clean_list(value)
            if not cleaned:
                continue
            existing = merged.get(str(key), [])
            merged[str(key)] = _clean_list([*existing, *cleaned])
    return merged


def _append_subject_id_value(target: Dict[str, List[str]], key: str, value: Any) -> None:
    cleaned = _clean_list(value)
    if not cleaned:
        return
    target[key] = _clean_list([*(target.get(key) or []), *cleaned])


def _append_display_item_ids(target: Dict[str, List[str]], item: Any, *, keys: tuple[str, ...]) -> None:
    for key in keys:
        _append_subject_id_value(target, key, getattr(item, key, None))


def _observed_subject_ids_from_manifest(
    *,
    subject_kind: Any,
    subject_name: Any,
    snapshot: Optional[DisplaySnapshot],
) -> Dict[str, List[str]]:
    if not isinstance(snapshot, DisplaySnapshot):
        return {}

    kind = str(subject_kind or "").strip().lower()
    name = str(subject_name or "").strip()
    if kind not in {"people", "org"} or not name:
        return {}

    observed: Dict[str, List[str]] = {}
    for item in list(snapshot.items or []):
        item_kind = str(getattr(item, "entity_kind", "") or "").strip().lower()
        if kind == "people":
            top_level_match = item_kind == "people" and (
                _subject_name_matches(getattr(item, "title_text", None), name)
                or any(_subject_name_matches(candidate, name) for candidate in list(getattr(item, "researchers", []) or []))
            )
            if top_level_match:
                _append_display_item_ids(observed, item, keys=("person_no", "org_id", "org_code", "biz_no"))
        elif kind == "org":
            top_level_match = item_kind == "org" and (
                _subject_name_matches(getattr(item, "title_text", None), name)
                or _subject_name_matches(getattr(item, "lead_org", None), name)
                or any(_subject_name_matches(candidate, name) for candidate in list(getattr(item, "participant_org", []) or []))
            )
            if top_level_match:
                _append_display_item_ids(observed, item, keys=("org_id", "org_code", "biz_no"))

        for ref in list(getattr(item, "child_refs", []) or []):
            ref_kind = str(getattr(ref, "kind", "") or "").strip().lower()
            if ref_kind != kind or not _subject_name_matches(getattr(ref, "display_name", None), name):
                continue
            observed = _merge_subject_ids_maps(observed, dict(getattr(ref, "ids_map", {}) or {}))
    return observed


def _question_analysis_ids_map(intent_payload: Any) -> Dict[str, List[str]]:
    raw_ids = _as_payload(_question_analysis_payload(intent_payload).get("ids_map"))
    return {
        str(key): _clean_list(value)
        for key, value in raw_ids.items()
        if _clean_list(value)
    }


def _subject_has_org_disambiguation_cue(
    *,
    subject_kind: Any,
    intent_payload: Any,
    staged_subject: Optional[SubjectQueryContext],
) -> bool:
    if str(subject_kind or "").strip().lower() != "people":
        return False
    if isinstance(staged_subject, SubjectQueryContext) and staged_subject.identity_status == "resolved_with_org":
        return True

    normalized_intent = _normalized_intent_payload(intent_payload)
    question_analysis = _question_analysis_payload(intent_payload)
    filters = _as_payload(question_analysis.get("filters"))
    return bool(
        _clean_list(filters.get("people_affiliation_org_name"))
        or _clean_list(filters.get("affiliation_org_name"))
        or _clean_list(normalized_intent.get("people_affiliation_org_terms"))
        or _clean_list(normalized_intent.get("people_affiliation_org_name"))
    )


def _has_explicit_identity_signal(
    *,
    subject_kind: Any,
    question_analysis_ids: Dict[str, List[str]],
) -> bool:
    return _identity_candidate_count(subject_kind, question_analysis_ids) == 1


def _resolved_subject_identity_status(
    *,
    subject_kind: Any,
    subject_ids_map: Dict[str, List[str]],
    question_analysis_ids: Dict[str, List[str]],
    intent_payload: Any,
    staged_subject: Optional[SubjectQueryContext],
) -> SubjectIdentityStatus:
    kind = str(subject_kind or "").strip().lower()
    candidate_count = _identity_candidate_count(kind, subject_ids_map)
    prior_status = (
        str(getattr(staged_subject, "identity_status", "") or "").strip() if isinstance(staged_subject, SubjectQueryContext) else ""
    )
    has_org_cue = _subject_has_org_disambiguation_cue(
        subject_kind=kind,
        intent_payload=intent_payload,
        staged_subject=staged_subject,
    )
    explicit_identity_signal = _has_explicit_identity_signal(
        subject_kind=kind,
        question_analysis_ids=question_analysis_ids,
    )

    if kind == "people":
        if has_org_cue and candidate_count == 1:
            return "resolved_with_org"
        if prior_status == "resolved_with_org" and candidate_count == 1:
            return "resolved_with_org"
        if prior_status == "ambiguous_name_only" and not (has_org_cue or explicit_identity_signal):
            return "ambiguous_name_only"
        if candidate_count == 1:
            return "resolved"
        return "ambiguous_name_only"

    if kind == "org":
        return "resolved" if candidate_count == 1 else "ambiguous_name_only"

    return "resolved"


def _single_identity_value(
    *,
    subject_kind: Any,
    identity_status: Any,
    subject_ids_map: Dict[str, List[str]],
    key: str,
) -> Optional[str]:
    if not _subject_identity_resolved(identity_status):
        return None
    if key not in _identity_keys_for_subject_kind(subject_kind):
        return None
    values = _clean_list(subject_ids_map.get(key))
    if len(values) != 1:
        return None
    return values[0]


def _infer_subject_kind_hint(question: str, normalized_intent: Dict[str, Any], strategy_meta: Dict[str, Any]) -> Optional[str]:
    turn_interpretation = _as_payload(strategy_meta.get("turn_interpretation"))
    target_kind = str(turn_interpretation.get("target_entity_kind") or "").strip().lower()
    if target_kind in {"people", "org", "project", "perf"}:
        return target_kind
    if _clean_list(normalized_intent.get("people_terms")):
        return "people"
    if _clean_list(
        normalized_intent.get("org_terms")
        or normalized_intent.get("lead_org_terms")
        or normalized_intent.get("participant_org_terms")
        or normalized_intent.get("people_affiliation_org_terms")
    ):
        return "org"
    text = str(question or "").strip().lower()
    if any(token in text for token in ("연구자", "연구원", "연구책임자", "참여연구원", "researcher", "pi")):
        return "people"
    if any(token in text for token in ("기관", "회사", "조직", "소속", "org")):
        return "org"
    return None


def _researcher_role_from_question(question: str) -> Optional[str]:
    text = str(question or "").strip().lower()
    if not text:
        return None
    if any(token in text for token in ("연구책임자", "책임자", "principal investigator", "pi")):
        return "연구책임자"
    if any(token in text for token in ("참여연구원", "참여 연구원", "참여자")):
        return "참여연구원"
    return None


def _clarification_reason_from_payload(answer_meta: Dict[str, Any], strategy_meta: Dict[str, Any]) -> Optional[str]:
    if answer_meta.get("clarification") or str(answer_meta.get("answer_kind") or "").strip().lower() == "clarification":
        return str(answer_meta.get("clarification_reason") or "clarification").strip() or "clarification"
    status = str(strategy_meta.get("followup_resolution_status") or "").strip().lower()
    clarification_payload = _as_payload(strategy_meta.get("clarification_payload"))
    if status == "clarification_required" or clarification_payload:
        return (
            str(
                clarification_payload.get("reason")
                or strategy_meta.get("clarification_reason")
                or strategy_meta.get("turn_policy_blocked_reason")
                or "clarification"
            ).strip()
            or "clarification"
        )
    count_status = str(strategy_meta.get("count_contract_validation_status") or "").strip().lower()
    if count_status == "invalid":
        return str(strategy_meta.get("count_contract_invalid_reason") or "planner_count_contract").strip()
    return None


def _is_internal_error_answer(answer_meta: Dict[str, Any], strategy_meta: Dict[str, Any]) -> bool:
    haystack = " ".join(
        str(value or "").strip().lower()
        for value in (
            answer_meta.get("answer_kind"),
            answer_meta.get("answer_source"),
            answer_meta.get("error_code"),
            answer_meta.get("error_reason"),
            strategy_meta.get("error_type"),
            strategy_meta.get("tool_execution_error"),
        )
    )
    if "error" in str(answer_meta.get("answer_kind") or "").strip().lower():
        return True
    return any(
        token in haystack
        for token in (
            "planner_error",
            "tool_error",
            "schema_error",
            "provider_error",
            "agent_internal_error",
            "internal_error",
            "llmjsonextractionerror",
        )
    )


def _build_unresolved_constraint_snapshot(
    *,
    intent_payload: Any,
    answer_meta: Dict[str, Any],
) -> tuple[Optional[str], Dict[str, Any], Dict[str, Any]]:
    normalized_intent = _normalized_intent_payload(intent_payload)
    question_analysis = _question_analysis_payload(intent_payload)
    strategy_meta = _strategy_meta_payload(intent_payload)
    filters = _as_payload(question_analysis.get("filters"))
    turn_interpretation = _as_payload(strategy_meta.get("turn_interpretation"))
    requested_refinement = _as_payload(turn_interpretation.get("requested_refinement"))
    question = _first_text(
        question_analysis.get("retrieval_query"),
        normalized_intent.get("retrieval_query"),
        strategy_meta.get("requested_token"),
    )

    constraints: Dict[str, Any] = {}
    for key in (
        "base_route",
        "action",
        "output_type",
        "year_from",
        "year_to",
        "org_role",
    ):
        value = _first_text(normalized_intent.get(key), question_analysis.get(key))
        if value:
            constraints[key] = value
    for key in (
        "years",
        "perf_types",
        "title",
        "project_tag_filters",
        "perf_tag_filters",
        "tag_filters",
    ):
        values = _clean_list(normalized_intent.get(key) or filters.get(key))
        if values:
            constraints[key] = values

    role = _researcher_role_from_question(question)
    if role:
        constraints["researcher_role"] = role
    subject_kind_hint = _infer_subject_kind_hint(question, normalized_intent, strategy_meta)
    if subject_kind_hint:
        constraints["subject_kind_hint"] = subject_kind_hint

    if answer_meta:
        constraints["answer_kind"] = str(answer_meta.get("answer_kind") or "").strip() or None
        constraints = {key: value for key, value in constraints.items() if value not in (None, "", [], {})}

    return question or None, constraints, requested_refinement


def _subject_seed_from_intent(intent_payload: Any) -> tuple[Optional[str], Optional[str]]:
    payload = _normalized_intent_payload(intent_payload)
    people = _first_text(payload.get("people_terms"))
    if people:
        return "people", people
    org = _first_text(
        payload.get("org_terms"),
        payload.get("lead_org_terms"),
        payload.get("participant_org_terms"),
        payload.get("people_affiliation_org_terms"),
    )
    if org:
        return "org", org
    return None, None


def _ids_map_from_focus(focus: FocusEntity) -> Dict[str, List[str]]:
    ids: Dict[str, List[str]] = {}
    for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn"):
        value = _first_text(getattr(focus, key, None))
        if value:
            ids[key] = [value]
    return ids


def _rights_for_manifest(followup_rights: str) -> FollowupRights:
    text = str(followup_rights or "").strip().lower()
    return FollowupRights(
        ordinal_allowed=text in {"ordinal_allowed", "source_allowed"},
        source_allowed=text == "source_allowed",
        refinement_allowed=False,
    )


def _followup_rights_label(rights: Optional[FollowupRights]) -> str:
    if not isinstance(rights, FollowupRights):
        return "none"
    if rights.source_allowed:
        return "source_allowed"
    if rights.ordinal_allowed:
        return "ordinal_allowed"
    return "none"


def _answer_publishability_from_subject_status(publication_status: Any) -> str:
    status = str(publication_status or "").strip()
    if status == "answer_withheld_subject_retained":
        return "withheld_partial"
    if status == "clarification_pending":
        return "blocked"
    return "publishable"


def _subject_continuity_retained(context: SubjectQueryContext) -> bool:
    status = str(context.publication_status or "").strip()
    return bool(context.followup_rights.refinement_allowed and status != "clarification_pending")


def _current_subject_confidence(publication_status: Any, identity_status: Any) -> float:
    status = str(publication_status or "").strip()
    if status == "answer_withheld_subject_retained":
        if str(identity_status or "").strip() == "ambiguous_name_only":
            return 0.25
        if str(identity_status or "").strip() == "resolved_with_org":
            return 0.9
        return 0.85
    if status == "clarification_pending":
        return 0.0
    if str(identity_status or "").strip() == "ambiguous_name_only":
        return 0.35
    if str(identity_status or "").strip() == "resolved_with_org":
        return 0.95
    return 1.0


def _snapshot_visible_count(snapshot: Optional[DisplaySnapshot]) -> int:
    if not isinstance(snapshot, DisplaySnapshot):
        return 0
    explicit_count = int(getattr(snapshot, "visible_count", 0) or 0)
    if explicit_count > 0:
        return explicit_count
    return len(list(getattr(snapshot, "items", []) or []))


def current_context_summary(memory: Optional[SessionMemory]) -> Dict[str, Any]:
    session = memory if isinstance(memory, SessionMemory) else empty_session_memory()
    context = session.current_context
    base: Dict[str, Any] = {
        "memory_source": "current_context",
        "current_context_type": getattr(context, "context_type", "empty"),
        "has_visible_answer_manifest": False,
        "manifest_visible_count": 0,
        "has_active_focus": False,
        "has_child_anchor": False,
        "subject_index_count": 0,
        "recent_mention_count": 0,
        "last_turn_kind": None,
        "last_answer_publishability": None,
        "last_followup_rights": None,
        "refinement_allowed": False,
        "subject_kind": None,
        "subject_name": None,
        "subject_publication_status": None,
        "subject_identity_status": None,
        "subject_identity_candidate_count": 0,
        "subject_identity_resolved": False,
        "subject_continuity_retained": False,
        "answer_publishability": None,
        "subject_refinement_allowed": False,
        "current_subject_confidence": None,
    }

    if isinstance(context, PublishedManifestContext):
        return {
            **base,
            "has_visible_answer_manifest": True,
            "manifest_visible_count": _snapshot_visible_count(context.result_manifest),
            "recent_mention_count": _snapshot_visible_count(context.result_manifest),
            "last_turn_kind": "list",
            "last_answer_publishability": "publishable",
            "answer_publishability": "publishable",
            "last_followup_rights": _followup_rights_label(context.followup_rights),
            "result_kind": context.result_kind,
        }

    if isinstance(context, SubjectQueryContext):
        refinement_allowed = bool(context.followup_rights.refinement_allowed)
        answer_publishability = _answer_publishability_from_subject_status(context.publication_status)
        continuity_retained = _subject_continuity_retained(context)
        identity_candidate_count = _identity_candidate_count(context.subject_kind, context.subject_ids_map)
        return {
            **base,
            "has_active_focus": True,
            "subject_index_count": 1,
            "recent_mention_count": 1,
            "last_turn_kind": "subject_query",
            "last_answer_publishability": answer_publishability,
            "last_followup_rights": "none",
            "refinement_allowed": refinement_allowed,
            "subject_kind": context.subject_kind,
            "subject_name": context.subject_name,
            "result_kind": context.result_kind,
            "subject_publication_status": context.publication_status,
            "subject_identity_status": context.identity_status,
            "subject_identity_candidate_count": identity_candidate_count,
            "subject_identity_resolved": _subject_identity_resolved(context.identity_status),
            "subject_continuity_retained": continuity_retained,
            "answer_publishability": answer_publishability,
            "subject_refinement_allowed": refinement_allowed,
            "current_subject_confidence": _current_subject_confidence(
                context.publication_status,
                context.identity_status,
            ),
        }

    if isinstance(context, DetailAnchorContext):
        return {
            **base,
            "has_active_focus": True,
            "recent_mention_count": 1,
            "last_turn_kind": "detail",
            "last_answer_publishability": "publishable",
            "answer_publishability": "publishable",
            "last_followup_rights": "none",
            "anchor_entity_kind": context.anchor.kind,
            "anchor_reuse_allowed": True,
        }

    if isinstance(context, ClarificationContext):
        return {
            **base,
            "last_turn_kind": "clarification",
            "last_answer_publishability": "blocked",
            "answer_publishability": "blocked",
            "last_followup_rights": "none",
            "clarification_reason": context.reason,
            "unresolved_question": context.unresolved_question,
            "unresolved_constraint_keys": sorted((context.unresolved_constraints or {}).keys()),
        }

    return base


def current_context_followup_contract(memory: Optional[SessionMemory]) -> Dict[str, Any]:
    session = memory if isinstance(memory, SessionMemory) else empty_session_memory()
    context = session.current_context
    context_type = getattr(context, "context_type", "empty")
    contract: Dict[str, Any] = {
        "memory_source": "current_context",
        "context_type": context_type,
        "previous_publishability": "not_applicable",
        "previous_followup_rights": "none",
        "has_manifest": False,
        "has_anchor_context": False,
        "anchor_reuse_allowed": False,
        "refinement_allowed": False,
        "subject_publication_status": None,
        "subject_identity_status": None,
        "subject_identity_candidate_count": 0,
        "subject_identity_resolved": False,
        "subject_continuity_retained": False,
        "answer_publishability": "not_applicable",
        "subject_refinement_allowed": False,
        "current_subject_confidence": None,
    }

    if isinstance(context, PublishedManifestContext):
        contract.update(
            {
                "previous_publishability": "publishable",
                "answer_publishability": "publishable",
                "previous_followup_rights": _followup_rights_label(context.followup_rights),
                "has_manifest": True,
            }
        )
    elif isinstance(context, SubjectQueryContext):
        answer_publishability = _answer_publishability_from_subject_status(context.publication_status)
        refinement_allowed = bool(context.followup_rights.refinement_allowed)
        contract.update(
            {
                "previous_publishability": answer_publishability,
                "previous_followup_rights": "none",
                "refinement_allowed": refinement_allowed,
                "subject_kind": context.subject_kind,
                "subject_name": context.subject_name,
                "subject_publication_status": context.publication_status,
                "subject_identity_status": context.identity_status,
                "subject_identity_candidate_count": _identity_candidate_count(
                    context.subject_kind,
                    context.subject_ids_map,
                ),
                "subject_identity_resolved": _subject_identity_resolved(context.identity_status),
                "subject_continuity_retained": _subject_continuity_retained(context),
                "answer_publishability": answer_publishability,
                "subject_refinement_allowed": refinement_allowed,
                "current_subject_confidence": _current_subject_confidence(
                    context.publication_status,
                    context.identity_status,
                ),
            }
        )
    elif isinstance(context, DetailAnchorContext):
        contract.update(
            {
                "previous_publishability": "publishable",
                "answer_publishability": "publishable",
                "previous_followup_rights": "none",
                "has_anchor_context": True,
                "anchor_reuse_allowed": True,
                "anchor_entity_kind": context.anchor.kind,
            }
        )
    elif isinstance(context, ClarificationContext):
        contract.update(
            {
                "previous_publishability": "blocked",
                "answer_publishability": "blocked",
                "previous_followup_rights": "none",
            }
        )

    return contract


def build_current_context(
    *,
    view_state: Optional[ConversationViewState],
    selected_answer_meta: Optional[Dict[str, Any]] = None,
    intent_payload: Any = None,
    staged_current_context: Any = None,
    retrieval_evidence_count: int = 0,
) -> CurrentContext:
    resolved_view_state = view_state if view_state is not None else ConversationViewState()
    staged_context = load_current_context(staged_current_context) if staged_current_context is not None else EmptyContext()
    staged_subject = staged_context if isinstance(staged_context, SubjectQueryContext) else None

    answer_meta = dict(selected_answer_meta or {})
    strategy_meta = _strategy_meta_payload(intent_payload)
    clarification_reason = _clarification_reason_from_payload(answer_meta, strategy_meta)
    publishability = str(answer_meta.get("answer_publishability") or "").strip().lower()
    visible_snapshot = getattr(resolved_view_state, "visible_answer_manifest", None)
    active_scope = getattr(resolved_view_state, "active_scope", None)
    active_snapshot = getattr(active_scope, "result_set", None)
    focus = getattr(active_scope, "focus", None) or getattr(active_scope, "child_anchor", None)
    contract = dict(getattr(resolved_view_state, "last_query_contract", {}) or {})
    followup_rights = str(contract.get("followup_rights") or "source_allowed").strip().lower()
    subject_kind, subject_name = _subject_seed_from_intent(intent_payload)
    if staged_subject is not None:
        subject_kind = subject_kind or staged_subject.subject_kind
        subject_name = subject_name or staged_subject.subject_name
    internal_error_answer = _is_internal_error_answer(answer_meta, strategy_meta)

    staged_manifest = staged_subject.result_manifest if staged_subject is not None else None
    subject_manifest = (
        active_snapshot
        if isinstance(active_snapshot, DisplaySnapshot)
        else visible_snapshot
        if isinstance(visible_snapshot, DisplaySnapshot)
        else staged_manifest
    )
    evidence_count = max(0, int(retrieval_evidence_count or 0))
    has_subject_evidence = bool(
        _snapshot_visible_count(subject_manifest)
        or _snapshot_visible_count(staged_manifest)
        or evidence_count > 0
    )
    subject_can_be_retained = (
        not internal_error_answer
        and bool(subject_kind and subject_name)
        and has_subject_evidence
        and (
            publishability == "publishable"
            or publishability in {"withheld_partial", "blocked"}
        )
    )
    if subject_can_be_retained and subject_kind and subject_name:
        publication_status: SubjectPublicationStatus = (
            "answer_published"
            if publishability == "publishable"
            else "answer_withheld_subject_retained"
        )
        manifest = subject_manifest if isinstance(subject_manifest, DisplaySnapshot) else None
        question_analysis_ids = _question_analysis_ids_map(intent_payload)
        observed_manifest_ids = _observed_subject_ids_from_manifest(
            subject_kind=subject_kind,
            subject_name=subject_name,
            snapshot=manifest,
        )
        subject_ids_map = _merge_subject_ids_maps(
            dict(staged_subject.subject_ids_map or {}) if staged_subject is not None else {},
            question_analysis_ids,
            observed_manifest_ids,
        )
        for identity_key in _identity_keys_for_subject_kind(subject_kind):
            observed_values = _clean_list(observed_manifest_ids.get(identity_key))
            if observed_values:
                subject_ids_map[identity_key] = observed_values
        identity_status = _resolved_subject_identity_status(
            subject_kind=subject_kind,
            subject_ids_map=subject_ids_map,
            question_analysis_ids=question_analysis_ids,
            intent_payload=intent_payload,
            staged_subject=staged_subject,
        )
        if publication_status == "answer_published" and identity_status == "ambiguous_name_only":
            publication_status = "answer_withheld_subject_retained"
        return SubjectQueryContext(
            subject_kind=subject_kind,
            subject_name=subject_name,
            subject_ids_map=subject_ids_map,
            identity_status=identity_status,
            result_kind=str(
                getattr(manifest, "context_kind", None)
                or getattr(staged_subject, "result_kind", None)
                or contract.get("context_kind")
                or "project"
            ),
            result_manifest=manifest,
            publication_status=publication_status,
            followup_rights=FollowupRights(
                refinement_allowed=True,
                ordinal_allowed=isinstance(manifest, DisplaySnapshot) and _snapshot_visible_count(manifest) > 0,
                source_allowed=isinstance(manifest, DisplaySnapshot) and _snapshot_visible_count(manifest) > 0,
            ),
        )

    if publishability == "publishable" and isinstance(visible_snapshot, DisplaySnapshot):
        return PublishedManifestContext(
            result_kind=str(getattr(visible_snapshot, "context_kind", None) or contract.get("context_kind") or "project"),
            result_manifest=visible_snapshot,
            followup_rights=_rights_for_manifest(followup_rights),
        )

    if publishability == "publishable" and isinstance(focus, FocusEntity):
        return DetailAnchorContext(anchor=focus)

    if clarification_reason and not internal_error_answer:
        unresolved_question, unresolved_constraints, requested_refinement = _build_unresolved_constraint_snapshot(
            intent_payload=intent_payload,
            answer_meta=answer_meta,
        )
        return ClarificationContext(
            reason=clarification_reason,
            unresolved_question=unresolved_question,
            unresolved_constraints=unresolved_constraints,
            requested_refinement=requested_refinement,
        )

    return EmptyContext()


def build_session_memory(
    *,
    history_log: List[Dict[str, str]],
    canonical_evidence: Any,
    render_profile: Any,
    view_state: Optional[ConversationViewState],
    selected_answer_meta: Optional[Dict[str, Any]] = None,
    intent_payload: Any = None,
    current_context: Any = None,
    turn_journal_tail: Optional[List[Dict[str, Any]]] = None,
) -> SessionMemory:
    normalized_view_state = view_state if isinstance(view_state, ConversationViewState) else ConversationViewState()
    resolved_current_context = load_current_context(current_context)
    return SessionMemory(
        history_log=list(history_log or []),
        current_context=resolved_current_context,
        entity_memory={},
        detail_cache=dict(getattr(normalized_view_state, "detail_cache", {}) or {}),
        turn_journal_tail=list(turn_journal_tail or [])[-20:],
        canonical_evidence=[item for item in (canonical_evidence or []) if isinstance(item, dict)],
        render_profile=dict(render_profile or {}) if isinstance(render_profile, dict) else {},
    )


def view_state_from_current_context(memory: Optional[SessionMemory]) -> ConversationViewState:
    session = memory if isinstance(memory, SessionMemory) else empty_session_memory()
    context = session.current_context
    view_state = ConversationViewState(detail_cache=dict(session.detail_cache or {}))

    if isinstance(context, PublishedManifestContext):
        mentions = [
            recent_mention_from_display_item(item, source="list_snapshot", turn_index=idx)
            for idx, item in enumerate(context.result_manifest.items[:12])
        ]
        return view_state.model_copy(
            update={
                "visible_answer_manifest": context.result_manifest,
                "active_scope": ActiveScope(result_set=context.result_manifest, scope_kind="list"),
                "active_result_set_kind": context.result_kind,
                "active_result_view_id": context.result_manifest.view_id,
                "recent_mentions": mentions,
                "last_query_contract": {
                    "turn_kind": "list",
                    "context_kind": context.result_kind,
                    "answer_publishability": "publishable",
                    "followup_rights": "source_allowed" if context.followup_rights.source_allowed else "ordinal_allowed",
                },
            }
        )

    if isinstance(context, SubjectQueryContext):
        publication_status = str(context.publication_status or "").strip()
        # SubjectQueryContext.publication_status (ADR-0015 어휘) 를
        # last_query_contract.answer_publishability (기존 파이프라인 어휘) 로 매핑.
        answer_publishability = _answer_publishability_from_subject_status(publication_status)
        refinement_allowed = bool(context.followup_rights.refinement_allowed)
        focus = FocusEntity(
            kind=context.subject_kind,
            source="subject_query_context",
            title_text=context.subject_name,
            person_no=_single_identity_value(
                subject_kind=context.subject_kind,
                identity_status=context.identity_status,
                subject_ids_map=context.subject_ids_map,
                key="person_no",
            ),
            org_id=_single_identity_value(
                subject_kind=context.subject_kind,
                identity_status=context.identity_status,
                subject_ids_map=context.subject_ids_map,
                key="org_id",
            ),
            org_code=_single_identity_value(
                subject_kind=context.subject_kind,
                identity_status=context.identity_status,
                subject_ids_map=context.subject_ids_map,
                key="org_code",
            ),
            biz_no=_single_identity_value(
                subject_kind=context.subject_kind,
                identity_status=context.identity_status,
                subject_ids_map=context.subject_ids_map,
                key="biz_no",
            ),
        )
        mentions = [recent_mention_from_focus_entity(focus, source="detail_focus")]
        # 차단 또는 보류 상태에서도 manifest 자체는 살려서 후속 turn에 ordinal 참조 가능하도록 view_state에 펼친다.
        # ordinal_allowed 자체는 followup_rights 에서 별도 결정되며, manifest 항목 ID 가시성은 별 신호다.
        manifest_snapshot = context.result_manifest if isinstance(context.result_manifest, DisplaySnapshot) else None
        manifest_visible = _snapshot_visible_count(manifest_snapshot) if manifest_snapshot is not None else 0
        ordinal_allowed_in_subject = bool(context.followup_rights.ordinal_allowed)
        update_fields: Dict[str, Any] = {
            "active_scope": ActiveScope(focus=focus, scope_kind="detail"),
            "recent_mentions": mentions,
            "last_query_contract": {
                "turn_kind": "subject_query",
                "context_kind": context.result_kind,
                "subject_kind": context.subject_kind,
                "subject_name": context.subject_name,
                "answer_publishability": answer_publishability,
                "publication_status": publication_status or answer_publishability,
                "subject_publication_status": publication_status or "answer_published",
                "subject_identity_status": context.identity_status,
                "subject_identity_candidate_count": _identity_candidate_count(
                    context.subject_kind,
                    context.subject_ids_map,
                ),
                "subject_identity_resolved": _subject_identity_resolved(context.identity_status),
                "subject_continuity_retained": _subject_continuity_retained(context),
                "followup_rights": "ordinal_allowed" if ordinal_allowed_in_subject else "none",
                "refinement_allowed": refinement_allowed,
                "subject_refinement_allowed": refinement_allowed,
                "current_subject_confidence": _current_subject_confidence(
                    publication_status,
                    context.identity_status,
                ),
                "subject_manifest_visible_count": int(manifest_visible),
                "subject_manifest_ordinal_allowed": ordinal_allowed_in_subject,
            },
        }
        if manifest_snapshot is not None and manifest_visible > 0:
            list_mentions = [
                recent_mention_from_display_item(item, source="list_snapshot", turn_index=idx)
                for idx, item in enumerate(manifest_snapshot.items[:12])
            ]
            update_fields["visible_answer_manifest"] = manifest_snapshot
            update_fields["active_scope"] = ActiveScope(
                result_set=manifest_snapshot,
                focus=focus,
                scope_kind="list",
            )
            update_fields["active_result_set_kind"] = context.result_kind
            update_fields["active_result_view_id"] = manifest_snapshot.view_id
            update_fields["recent_mentions"] = [*mentions, *list_mentions]
        return view_state.model_copy(update=update_fields)

    if isinstance(context, DetailAnchorContext):
        return view_state.model_copy(
            update={
                "active_scope": ActiveScope(focus=context.anchor, scope_kind="detail"),
                "recent_mentions": [recent_mention_from_focus_entity(context.anchor, source="detail_focus")],
                "last_query_contract": {
                    "turn_kind": "detail",
                    "context_kind": context.anchor.kind,
                    "answer_publishability": "publishable",
                    "followup_rights": "none",
                },
            }
        )

    if isinstance(context, GroupAnchorContext):
        focus = FocusEntity(
            kind="project",
            source="group_anchor_context",
            title_text=context.anchor.title,
            pjt_no=context.anchor.pjt_no,
        )
        refinement_allowed = bool(context.followup_rights.refinement_allowed)
        return view_state.model_copy(
            update={
                "active_scope": ActiveScope(focus=focus, scope_kind="detail"),
                "recent_mentions": [recent_mention_from_focus_entity(focus, source="detail_focus")],
                "last_query_contract": {
                    "turn_kind": "group_anchor",
                    "context_kind": "project",
                    "answer_publishability": "publishable",
                    "followup_rights": "refinement_allowed" if refinement_allowed else "none",
                    "refinement_allowed": refinement_allowed,
                },
            }
        )

    return view_state
