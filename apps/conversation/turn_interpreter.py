from __future__ import annotations

import json
import os
from typing import Any, Dict, Literal, Optional

from apps.platform.langchain_compat import ChatPromptTemplate, PydanticOutputParser, SystemMessage
from pydantic import BaseModel, Field

from apps.api.runtime_helpers import log_event
from apps.chat.llm_json import sanitize_llm_json
from apps.chat.llm_runtime import build_llm, load_prompt_file
from apps.conversation.clarification_prose import compose_clarification_message
from apps.conversation.entity_registry import detect_entity_kind_from_text, normalize_entity_kind
from apps.conversation.followup_anchor import (
    is_referential_followup,
    parse_ordinal_reference,
    parse_relative_reference,
    parse_source_reference,
)
from apps.conversation.session_memory import SessionMemory, current_context_summary
from apps.conversation.turn_trigger import TurnTriggerResult
from apps.conversation.view_state import (
    ConversationViewState,
    FocusEntity,
    SubjectIndexEntry,
    focus_entity_from_item,
    get_active_child_anchor,
    get_active_focus_entity,
    get_recent_mentions,
)
from apps.planner.planner_defaults import PLANNER_DISABLE_THINKING, PLANNER_TEMPERATURE
from apps.planner.prompt_asset_paths import planner_prompt_path

CandidateSource = Literal[
    "manifest_item",
    "focus_entity",
    "child_anchor",
    "subject_index",
    "recent_mention",
]
EntityKind = str
ChosenAction = Literal[
    "reuse_manifest",
    "reuse_anchor",
    "fresh_retrieval",
    "clarification",
]

_MAX_CANDIDATES_FOR_PROMPT = 16
_LLM_FIRST_VALIDATOR_TOP_K = 5
_INTERPRETER_LOW_CONFIDENCE = 0.45
_IDENTITY_ID_KEYS = ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn")
_HEURISTIC_DIRECT_REASONS = {
    "named_candidate_heuristic",
    "relative_candidate_heuristic",
    "single_anchor_context",
    "single_filtered_candidate",
    "single_same_kind_candidate_auto",
}
_SOURCE_PRIORITY = {
    "child_anchor": 0,
    "focus_entity": 1,
    "manifest_item": 2,
    "recent_mention": 3,
    "subject_index": 4,
}
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "y", "on"}
_RESEARCHER_ROLE_REFINEMENTS = (
    ("연구책임자", "principal_investigator"),
    ("책임자", "principal_investigator"),
    ("principal investigator", "principal_investigator"),
    ("pi", "principal_investigator"),
    ("참여연구원", "participant_researcher"),
    ("참여 연구원", "participant_researcher"),
    ("참여자", "participant_researcher"),
)


class TurnCandidate(BaseModel):
    candidate_id: str
    source: CandidateSource
    entity_kind: EntityKind
    subject_id: Optional[str] = None
    display_name: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    ids_map: dict[str, list[str]] = Field(default_factory=dict)
    parent_subject_ids: list[str] = Field(default_factory=list)
    display_rank: Optional[int] = None
    turn_index: Optional[int] = None
    view_id: Optional[str] = None
    resolution_state: Literal["resolved", "provisional"] = "resolved"


class RequestedRefinement(BaseModel):
    entity_kind_hint: Optional[str] = None
    source_filter: Optional[str] = None
    time_filter: Optional[str] = None
    ordinal_hint: Optional[int] = None
    relative_position: Optional[Literal["first", "last"]] = None
    top_k: Optional[int] = None
    sort_key: Optional[str] = None
    sort_dir: Optional[str] = None


class TurnInterpretationResult(BaseModel):
    chosen_action: ChosenAction
    selected_candidate_ids: list[str] = Field(default_factory=list)
    target_entity_kind: Optional[str] = None
    requested_refinement: RequestedRefinement = Field(default_factory=RequestedRefinement)
    rewritten_user_intent: Optional[str] = None
    ambiguity_reason: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = "llm_interpretation"


class HardResolutionResult(BaseModel):
    matched: bool = False
    action: Optional[str] = None
    anchor: Optional[FocusEntity] = None
    reason: Optional[str] = None


class ClarificationSuggestion(BaseModel):
    label: str
    candidate_id: Optional[str] = None
    entity_kind: Optional[str] = None
    # ADR-0014 Scope A: disambiguator와 aux는 동명 후보 구분을 위한 부가 정보.
    # None/빈 값이면 직렬화에서 제거된다 (기존 API 응답 포맷과의 호환을 위해).
    disambiguator: Optional[str] = None
    aux: Dict[str, Any] = Field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        """None/빈 aux는 생략하여 기존 payload와 동치 유지."""
        payload: Dict[str, Any] = {"label": self.label}
        if self.candidate_id:
            payload["candidate_id"] = self.candidate_id
        if self.entity_kind:
            payload["entity_kind"] = self.entity_kind
        if self.disambiguator:
            payload["disambiguator"] = self.disambiguator
        if self.aux:
            payload["aux"] = dict(self.aux)
        return payload


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()


def turn_interpretation_llm_first_enabled() -> bool:
    return _normalized_text(os.getenv("TURN_INTERPRETATION_LLM_FIRST", "0")).lower() in _TRUTHY_ENV_VALUES


def _normalize_entity_kind(value: Any) -> EntityKind:
    return normalize_entity_kind(value, default="project")


def _normalize_ids_map(values: Any) -> Dict[str, list[str]]:
    if not isinstance(values, dict):
        return {}
    normalized: Dict[str, list[str]] = {}
    for key, raw_values in values.items():
        if isinstance(raw_values, str):
            raw_iter = [raw_values]
        elif isinstance(raw_values, (list, tuple, set)):
            raw_iter = list(raw_values)
        else:
            raw_iter = [raw_values]
        deduped: list[str] = []
        seen: set[str] = set()
        for raw in raw_iter:
            text = _normalized_text(raw)
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        if deduped:
            normalized[_normalized_text(key)] = deduped
    return normalized


def _dedupe_texts(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalized_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _focus_ids_map(entity: Optional[FocusEntity]) -> Dict[str, list[str]]:
    if entity is None:
        return {}
    return _normalize_ids_map(
        {
            "pjt_id": entity.pjt_id,
            "pjt_no": entity.pjt_no,
            "rst_id": entity.rst_id,
            "person_no": entity.person_no,
            "org_id": entity.org_id,
            "org_code": entity.org_code,
            "biz_no": entity.biz_no,
            "doi": entity.doi,
            "issn": entity.issn,
        }
    )


def _recent_mention_ids_map(mention: Any) -> Dict[str, list[str]]:
    return _normalize_ids_map(
        {
            "pjt_id": getattr(mention, "pjt_id", None),
            "pjt_no": getattr(mention, "pjt_no", None),
            "rst_id": getattr(mention, "rst_id", None),
            "doi": getattr(mention, "doi", None),
            "issn": getattr(mention, "issn", None),
        }
    )


def _identity_key(*, ids_map: Dict[str, list[str]], subject_id: Optional[str], display_name: Optional[str]) -> str:
    for key in _IDENTITY_ID_KEYS:
        values = ids_map.get(key) or []
        if values:
            return f"{key}:{values[0]}"
    subject = _normalized_text(subject_id)
    if subject:
        return f"subject:{subject}"
    name = _normalized_text(display_name).lower()
    if name:
        return f"name:{name}"
    return "candidate"


def _candidate_id(*, source: CandidateSource, ids_map: Dict[str, list[str]], subject_id: Optional[str], display_name: Optional[str]) -> str:
    return f"{source}:{_identity_key(ids_map=ids_map, subject_id=subject_id, display_name=display_name)}"


def _candidate_sort_key(candidate: TurnCandidate) -> tuple[int, int, int, int]:
    return (
        _SOURCE_PRIORITY.get(candidate.source, 99),
        int(candidate.display_rank or 9999),
        -int(candidate.turn_index or 0),
        0 if candidate.resolution_state == "resolved" else 1,
    )


def _compact_candidate(candidate: TurnCandidate) -> dict[str, Any]:
    payload = {
        "candidate_id": candidate.candidate_id,
        "source": candidate.source,
        "entity_kind": candidate.entity_kind,
        "subject_id": candidate.subject_id,
        "display_name": candidate.display_name,
        "aliases": list(candidate.aliases or []),
        "ids_map": dict(candidate.ids_map or {}),
        "parent_subject_ids": list(candidate.parent_subject_ids or []),
        "display_rank": candidate.display_rank,
        "turn_index": candidate.turn_index,
        "view_id": candidate.view_id,
        "resolution_state": candidate.resolution_state,
    }
    return {key: value for key, value in payload.items() if value not in (None, [], {}, "")}


def _candidate_matches_name(question: str, candidate: TurnCandidate) -> bool:
    text = _normalized_text(question)
    if not text:
        return False
    terms = [candidate.display_name, *(candidate.aliases or [])]
    return any(term and _normalized_text(term) in text for term in terms)


def _question_entity_kind(question: str) -> Optional[EntityKind]:
    return detect_entity_kind_from_text(question)


def _filtered_candidates_for_question(question: str, candidates: list[TurnCandidate]) -> list[TurnCandidate]:
    target_kind = _question_entity_kind(question)
    if target_kind is None:
        return list(candidates)
    filtered = [candidate for candidate in candidates if normalize_entity_kind(candidate.entity_kind, default="") == target_kind]
    return filtered


def _select_prompt_candidates(
    question: str,
    candidates: list[TurnCandidate],
    *,
    limit: int = _MAX_CANDIDATES_FOR_PROMPT,
) -> list[TurnCandidate]:
    filtered = _filtered_candidates_for_question(question, candidates)
    if limit <= 0 or not filtered:
        return []
    text = _normalized_text(question)

    def score(candidate: TurnCandidate) -> tuple[int, int, int, int]:
        name_match = 0 if _candidate_matches_name(text, candidate) else 1
        source_rank = _SOURCE_PRIORITY.get(candidate.source, 99)
        display_rank = int(candidate.display_rank or 9999)
        recency_rank = -int(candidate.turn_index or 0)
        return (name_match, source_rank, display_rank, recency_rank)

    return sorted(filtered, key=score)[:limit]


def _relative_candidates(question: str, candidates: list[TurnCandidate]) -> list[TurnCandidate]:
    relative = parse_relative_reference(question)
    if relative is None:
        return []
    filtered = _filtered_candidates_for_question(question, candidates)
    if not filtered:
        return []
    recent = [candidate for candidate in filtered if candidate.source == "recent_mention"]
    manifest = [candidate for candidate in filtered if candidate.source == "manifest_item"]
    ordered = recent or manifest or filtered
    ordered = sorted(
        ordered,
        key=lambda candidate: (
            int(candidate.turn_index or 0),
            int(candidate.display_rank or 0),
            _SOURCE_PRIORITY.get(candidate.source, 99),
        ),
    )
    position = relative.get("position")
    if position == "last":
        return [ordered[-1]]
    if position == "first":
        return [ordered[0]]
    if position == "ordinal":
        requested = int(relative.get("index") or 0)
        if 1 <= requested <= len(ordered):
            return [ordered[requested - 1]]
    return []


def _build_requested_refinement(question: str) -> RequestedRefinement:
    refinement = RequestedRefinement(entity_kind_hint=_question_entity_kind(question))
    relative = parse_relative_reference(question)
    if relative and relative.get("position") in {"first", "last"}:
        refinement.relative_position = relative["position"]
    ordinal = parse_ordinal_reference(question)
    if ordinal is not None:
        refinement.ordinal_hint = ordinal
    question_text = _normalized_text(question)
    if "최근" in question_text:
        refinement.time_filter = "recent"
    lowered = question_text.lower()
    for cue, source_filter in _RESEARCHER_ROLE_REFINEMENTS:
        if cue in lowered:
            refinement.source_filter = source_filter
            break
    if "상위" in question_text or "top" in question_text.lower():
        refinement.sort_dir = "desc"
    return refinement


def build_turn_candidates(
    *,
    view_state: Optional[ConversationViewState],
) -> list[TurnCandidate]:
    if view_state is None:
        return []

    candidate_map: dict[str, TurnCandidate] = {}

    def add_candidate(candidate: TurnCandidate) -> None:
        if not candidate.candidate_id:
            return
        if not candidate.display_name and not candidate.ids_map:
            return
        existing = candidate_map.get(candidate.candidate_id)
        if existing is None:
            candidate_map[candidate.candidate_id] = candidate
            return
        merged = existing.model_copy(
            update={
                "aliases": _dedupe_texts([*(existing.aliases or []), *(candidate.aliases or []), existing.display_name or "", candidate.display_name or ""]),
                "ids_map": {**dict(existing.ids_map or {}), **dict(candidate.ids_map or {})},
                "parent_subject_ids": _dedupe_texts([*(existing.parent_subject_ids or []), *(candidate.parent_subject_ids or [])]),
                "display_rank": existing.display_rank if existing.display_rank is not None else candidate.display_rank,
                "turn_index": candidate.turn_index if candidate.turn_index is not None else existing.turn_index,
                "view_id": existing.view_id or candidate.view_id,
                "resolution_state": "resolved" if existing.resolution_state == "resolved" or candidate.resolution_state == "resolved" else "provisional",
            }
        )
        candidate_map[candidate.candidate_id] = merged

    focus = get_active_focus_entity(view_state)
    if focus is not None:
        ids_map = _focus_ids_map(focus)
        display_name = _normalized_text(focus.title_text) or None
        add_candidate(
            TurnCandidate(
                candidate_id=_candidate_id(source="focus_entity", ids_map=ids_map, subject_id=None, display_name=display_name),
                source="focus_entity",
                entity_kind=_normalize_entity_kind(focus.kind),
                display_name=display_name,
                aliases=_dedupe_texts([display_name or ""]),
                ids_map=ids_map,
                display_rank=focus.display_rank,
                view_id=focus.view_id,
            )
        )

    child_anchor = get_active_child_anchor(view_state)
    if child_anchor is not None:
        ids_map = _focus_ids_map(child_anchor)
        display_name = _normalized_text(child_anchor.title_text) or None
        add_candidate(
            TurnCandidate(
                candidate_id=_candidate_id(source="child_anchor", ids_map=ids_map, subject_id=None, display_name=display_name),
                source="child_anchor",
                entity_kind=_normalize_entity_kind(child_anchor.kind),
                display_name=display_name,
                aliases=_dedupe_texts([display_name or ""]),
                ids_map=ids_map,
                display_rank=child_anchor.display_rank,
                view_id=child_anchor.view_id,
            )
        )

    snapshot = getattr(view_state, "visible_answer_manifest", None)
    if snapshot is not None:
        for item in list(getattr(snapshot, "items", []) or []):
            ids_map = _normalize_ids_map(
                {
                    "pjt_id": item.pjt_id,
                    "pjt_no": item.pjt_no,
                    "rst_id": item.rst_id,
                    "person_no": item.person_no,
                    "org_id": item.org_id,
                    "org_code": item.org_code,
                    "biz_no": item.biz_no,
                    "doi": item.doi,
                    "issn": item.issn,
                }
            )
            display_name = _normalized_text(item.title_text) or None
            add_candidate(
                TurnCandidate(
                    candidate_id=_candidate_id(source="manifest_item", ids_map=ids_map, subject_id=None, display_name=display_name),
                    source="manifest_item",
                    entity_kind=_normalize_entity_kind(item.entity_kind),
                    display_name=display_name,
                    aliases=_dedupe_texts([display_name or ""]),
                    ids_map=ids_map,
                    display_rank=item.display_rank,
                    view_id=getattr(snapshot, "view_id", None),
                )
            )

    subject_index = getattr(view_state, "subject_index", {}) or {}
    if isinstance(subject_index, dict):
        for raw_entry in subject_index.values():
            try:
                entry = raw_entry if isinstance(raw_entry, SubjectIndexEntry) else SubjectIndexEntry.model_validate(raw_entry)
            except Exception:
                continue
            ids_map = _normalize_ids_map(entry.ids_map)
            display_name = _normalized_text(entry.display_name) or None
            add_candidate(
                TurnCandidate(
                    candidate_id=_candidate_id(source="subject_index", ids_map=ids_map, subject_id=entry.subject_id, display_name=display_name),
                    source="subject_index",
                    entity_kind=_normalize_entity_kind(entry.kind),
                    subject_id=_normalized_text(entry.subject_id) or None,
                    display_name=display_name,
                    aliases=_dedupe_texts([entry.display_name, *(entry.aliases or [])]),
                    ids_map=ids_map,
                    parent_subject_ids=_dedupe_texts(list(entry.parent_subject_ids or [])),
                    resolution_state=entry.resolution_state,
                )
            )

    for mention in reversed(get_recent_mentions(view_state)):
        ids_map = _recent_mention_ids_map(mention)
        display_name = _normalized_text(getattr(mention, "title_text", None)) or None
        add_candidate(
            TurnCandidate(
                candidate_id=_candidate_id(source="recent_mention", ids_map=ids_map, subject_id=None, display_name=display_name),
                source="recent_mention",
                entity_kind=_normalize_entity_kind(getattr(mention, "entity_kind", None)),
                display_name=display_name,
                aliases=_dedupe_texts([display_name or ""]),
                ids_map=ids_map,
                display_rank=getattr(mention, "display_rank", None),
                turn_index=getattr(mention, "turn_index", None),
            )
        )

    return sorted(candidate_map.values(), key=_candidate_sort_key)


def _candidate_action(candidate: TurnCandidate) -> ChosenAction:
    return "reuse_manifest" if candidate.source == "manifest_item" else "reuse_anchor"


def match_anchor_to_candidates(
    *,
    anchor: Optional[FocusEntity],
    candidates: list[TurnCandidate],
) -> list[str]:
    if anchor is None:
        return []
    anchor_ids = _focus_ids_map(anchor)
    anchor_name = _normalized_text(anchor.title_text).lower()
    matched: list[str] = []
    for candidate in candidates:
        for key in _IDENTITY_ID_KEYS:
            candidate_values = candidate.ids_map.get(key) or []
            anchor_values = anchor_ids.get(key) or []
            if candidate_values and anchor_values and candidate_values[0] == anchor_values[0]:
                matched.append(candidate.candidate_id)
                break
        else:
            if anchor_name and anchor_name == _normalized_text(candidate.display_name).lower():
                matched.append(candidate.candidate_id)
    return matched[:3]


def resolve_hard_followup_signal(
    *,
    question: str,
    normalized_intent: Any,
    view_state: Optional[ConversationViewState],
) -> HardResolutionResult:
    if view_state is None:
        view_state = ConversationViewState()

    ids_map = _normalize_ids_map(
        getattr(normalized_intent, "ids_map", {}) or (normalized_intent.get("ids_map") if isinstance(normalized_intent, dict) else {})
    )
    if ids_map:
        kind_hint = _normalize_entity_kind(
            getattr(normalized_intent, "base_route", None) if not isinstance(normalized_intent, dict) else normalized_intent.get("base_route")
        )
        return HardResolutionResult(
            matched=True,
            action="reuse_anchor",
            reason="explicit_id_signal",
            anchor=FocusEntity(
                kind=kind_hint,
                source="explicit_id",
                pjt_id=(ids_map.get("pjt_id") or [None])[0],
                pjt_no=(ids_map.get("pjt_no") or [None])[0],
                rst_id=(ids_map.get("rst_id") or [None])[0],
                person_no=(ids_map.get("person_no") or [None])[0],
                org_id=(ids_map.get("org_id") or [None])[0],
                org_code=(ids_map.get("org_code") or [None])[0],
                biz_no=(ids_map.get("biz_no") or [None])[0],
                doi=(ids_map.get("doi") or [None])[0],
                issn=(ids_map.get("issn") or [None])[0],
            ),
        )

    snapshot = getattr(view_state, "visible_answer_manifest", None)
    focus = get_active_focus_entity(view_state)

    source_ref = parse_source_reference(question)
    if source_ref is not None:
        if snapshot is None:
            return HardResolutionResult(matched=True, action="clarification", reason="source_reference_missing_manifest")
        if not 1 <= source_ref <= len(snapshot.items):
            return HardResolutionResult(matched=True, action="clarification", reason="source_reference_out_of_range")
        item = snapshot.items[source_ref - 1]
        return HardResolutionResult(
            matched=True,
            action="reuse_manifest",
            reason="source_reference_display_snapshot",
            anchor=focus_entity_from_item(
                item=item,
                kind=getattr(snapshot, "context_kind", None) or item.entity_kind or "project",
                source="display_snapshot",
                view_id=getattr(snapshot, "view_id", None),
            ),
        )

    ordinal = parse_ordinal_reference(question)
    if ordinal is not None:
        if snapshot is None:
            return HardResolutionResult(matched=True, action="clarification", reason="ordinal_missing_manifest")
        if not 1 <= ordinal <= len(snapshot.items):
            return HardResolutionResult(matched=True, action="clarification", reason="ordinal_out_of_range")
        item = snapshot.items[ordinal - 1]
        return HardResolutionResult(
            matched=True,
            action="reuse_manifest",
            reason="ordinal_display_snapshot",
            anchor=focus_entity_from_item(
                item=item,
                kind=getattr(snapshot, "context_kind", None) or item.entity_kind or "project",
                source="display_snapshot",
                view_id=getattr(snapshot, "view_id", None),
            ),
        )

    relative = parse_relative_reference(question)
    if relative is not None:
        relative_candidates = _relative_candidates(question, build_turn_candidates(view_state=view_state))
        if relative_candidates:
            selected = relative_candidates[0]
            if selected.source == "manifest_item" and snapshot is not None and selected.display_rank is not None:
                for item in list(snapshot.items or []):
                    if int(item.display_rank or 0) != int(selected.display_rank or 0):
                        continue
                    return HardResolutionResult(
                        matched=True,
                        action="reuse_manifest",
                        reason=f"relative_{relative.get('position')}",
                        anchor=focus_entity_from_item(
                            item=item,
                            kind=getattr(snapshot, "context_kind", None) or item.entity_kind or "project",
                            source="display_snapshot",
                            view_id=getattr(snapshot, "view_id", None),
                        ),
                    )
            if selected.source == "focus_entity" and focus is not None:
                return HardResolutionResult(
                    matched=True,
                    action="reuse_anchor",
                    reason=f"relative_{relative.get('position')}",
                    anchor=focus,
                )
            from apps.conversation.followup_anchor import resolve_candidate_to_focus_entity

            materialized = resolve_candidate_to_focus_entity(candidate=selected, view_state=view_state)
            if materialized is not None:
                return HardResolutionResult(
                    matched=True,
                    action="reuse_anchor",
                    reason=f"relative_{relative.get('position')}",
                    anchor=materialized,
                )
        return HardResolutionResult(matched=True, action="clarification", reason="relative_reference_ambiguous")

    return HardResolutionResult()


def build_clarification_payload(
    *,
    question: str,
    candidates: list[TurnCandidate],
    interpretation: Optional[TurnInterpretationResult],
    blocked_reason: str,
    view_state: Optional[ConversationViewState] = None,
) -> dict[str, Any]:
    # ADR-0014 Scope A: 동명 후보 disambiguator enrichment (sync-touch).
    # view_state가 None이면 labeler는 heuristic만 돌리며 aux는 비게 된다
    # (기존 동작과 동치 + 라벨에 (kind)만 붙음).
    from apps.conversation.clarification_labeler import (
        enrich_candidate_labels_for_summary,
        render_label,
        summarize_view_state_for_labeler,
    )

    filtered = _filtered_candidates_for_question(question, candidates)
    eligible: list[TurnCandidate] = []
    for candidate in filtered[:3]:
        display_seed = _normalized_text(candidate.display_name) or candidate.subject_id or candidate.candidate_id
        if candidate.entity_kind and display_seed:
            eligible.append(candidate)
    enriched = enrich_candidate_labels_for_summary(
        question=question,
        candidates=eligible,
        view_state_summary=summarize_view_state_for_labeler(view_state),
    )

    suggestions: list[ClarificationSuggestion] = []
    for candidate, aux, disambiguator in enriched:
        display_seed = _normalized_text(candidate.display_name) or candidate.subject_id or candidate.candidate_id
        label = render_label(
            display_name=display_seed,
            entity_kind=candidate.entity_kind,
            disambiguator=disambiguator,
        )
        suggestions.append(
            ClarificationSuggestion(
                label=label,
                candidate_id=candidate.candidate_id,
                entity_kind=candidate.entity_kind,
                disambiguator=disambiguator,
                aux=aux or {},
            )
        )
    message = compose_clarification_message(
        question=question,
        blocked_reason=blocked_reason,
        suggestions=suggestions,
        focus_entity=get_active_focus_entity(view_state) if view_state is not None else None,
        view_state_summary=_view_state_summary(view_state),
        ctx={
            "clarification_type": "turn_interpretation",
            "selected_candidate_ids": list((interpretation.selected_candidate_ids if interpretation else []) or []),
            "target_entity_kind": getattr(interpretation, "target_entity_kind", None) if interpretation else None,
        },
    )
    return {
        "clarification_type": "turn_interpretation",
        "reason": blocked_reason,
        "message": message,
        "candidates": [suggestion.to_payload() for suggestion in suggestions],
        "selected_candidate_ids": list((interpretation.selected_candidate_ids if interpretation else []) or []),
    }


def _view_state_summary(view_state: Optional[ConversationViewState]) -> Dict[str, Any]:
    if view_state is None:
        return {
            "has_visible_answer_manifest": False,
            "manifest_visible_count": 0,
            "has_active_focus": False,
            "has_child_anchor": False,
            "subject_index_count": 0,
            "recent_mention_count": 0,
            "last_turn_kind": None,
            "last_answer_publishability": None,
            "last_followup_rights": None,
            "subject_kind": None,
            "subject_name": None,
            "refinement_allowed": False,
        }
    snapshot = getattr(view_state, "visible_answer_manifest", None)
    active_scope = getattr(view_state, "active_scope", None)
    active_focus = getattr(active_scope, "focus", None)
    last_query_contract = dict(getattr(view_state, "last_query_contract", {}) or {})
    subject_kind = _normalized_text(last_query_contract.get("subject_kind")).lower() or None
    subject_name = _normalized_text(last_query_contract.get("subject_name")) or None
    focus_kind = _normalized_text(getattr(active_focus, "kind", "")).lower()
    if not subject_kind and focus_kind in {"people", "org"}:
        subject_kind = focus_kind
        subject_name = _normalized_text(getattr(active_focus, "title_text", "")) or subject_name
    return {
        "has_visible_answer_manifest": snapshot is not None,
        "manifest_visible_count": int(getattr(snapshot, "visible_count", 0) or 0),
        "has_active_focus": active_focus is not None,
        "has_child_anchor": getattr(active_scope, "child_anchor", None) is not None,
        "subject_index_count": len(getattr(view_state, "subject_index", {}) or {}),
        "recent_mention_count": len(get_recent_mentions(view_state)),
        "last_turn_kind": _normalized_text(last_query_contract.get("turn_kind")).lower() or None,
        "last_answer_publishability": _normalized_text(last_query_contract.get("answer_publishability")).lower() or None,
        "last_followup_rights": _normalized_text(last_query_contract.get("followup_rights")).lower() or None,
        "subject_kind": subject_kind,
        "subject_name": subject_name,
        "refinement_allowed": bool(last_query_contract.get("refinement_allowed")) or bool(subject_kind and subject_name),
    }


def _heuristic_turn_interpretation(
    *,
    question: str,
    candidates: list[TurnCandidate],
    trigger: TurnTriggerResult,
    has_explicit_seed: bool,
) -> TurnInterpretationResult:
    if has_explicit_seed:
        return TurnInterpretationResult(
            chosen_action="fresh_retrieval",
            confidence=1.0,
            reason="explicit_seed_bypass",
            rewritten_user_intent=_normalized_text(question) or None,
        )

    if trigger.turn_intent == "fresh":
        return TurnInterpretationResult(
            chosen_action="fresh_retrieval",
            confidence=max(float(trigger.confidence or 0.0), 0.7),
            reason=trigger.reason or "fresh_trigger",
            rewritten_user_intent=_normalized_text(question) or None,
        )

    if trigger.reference_style == "refinement":
        return TurnInterpretationResult(
            chosen_action="fresh_retrieval",
            target_entity_kind=_question_entity_kind(question),
            requested_refinement=_build_requested_refinement(question),
            confidence=max(float(trigger.confidence or 0.0), 0.82),
            reason="subject_refresh",
            rewritten_user_intent=_normalized_text(question) or None,
        )

    if parse_source_reference(question) is not None or parse_ordinal_reference(question) is not None:
        return TurnInterpretationResult(
            chosen_action="clarification",
            confidence=0.98,
            reason="hard_signal_required",
            ambiguity_reason="hard_signal_required",
            rewritten_user_intent=_normalized_text(question) or None,
        )

    if not candidates:
        return TurnInterpretationResult(
            chosen_action="clarification",
            confidence=0.92,
            reason="no_candidates",
            ambiguity_reason="no_candidates",
            rewritten_user_intent=_normalized_text(question) or None,
        )

    relative_choice = _relative_candidates(question, candidates)
    if relative_choice:
        selected = relative_choice[0]
        return TurnInterpretationResult(
            chosen_action=_candidate_action(selected),
            selected_candidate_ids=[selected.candidate_id],
            target_entity_kind=selected.entity_kind,
            requested_refinement=_build_requested_refinement(question),
            confidence=0.9,
            reason="relative_candidate_heuristic",
            rewritten_user_intent=_normalized_text(question) or None,
        )

    target_kind = _question_entity_kind(question)
    filtered = _filtered_candidates_for_question(question, candidates)
    if target_kind is not None and not filtered:
        return TurnInterpretationResult(
            chosen_action="clarification",
            target_entity_kind=target_kind,
            requested_refinement=_build_requested_refinement(question),
            confidence=0.88,
            reason="same_kind_candidates_missing",
            ambiguity_reason="same_kind_candidates_missing",
            rewritten_user_intent=_normalized_text(question) or None,
        )
    named_matches = [candidate for candidate in filtered if _candidate_matches_name(question, candidate)]
    if len(named_matches) == 1:
        selected = named_matches[0]
        return TurnInterpretationResult(
            chosen_action=_candidate_action(selected),
            selected_candidate_ids=[selected.candidate_id],
            target_entity_kind=selected.entity_kind,
            requested_refinement=_build_requested_refinement(question),
            confidence=0.84,
            reason="named_candidate_heuristic",
            rewritten_user_intent=_normalized_text(question) or None,
        )
    if len(named_matches) > 1:
        return TurnInterpretationResult(
            chosen_action="clarification",
            target_entity_kind=target_kind,
            requested_refinement=_build_requested_refinement(question),
            confidence=0.28,
            reason="named_candidate_ambiguous",
            ambiguity_reason="multiple_named_candidates",
            rewritten_user_intent=_normalized_text(question) or None,
        )

    strong = [candidate for candidate in filtered if candidate.source in {"child_anchor", "focus_entity"}]
    if len(strong) == 1 and is_referential_followup(question):
        selected = strong[0]
        return TurnInterpretationResult(
            chosen_action="reuse_anchor",
            selected_candidate_ids=[selected.candidate_id],
            target_entity_kind=selected.entity_kind,
            requested_refinement=_build_requested_refinement(question),
            confidence=0.82,
            reason="single_anchor_context",
            rewritten_user_intent=_normalized_text(question) or None,
        )

    if len(filtered) == 1 and is_referential_followup(question):
        selected = filtered[0]
        return TurnInterpretationResult(
            chosen_action=_candidate_action(selected),
            selected_candidate_ids=[selected.candidate_id],
            target_entity_kind=selected.entity_kind,
            requested_refinement=_build_requested_refinement(question),
            confidence=0.76,
            reason="single_filtered_candidate",
            rewritten_user_intent=_normalized_text(question) or None,
        )

    if target_kind is not None and len(filtered) == 1:
        selected = filtered[0]
        return TurnInterpretationResult(
            chosen_action=_candidate_action(selected),
            selected_candidate_ids=[selected.candidate_id],
            target_entity_kind=selected.entity_kind,
            requested_refinement=_build_requested_refinement(question),
            confidence=0.72,
            reason="single_same_kind_candidate_auto",
            rewritten_user_intent=_normalized_text(question) or None,
        )

    return TurnInterpretationResult(
        chosen_action="clarification",
        target_entity_kind=target_kind,
        requested_refinement=_build_requested_refinement(question),
        confidence=0.24,
        reason="heuristic_ambiguity",
        ambiguity_reason="multiple_candidates",
        rewritten_user_intent=_normalized_text(question) or None,
    )


async def _invoke_turn_interpretation_llm(
    *,
    question: str,
    trigger: TurnTriggerResult,
    summary: Dict[str, Any],
    candidate_payload: list[dict[str, Any]],
    heuristic: TurnInterpretationResult,
) -> TurnInterpretationResult:
    llm = build_llm(model_name="solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=TurnInterpretationResult)
    system_prompt = await load_prompt_file(planner_prompt_path("turn_interpreter_v1.md"))
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
            (
                "human",
                "{format_instructions}\n"
                "<user_query>{question}</user_query>\n"
                "<turn_trigger>{turn_trigger}</turn_trigger>\n"
                "<view_state_summary>{view_state_summary}</view_state_summary>\n"
                "<candidates>{candidates}</candidates>\n"
                "<heuristic_hint>{heuristic_hint}</heuristic_hint>",
            ),
        ]
    )
    interpreter_llm = llm.bind(
        reasoning_effort="low",
        include_reasoning=False,
        disable_thinking=PLANNER_DISABLE_THINKING,
        temperature=PLANNER_TEMPERATURE,
        top_p=1.0,
        max_tokens=260,
    )
    chain = prompt | interpreter_llm | sanitize_llm_json | parser
    return await chain.ainvoke(
        {
            "format_instructions": parser.get_format_instructions(),
            "question": question,
            "turn_trigger": json.dumps(trigger.model_dump(), ensure_ascii=False),
            "view_state_summary": json.dumps(summary, ensure_ascii=False),
            "candidates": json.dumps(candidate_payload, ensure_ascii=False),
            "heuristic_hint": json.dumps(heuristic.model_dump(), ensure_ascii=False),
        }
    )


def _llm_first_gate(
    *,
    has_explicit_seed: bool,
    trigger: TurnTriggerResult,
    prompt_candidates: list[TurnCandidate],
    summary: Dict[str, Any],
) -> bool:
    # ADR-0014 Scope C: SessionMemory.current_context is official truth.
    # A current_context summary is treated as view-state-equivalent context
    # for this LLM-first gate.
    has_view_state_equivalent_context = bool(
        summary.get("has_visible_answer_manifest")
        or summary.get("has_active_focus")
        or summary.get("has_child_anchor")
        or int(summary.get("subject_index_count") or 0) > 0
        or int(summary.get("recent_mention_count") or 0) > 0
        or (summary.get("current_context_type") and summary.get("current_context_type") != "empty")
    )
    return bool(
        turn_interpretation_llm_first_enabled()
        and not has_explicit_seed
        and trigger.turn_intent != "fresh"
        and trigger.reference_style != "refinement"
        and has_view_state_equivalent_context
        and prompt_candidates
    )


def _llm_first_trigger_source(summary: Dict[str, Any]) -> str:
    if summary.get("memory_source") == "current_context":
        return "current_context"
    return "view_state"


def _validate_llm_first_result(
    *,
    result: TurnInterpretationResult,
    heuristic: TurnInterpretationResult,
    prompt_candidates: list[TurnCandidate],
) -> tuple[TurnInterpretationResult, str]:
    valid_candidate_ids = {candidate.candidate_id for candidate in prompt_candidates}
    selected_candidate_ids = list(result.selected_candidate_ids or [])
    if any(candidate_id not in valid_candidate_ids for candidate_id in selected_candidate_ids):
        raise ValueError("invalid_candidate_id")
    if float(result.confidence or 0.0) < _INTERPRETER_LOW_CONFIDENCE and result.chosen_action != "clarification":
        return heuristic.model_copy(update={"reason": "low_confidence_fallback"}), "skipped"
    if result.chosen_action == "clarification":
        return result.model_copy(update={"reason": "llm_first"}), "skipped"
    if result.chosen_action == "fresh_retrieval" or not selected_candidate_ids:
        return (
            result.model_copy(
                update={
                    "chosen_action": "clarification",
                    "selected_candidate_ids": [],
                    "ambiguity_reason": "llm_heuristic_divergence",
                    "confidence": min(float(result.confidence or 0.0), 0.34),
                    "reason": "llm_first_validator_diverged",
                }
            ),
            "diverged",
        )
    if selected_candidate_ids:
        top_candidate_ids = {candidate.candidate_id for candidate in prompt_candidates[:_LLM_FIRST_VALIDATOR_TOP_K]}
        if not top_candidate_ids.intersection(selected_candidate_ids):
            return (
                result.model_copy(
                    update={
                        "chosen_action": "clarification",
                        "selected_candidate_ids": [],
                        "ambiguity_reason": "llm_heuristic_divergence",
                        "confidence": min(float(result.confidence or 0.0), 0.34),
                        "reason": "llm_first_validator_diverged",
                    }
                ),
                "diverged",
            )
    return result.model_copy(update={"reason": "llm_first_validator_ok"}), "ok"


async def run_turn_interpreter(
    *,
    question: str,
    view_state: Optional[ConversationViewState],
    session_memory: Optional[SessionMemory] = None,
    candidates: list[TurnCandidate],
    trigger: TurnTriggerResult,
    has_explicit_seed: bool,
    request_id: Optional[str],
    conversation_id: str,
) -> TurnInterpretationResult:
    heuristic = _heuristic_turn_interpretation(
        question=question,
        candidates=candidates,
        trigger=trigger,
        has_explicit_seed=has_explicit_seed,
    )
    prompt_candidates = _select_prompt_candidates(question, candidates)
    candidate_payload = [_compact_candidate(candidate) for candidate in prompt_candidates]
    summary = current_context_summary(session_memory) if session_memory is not None else _view_state_summary(view_state)

    if _llm_first_gate(
        has_explicit_seed=has_explicit_seed,
        trigger=trigger,
        prompt_candidates=prompt_candidates,
        summary=summary,
    ):
        llm_first_trigger_source = _llm_first_trigger_source(summary)
        try:
            result = await _invoke_turn_interpretation_llm(
                question=question,
                trigger=trigger,
                summary=summary,
                candidate_payload=candidate_payload,
                heuristic=heuristic,
            )
            validated, validator = _validate_llm_first_result(
                result=result,
                heuristic=heuristic,
                prompt_candidates=prompt_candidates,
            )
            log_event(
                "TURN.INTERPRETATION.LLM_FIRST",
                request_id=request_id,
                conversation_id=conversation_id,
                trigger=llm_first_trigger_source,
                validator=validator,
                candidate_count=len(prompt_candidates),
                top_k=_LLM_FIRST_VALIDATOR_TOP_K,
                selected_candidate_ids=list(result.selected_candidate_ids or []),
                reason=validated.reason,
            )
            if validator == "skipped" and validated.reason == "low_confidence_fallback":
                log_event(
                    "TURN.INTERPRETATION",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    source="heuristic_fallback",
                    chosen_action=validated.chosen_action,
                    selected_candidate_ids=list(validated.selected_candidate_ids or []),
                    confidence=round(float(validated.confidence or 0.0), 3),
                    reason=validated.reason,
                    candidate_count=len(candidates),
                    fallback_reason="low_confidence",
                    target_entity_kind=validated.target_entity_kind,
                )
                return validated
            log_event(
                "TURN.INTERPRETATION",
                request_id=request_id,
                conversation_id=conversation_id,
                source="llm_first",
                chosen_action=validated.chosen_action,
                selected_candidate_ids=list(validated.selected_candidate_ids or []),
                confidence=round(float(validated.confidence or 0.0), 3),
                reason=validated.reason,
                candidate_count=len(candidates),
                target_entity_kind=validated.target_entity_kind,
            )
            return validated
        except Exception as exc:
            fallback = heuristic.model_copy(update={"reason": f"llm_first_error_fallback:{type(exc).__name__}"})
            log_event(
                "TURN.INTERPRETATION.LLM_FIRST",
                request_id=request_id,
                conversation_id=conversation_id,
                trigger=llm_first_trigger_source,
                validator="skipped",
                candidate_count=len(prompt_candidates),
                top_k=_LLM_FIRST_VALIDATOR_TOP_K,
                selected_candidate_ids=[],
                reason=f"llm_first_error_fallback:{type(exc).__name__}",
                fallback_reason=type(exc).__name__,
            )
            log_event(
                "TURN.INTERPRETATION",
                request_id=request_id,
                conversation_id=conversation_id,
                source="heuristic_fallback",
                chosen_action=fallback.chosen_action,
                selected_candidate_ids=list(fallback.selected_candidate_ids or []),
                confidence=round(float(fallback.confidence or 0.0), 3),
                reason=fallback.reason,
                candidate_count=len(candidates),
                fallback_reason=type(exc).__name__,
                target_entity_kind=fallback.target_entity_kind,
            )
            return fallback

    if (
        has_explicit_seed
        or trigger.turn_intent == "fresh"
        or trigger.reference_style == "refinement"
        or not prompt_candidates
        or (
            heuristic.chosen_action != "clarification"
            and str(heuristic.reason or "").strip().lower() in _HEURISTIC_DIRECT_REASONS
        )
    ):
        log_event(
            "TURN.INTERPRETATION",
            request_id=request_id,
            conversation_id=conversation_id,
            source="heuristic",
            chosen_action=heuristic.chosen_action,
            selected_candidate_ids=list(heuristic.selected_candidate_ids or []),
            confidence=round(float(heuristic.confidence or 0.0), 3),
            reason=heuristic.reason,
            candidate_count=len(prompt_candidates),
            target_entity_kind=heuristic.target_entity_kind,
        )
        return heuristic

    try:
        result = await _invoke_turn_interpretation_llm(
            question=question,
            trigger=trigger,
            summary=summary,
            candidate_payload=candidate_payload,
            heuristic=heuristic,
        )
        valid_candidate_ids = {candidate.candidate_id for candidate in prompt_candidates}
        if any(candidate_id not in valid_candidate_ids for candidate_id in result.selected_candidate_ids):
            raise ValueError("invalid_candidate_id")
        if float(result.confidence or 0.0) < _INTERPRETER_LOW_CONFIDENCE and result.chosen_action != "clarification":
            fallback = heuristic.model_copy(update={"reason": "low_confidence_fallback"})
            log_event(
                "TURN.INTERPRETATION",
                request_id=request_id,
                conversation_id=conversation_id,
                source="heuristic_fallback",
                chosen_action=fallback.chosen_action,
                selected_candidate_ids=list(fallback.selected_candidate_ids or []),
                confidence=round(float(fallback.confidence or 0.0), 3),
                reason=fallback.reason,
                candidate_count=len(candidates),
                fallback_reason="low_confidence",
                target_entity_kind=fallback.target_entity_kind,
            )
            return fallback

        log_event(
            "TURN.INTERPRETATION",
            request_id=request_id,
            conversation_id=conversation_id,
            source="llm",
            chosen_action=result.chosen_action,
            selected_candidate_ids=list(result.selected_candidate_ids or []),
            confidence=round(float(result.confidence or 0.0), 3),
            reason=result.reason,
            candidate_count=len(candidates),
            target_entity_kind=result.target_entity_kind,
        )
        return result
    except Exception as exc:
        fallback = heuristic.model_copy(update={"reason": f"llm_error_fallback:{type(exc).__name__}"})
        log_event(
            "TURN.INTERPRETATION",
            request_id=request_id,
            conversation_id=conversation_id,
            source="heuristic_fallback",
            chosen_action=fallback.chosen_action,
            selected_candidate_ids=list(fallback.selected_candidate_ids or []),
            confidence=round(float(fallback.confidence or 0.0), 3),
            reason=fallback.reason,
            candidate_count=len(candidates),
            fallback_reason=type(exc).__name__,
            target_entity_kind=fallback.target_entity_kind,
        )
        return fallback
