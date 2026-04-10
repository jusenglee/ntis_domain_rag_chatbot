from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

DETAIL_CACHE_SCHEMA_VERSION = 2
_SYNTHETIC_TITLE_PRIMARY_TYPES = {
    "aggregation",
    "series",
    "pattern_analysis",
    "reverse_trace",
    "multi_hop_bundle",
}
_META_TITLE_KEYS = ("kor_pjt_nm", "eng_pjt_nm", "title", "paper_nm")


class ChildEntityRef(BaseModel):
    kind: Literal["people", "org", "perf"]
    display_name: str
    ids_map: Dict[str, List[str]] = Field(default_factory=dict)
    subject_id: str = ""
    resolution_state: Literal["resolved", "provisional"] = "resolved"
    role: Optional[str] = None
    affiliation: Optional[str] = None
    parent_relation: Optional[str] = None

    @model_validator(mode="after")
    def _normalize_entity_ref(self) -> "ChildEntityRef":
        ids_map = _normalize_child_ids_map(self.ids_map)
        display_name = _first_text(self.display_name) or ""
        role = _first_text(self.role)
        affiliation = _first_text(self.affiliation)
        self.display_name = display_name
        self.ids_map = ids_map
        self.subject_id = _first_text(self.subject_id) or _build_subject_identity(
            kind=self.kind,
            display_name=display_name,
            ids_map=ids_map,
            role=role,
            affiliation=affiliation,
        )
        self.resolution_state = "resolved" if _has_subject_ids(ids_map) else "provisional"
        self.role = role or None
        self.affiliation = affiliation or None
        self.parent_relation = _first_text(self.parent_relation) or None
        return self


class SubjectIndexEntry(BaseModel):
    kind: str
    subject_id: str
    display_name: str
    aliases: List[str] = Field(default_factory=list)
    ids_map: Dict[str, List[str]] = Field(default_factory=dict)
    role: Optional[str] = None
    affiliation: Optional[str] = None
    parent_subject_ids: List[str] = Field(default_factory=list)
    source_turn_id: Optional[str] = None
    last_seen_turn_id: Optional[str] = None
    resolution_state: Literal["resolved", "provisional"] = "resolved"

    @model_validator(mode="after")
    def _normalize_subject_index_entry(self) -> "SubjectIndexEntry":
        ids_map = _normalize_child_ids_map(self.ids_map)
        display_name = _first_text(self.display_name) or ""
        aliases = _dedupe_texts([display_name, *(self.aliases or [])])
        role = _first_text(self.role)
        affiliation = _first_text(self.affiliation)
        self.kind = str(self.kind or "").strip().lower() or "unknown"
        self.subject_id = _first_text(self.subject_id) or _build_subject_identity(
            kind=str(self.kind or "").strip().lower() or "unknown",
            display_name=display_name,
            ids_map=ids_map,
            role=role,
            affiliation=affiliation,
        )
        self.display_name = display_name
        self.aliases = aliases
        self.ids_map = ids_map
        self.role = role or None
        self.affiliation = affiliation or None
        self.parent_subject_ids = _dedupe_texts(self.parent_subject_ids or [])
        self.source_turn_id = _first_text(self.source_turn_id) or None
        self.last_seen_turn_id = _first_text(self.last_seen_turn_id) or None
        self.resolution_state = "resolved" if _has_subject_ids(ids_map) else "provisional"
        return self


class DisplayItem(BaseModel):
    display_rank: int
    entity_kind: str = "project"
    doc_type: Optional[str] = None
    doc_id: Optional[str] = None
    col: Optional[str] = None
    title_text: str = ""
    pjt_id: Optional[str] = None
    pjt_no: Optional[str] = None
    rst_id: Optional[str] = None
    person_no: Optional[str] = None
    org_id: Optional[str] = None
    org_code: Optional[str] = None
    biz_no: Optional[str] = None
    doi: Optional[str] = None
    issn: Optional[str] = None
    year: Optional[int] = None
    lead_org: Optional[str] = None
    participant_org: List[str] = Field(default_factory=list)
    researchers: List[str] = Field(default_factory=list)
    child_refs: List[ChildEntityRef] = Field(default_factory=list)
    score: Optional[float] = None


class DisplaySnapshot(BaseModel):
    view_id: str
    conversation_id: str
    turn_id: str
    context_kind: str
    requested_count: int
    visible_count: int
    raw_count: int
    items: List[DisplayItem] = Field(default_factory=list)


class FocusEntity(BaseModel):
    kind: str
    source: str
    view_id: Optional[str] = None
    display_rank: Optional[int] = None
    doc_type: Optional[str] = None
    doc_id: Optional[str] = None
    pjt_id: Optional[str] = None
    pjt_no: Optional[str] = None
    rst_id: Optional[str] = None
    person_no: Optional[str] = None
    org_id: Optional[str] = None
    org_code: Optional[str] = None
    biz_no: Optional[str] = None
    doi: Optional[str] = None
    issn: Optional[str] = None
    title_text: Optional[str] = None
    year: Optional[int] = None
    lead_org: Optional[str] = None
    participant_org: List[str] = Field(default_factory=list)
    researchers: List[str] = Field(default_factory=list)
    child_refs: List[ChildEntityRef] = Field(default_factory=list)


class DetailCoverage(BaseModel):
    entity_found: bool = False
    detail_level: str = "not_found"
    available_fields: List[str] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    core_profile: Dict[str, Any] = Field(default_factory=dict)
    rich_detail: Dict[str, Any] = Field(default_factory=dict)


class DetailCacheEntry(BaseModel):
    entity_key: str
    anchor: FocusEntity
    coverage: DetailCoverage
    hydrated_fields: List[str] = Field(default_factory=list)
    source_turn_id: str
    schema_version: int = DETAIL_CACHE_SCHEMA_VERSION


class ActiveScope(BaseModel):
    result_set: Optional[DisplaySnapshot] = None
    focus: Optional[FocusEntity] = None
    child_anchor: Optional[FocusEntity] = None
    parent_chain: List[Dict[str, Any]] = Field(default_factory=list)
    scope_kind: Literal["list", "detail", "child", "fresh"] = "fresh"


class RecentMentionRecord(BaseModel):
    """retrieval 결과에서 추출한 최근 언급 엔티티 레코드.

    답변 텍스트 파싱이 아니라 구조화된 retrieval state에서만 생성한다.
    """
    entity_kind: Literal["project", "perf", "people", "org"]
    title_text: Optional[str] = None
    pjt_id: Optional[str] = None
    pjt_no: Optional[str] = None
    rst_id: Optional[str] = None
    doi: Optional[str] = None
    issn: Optional[str] = None
    year: Optional[str] = None
    lead_org: Optional[str] = None
    participant_org: List[str] = Field(default_factory=list)
    researchers: List[str] = Field(default_factory=list)
    source: Literal["list_snapshot", "detail_focus", "child_anchor"] = "list_snapshot"
    display_rank: Optional[int] = None
    turn_index: Optional[int] = None


class ConversationViewState(BaseModel):
    visible_answer_manifest: Optional[DisplaySnapshot] = None
    active_scope: ActiveScope = Field(default_factory=ActiveScope)
    subject_index: Dict[str, SubjectIndexEntry] = Field(default_factory=dict)
    detail_cache: Dict[str, DetailCacheEntry] = Field(default_factory=dict)
    raw_candidates_cache: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    active_result_set_kind: Optional[str] = None
    active_result_view_id: Optional[str] = None
    applied_filters: Dict[str, Any] = Field(default_factory=dict)
    sort_key: Optional[str] = None
    sort_dir: Optional[str] = None
    entity_scope: Optional[str] = None
    refinement_history: List[Dict[str, Any]] = Field(default_factory=list)
    last_query_contract: Dict[str, Any] = Field(default_factory=dict)
    recent_mentions: List[RecentMentionRecord] = Field(default_factory=list)


def load_view_state(payload: Any) -> ConversationViewState:
    if isinstance(payload, ConversationViewState):
        return _normalize_view_state(payload)
    if isinstance(payload, dict):
        try:
            return _normalize_view_state(ConversationViewState.model_validate(payload))
        except Exception:
            return ConversationViewState()
    return ConversationViewState()


def _normalize_scope_kind(value: Any) -> Literal["list", "detail", "child", "fresh"]:
    text = str(value or "").strip().lower()
    if text in {"list", "detail", "child"}:
        return text  # type: ignore[return-value]
    return "fresh"


def _append_scope_transition(
    chain: List[Dict[str, Any]],
    *,
    scope_kind: str,
    turn_id: Optional[str],
    view_id: Optional[str] = None,
    entity_kind: Optional[str] = None,
    anchor_kind: Optional[str] = None,
    reason: Optional[str] = None,
    max_items: int = 12,
) -> List[Dict[str, Any]]:
    entry = {
        "scope_kind": _normalize_scope_kind(scope_kind),
        "turn_id": str(turn_id or "").strip() or None,
        "view_id": str(view_id or "").strip() or None,
        "entity_kind": str(entity_kind or "").strip().lower() or None,
        "anchor_kind": str(anchor_kind or "").strip().lower() or None,
        "reason": str(reason or "").strip() or None,
    }
    next_chain = list(chain or [])
    next_chain.append(entry)
    return next_chain[-max_items:]


def _normalize_view_state(
    view_state: ConversationViewState,
) -> ConversationViewState:
    def _coerce_display_snapshot(value: Any) -> Optional[DisplaySnapshot]:
        if value is None or isinstance(value, DisplaySnapshot):
            return value
        payload: Any = value
        if hasattr(value, "model_dump"):
            payload = value.model_dump()
        elif not isinstance(value, dict) and hasattr(value, "__dict__"):
            payload = dict(vars(value))
        try:
            return DisplaySnapshot.model_validate(payload)
        except Exception:
            return None

    active_scope = getattr(view_state, "active_scope", None)
    if not isinstance(active_scope, ActiveScope):
        try:
            active_scope = ActiveScope.model_validate(active_scope or {})
        except Exception:
            active_scope = ActiveScope()

    result_set = _coerce_display_snapshot(active_scope.result_set)
    child_anchor = active_scope.child_anchor
    focus = active_scope.focus

    scope_kind = _normalize_scope_kind(active_scope.scope_kind)
    if scope_kind == "fresh":
        if child_anchor is not None:
            scope_kind = "child"
        elif focus is not None:
            scope_kind = "detail"
        elif result_set is not None:
            scope_kind = "list"

    parent_chain = [entry for entry in list(active_scope.parent_chain or []) if isinstance(entry, dict)]
    normalized_scope = ActiveScope(
        result_set=result_set,
        focus=focus,
        child_anchor=child_anchor,
        parent_chain=parent_chain,
        scope_kind=scope_kind,
    )
    visible_answer_manifest = _coerce_display_snapshot(getattr(view_state, "visible_answer_manifest", None))
    subject_index_payload = getattr(view_state, "subject_index", {}) or {}
    subject_index: Dict[str, SubjectIndexEntry] = {}
    if isinstance(subject_index_payload, dict):
        for key, value in subject_index_payload.items():
            try:
                entry = value if isinstance(value, SubjectIndexEntry) else SubjectIndexEntry.model_validate(value)
            except Exception:
                continue
            entry_key = _first_text(key) or entry.subject_id
            if entry_key:
                subject_index[entry_key] = entry
    return view_state.model_copy(
        update={
            "active_scope": normalized_scope,
            "visible_answer_manifest": visible_answer_manifest,
            "subject_index": subject_index,
            "active_result_view_id": getattr(normalized_scope.result_set, "view_id", None),
        }
    )


def get_active_scope(view_state: Any) -> ActiveScope:
    if view_state is None:
        return ActiveScope()
    if isinstance(view_state, ConversationViewState):
        return _normalize_view_state(view_state).active_scope
    active_scope = getattr(view_state, "active_scope", None)
    if active_scope is None and isinstance(view_state, dict):
        active_scope = view_state.get("active_scope")
    if isinstance(active_scope, ActiveScope):
        return active_scope
    try:
        return ActiveScope.model_validate(active_scope or {})
    except Exception:
        return ActiveScope()


def get_active_result_snapshot(view_state: Any) -> Optional[DisplaySnapshot]:
    return get_active_scope(view_state).result_set


def get_active_focus_entity(view_state: Any) -> Optional[FocusEntity]:
    return get_active_scope(view_state).focus


def get_active_child_anchor(view_state: Any) -> Optional[FocusEntity]:
    return get_active_scope(view_state).child_anchor


def get_active_subject_entity(view_state: Any) -> Optional[FocusEntity]:
    active_scope = get_active_scope(view_state)
    return active_scope.child_anchor or active_scope.focus


def _to_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _first_nested_text(source: Any, *keys: str) -> Optional[str]:
    for key in keys:
        if isinstance(source, dict):
            value = source.get(key)
            text = _first_text(value)
            if text:
                return text
    return None


def _list_texts(items: Any, key: str) -> List[str]:
    out: List[str] = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        text = _first_text(item.get(key))
        if text and text not in out:
            out.append(text)
    return out


def _normalize_child_ids_map(raw_ids_map: Dict[str, Any]) -> Dict[str, List[str]]:
    normalized: Dict[str, List[str]] = {}
    for key, raw_values in (raw_ids_map or {}).items():
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        items: List[str] = []
        seen: set[str] = set()
        for value in values:
            text = _first_text(value)
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
        if items:
            normalized[str(key).strip()] = items
    return normalized


def _dedupe_texts(values: List[Any]) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = _first_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
    return deduped


def _has_subject_ids(ids_map: Dict[str, List[str]]) -> bool:
    return any(bool(values) for values in (ids_map or {}).values())


def _build_subject_identity(*, kind: str, display_name: Optional[str], ids_map: Dict[str, List[str]], role: Optional[str], affiliation: Optional[str]) -> str:
    if kind == "people" and ids_map.get("person_no"):
        return f"people:id:{ids_map['person_no'][0]}"
    if kind == "org":
        if ids_map.get("org_id"):
            return f"org:org_id:{ids_map['org_id'][0]}"
        if ids_map.get("org_code"):
            return f"org:org_code:{ids_map['org_code'][0]}"
        if ids_map.get("biz_no"):
            return f"org:biz_no:{ids_map['biz_no'][0]}"
    if kind == "perf":
        if ids_map.get("rst_id"):
            return f"perf:rst_id:{ids_map['rst_id'][0]}"
        if ids_map.get("doi"):
            return f"perf:doi:{ids_map['doi'][0]}"
        if ids_map.get("issn"):
            return f"perf:issn:{ids_map['issn'][0]}"
    return (
        f"{kind}:name:{(display_name or '').strip().lower()}"
        f"|aff:{(affiliation or '').strip().lower()}"
        f"|role:{(role or '').strip().lower()}"
    )


def _extract_people_child_refs(*sources: Any) -> List[ChildEntityRef]:
    refs: List[ChildEntityRef] = []
    seen: set[str] = set()

    def _append(source: Any, *, parent_relation: str) -> None:
        if not isinstance(source, dict):
            return
        display_name = _first_text(source.get("hm_nm"), source.get("person_name"), source.get("name"))
        person_no = _first_text(
            source.get("hm_id"),
            source.get("person_no"),
            source.get("prtcp_mp_id"),
            source.get("mp_id"),
            source.get("id"),
        )
        ids_map = _normalize_child_ids_map({"person_no": person_no} if person_no else {})
        role = _first_text(source.get("role_slct_nm"), source.get("role_nm"), source.get("role"))
        affiliation = _first_text(source.get("blng_org_nm"), source.get("affiliation"), source.get("org_nm"))
        if not display_name and not ids_map:
            return

        identity = _build_subject_identity(
            kind="people",
            display_name=display_name,
            ids_map=ids_map,
            role=role,
            affiliation=affiliation,
        )
        if identity in seen:
            return
        seen.add(identity)
        refs.append(
            ChildEntityRef(
                kind="people",
                display_name=display_name or person_no or "researcher",
                ids_map=ids_map,
                role=role or None,
                affiliation=affiliation or None,
                parent_relation=parent_relation,
            )
        )

    for source in sources:
        _append(source, parent_relation="top_level_researcher")

    for source in sources:
        members = source.get("prtcp_mp") if isinstance(source, dict) else None
        if not isinstance(members, list):
            continue
        for member in members:
            _append(member, parent_relation="participant_researcher")

    return refs


def _extract_org_child_refs(*sources: Any) -> List[ChildEntityRef]:
    refs: List[ChildEntityRef] = []
    seen: set[str] = set()

    def _append(source: Any, *, parent_relation: str) -> None:
        if not isinstance(source, dict):
            return
        display_name = _first_text(
            source.get("org_nm"),
            source.get("org_name"),
            source.get("lead_org_name"),
            source.get("pjt_prfrm_org_nm"),
            source.get("affiliation"),
        )
        ids_map = _normalize_child_ids_map(
            {
                "org_id": _first_text(source.get("org_id")),
                "org_code": _first_text(source.get("org_code"), source.get("org_cd")),
                "biz_no": _first_text(source.get("biz_no"), source.get("org_no")),
            }
        )
        role = _first_text(source.get("role"), source.get("role_nm"), source.get("role_slct_nm"))
        affiliation = _first_text(source.get("blng_org_nm"))
        if not display_name and not ids_map:
            return

        identity = _build_subject_identity(
            kind="org",
            display_name=display_name,
            ids_map=ids_map,
            role=role,
            affiliation=affiliation,
        )
        if identity in seen:
            return
        seen.add(identity)
        refs.append(
            ChildEntityRef(
                kind="org",
                display_name=display_name or _first_text(*(ids_map.get("org_id") or []), *(ids_map.get("org_code") or []), *(ids_map.get("biz_no") or [])) or "organization",
                ids_map=ids_map,
                role=role or None,
                affiliation=affiliation or None,
                parent_relation=parent_relation,
            )
        )

    for source in sources:
        _append(source, parent_relation="top_level_org")

    for source in sources:
        orgs = source.get("prtcp_org") if isinstance(source, dict) else None
        if not isinstance(orgs, list):
            continue
        for org in orgs:
            _append(org, parent_relation="participant_org")

    return refs


def _iter_perf_candidates(source: Any) -> List[tuple[dict[str, Any], str]]:
    if not isinstance(source, dict):
        return []

    candidates: List[tuple[dict[str, Any], str]] = []
    for key, relation in (
        ("outputs", "linked_output"),
        ("linked_outputs", "linked_output"),
        ("perf_items", "linked_output"),
        ("related_outputs", "linked_output"),
        ("related_perf", "related_perf"),
    ):
        items = source.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                candidates.append((item, relation))
            elif str(item or "").strip():
                candidates.append(({"name": str(item).strip()}, relation))

    for key, relation in (("origin_perf", "origin_perf"), ("perf", "linked_output")):
        item = source.get(key)
        if isinstance(item, dict):
            candidates.append((item, relation))
    return candidates


def _extract_perf_child_refs(*sources: Any) -> List[ChildEntityRef]:
    refs: List[ChildEntityRef] = []
    seen: set[str] = set()

    for source in sources:
        for item, parent_relation in _iter_perf_candidates(source):
            display_name = _first_text(item.get("title"), item.get("title_text"), item.get("name"), item.get("value"))
            ids_map = _normalize_child_ids_map(
                {
                    "rst_id": _first_text(item.get("rst_id")),
                    "doi": _first_text(item.get("doi")),
                    "issn": _first_text(item.get("issn")),
                }
            )
            role = _first_text(item.get("perf_type"), item.get("tag"), item.get("type"))
            affiliation = _first_text(item.get("org_nm"), item.get("affiliation"))
            if not display_name and not ids_map:
                continue

            identity = _build_subject_identity(
                kind="perf",
                display_name=display_name,
                ids_map=ids_map,
                role=role,
                affiliation=affiliation,
            )
            if identity in seen:
                continue
            seen.add(identity)
            refs.append(
                ChildEntityRef(
                    kind="perf",
                    display_name=display_name or _first_text(*(ids_map.get("rst_id") or []), *(ids_map.get("doi") or []), *(ids_map.get("issn") or [])) or "performance",
                    ids_map=ids_map,
                    role=role or None,
                    affiliation=affiliation or None,
                    parent_relation=parent_relation,
                )
            )
    return refs


def _extract_child_refs_from_canonical(*sources: Any) -> List[ChildEntityRef]:
    refs: List[ChildEntityRef] = []
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        child_entities = source.get("child_entities")
        if not isinstance(child_entities, list):
            continue
        for entity in child_entities:
            if not isinstance(entity, dict):
                continue
            try:
                ref = ChildEntityRef.model_validate(entity)
            except Exception:
                continue
            identity = _first_text(ref.subject_id) or _build_subject_identity(
                kind=ref.kind,
                display_name=ref.display_name,
                ids_map=ref.ids_map,
                role=ref.role,
                affiliation=ref.affiliation,
            )
            if not identity or identity in seen:
                continue
            seen.add(identity)
            refs.append(ref)
    return refs


def _extract_child_refs(*sources: Any) -> List[ChildEntityRef]:
    canonical_refs = _extract_child_refs_from_canonical(*sources)
    if canonical_refs:
        return canonical_refs
    refs: List[ChildEntityRef] = []
    refs.extend(_extract_people_child_refs(*sources))
    refs.extend(_extract_org_child_refs(*sources))
    refs.extend(_extract_perf_child_refs(*sources))
    return refs


def _researcher_names_from_child_refs(child_refs: List[ChildEntityRef]) -> List[str]:
    names: List[str] = []
    for ref in child_refs or []:
        if str(getattr(ref, "kind", "") or "").strip().lower() != "people":
            continue
        name = _first_text(getattr(ref, "display_name", None))
        if name and name not in names:
            names.append(name)
    return names


def _normalize_kind(value: Any, *, default: str) -> str:
    text = str(value or "").strip().lower()
    return text or default


def _infer_entity_kind(*, context_kind: str, doc: Dict[str, Any], evidence: Dict[str, Any], ids: Dict[str, Any], rank_item: Dict[str, Any]) -> str:
    explicit_kind = _first_text(
        doc.get("entity_kind"),
        evidence.get("entity_kind"),
        rank_item.get("entity_kind"),
        doc.get("context_kind"),
        evidence.get("context_kind"),
    )
    if explicit_kind:
        return _normalize_kind(explicit_kind, default="project")

    if _first_text(ids.get("pjt_id"), ids.get("pjt_no"), doc.get("pjt_id"), doc.get("pjt_no"), rank_item.get("pjt_id"), rank_item.get("pjt_no"), rank_item.get("group_key")):
        return "project"
    if _first_text(ids.get("rst_id"), ids.get("doi"), ids.get("issn"), doc.get("rst_id"), doc.get("doi"), doc.get("issn"), rank_item.get("rst_id"), rank_item.get("doi"), rank_item.get("issn")):
        return "perf"
    if _first_text(ids.get("person_no"), doc.get("person_no"), rank_item.get("person_no"), rank_item.get("hm_id"), doc.get("hm_id")):
        return "people"
    if _first_text(ids.get("org_id"), ids.get("org_code"), ids.get("biz_no"), doc.get("org_id"), doc.get("org_code"), doc.get("biz_no"), rank_item.get("org_id"), rank_item.get("org_code"), rank_item.get("biz_no")):
        return "org"

    source_type = _normalize_kind(doc.get("source_type") or evidence.get("source_type"), default=context_kind)
    if "perf" in source_type or source_type in {"paper", "patent", "report", "software", "standard", "compound", "equipment"}:
        return "perf"
    if source_type in {"people", "researcher"}:
        return "people"
    if source_type in {"org", "organization"}:
        return "org"
    return _normalize_kind(context_kind, default="project")


def _extract_rank_item(doc: Dict[str, Any]) -> Dict[str, Any]:
    for key in ("rank_item", "series_item", "pattern_item", "origin_project"):
        value = doc.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _meta_title_candidates(*sources: Any) -> List[str]:
    values: List[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        for title_key in _META_TITLE_KEYS:
            value = _first_text(source.get(title_key))
            if value:
                values.append(value)
        for meta_key in ("meta_basic", "meta_detail"):
            meta = source.get(meta_key)
            if not isinstance(meta, dict):
                continue
            for title_key in _META_TITLE_KEYS:
                value = _first_text(meta.get(title_key))
                if value:
                    values.append(value)
    return values


def _source_title_candidates(source: Any, *, synthetic: bool = False) -> List[str]:
    if not isinstance(source, dict):
        return []
    ordered_keys = ("title", "title_text", "title1", "title2") if synthetic else ("title1", "title_text", "title2", "title")
    values: List[str] = []
    for key in ordered_keys:
        value = _first_text(source.get(key))
        if value:
            values.append(value)
    return values


def _has_canonical_identity(ids: Dict[str, Any], *sources: Any) -> bool:
    if _first_text(
        ids.get("pjt_id"),
        ids.get("pjt_no"),
        ids.get("rst_id"),
        ids.get("person_no"),
        ids.get("org_id"),
        ids.get("org_code"),
        ids.get("biz_no"),
        ids.get("doi"),
        ids.get("issn"),
        ids.get("doc_id"),
    ):
        return True
    for source in sources:
        if not isinstance(source, dict):
            continue
        if _first_text(
            source.get("pjt_id"),
            source.get("pjt_no"),
            source.get("rst_id"),
            source.get("person_no"),
            source.get("hm_id"),
            source.get("org_id"),
            source.get("org_code"),
            source.get("biz_no"),
            source.get("doi"),
            source.get("issn"),
            source.get("doc_id"),
            source.get("group_key"),
        ):
            return True
    return False


def _uses_synthetic_display_title(*sources: Any) -> bool:
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_type = _normalize_kind(source.get("source_type") or source.get("doc_type"), default="")
        if source_type in _SYNTHETIC_TITLE_PRIMARY_TYPES:
            return True
    return False


def _resolve_title_text(
    *,
    doc: Dict[str, Any],
    facts: Dict[str, Any],
    ids: Dict[str, Any],
    rank_item: Dict[str, Any],
    extra_sources: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    sources: List[Dict[str, Any]] = [doc]
    for source in extra_sources or []:
        if isinstance(source, dict):
            sources.append(source)
    meta_titles = _meta_title_candidates(*sources)
    if _uses_synthetic_display_title(doc, *sources[1:]):
        synthetic_titles: List[Any] = []
        for source in sources:
            synthetic_titles.extend(_source_title_candidates(source, synthetic=True))
        return _first_text(
            *synthetic_titles,
            facts.get("title"),
            rank_item.get("project_title"),
            rank_item.get("org_name"),
            rank_item.get("hm_nm"),
            *meta_titles,
        )

    prefer_canonical = bool(
        _has_canonical_identity(ids, doc, rank_item, *sources[1:])
        or _first_text(
            facts.get("title"),
            rank_item.get("project_title"),
            rank_item.get("org_name"),
            rank_item.get("hm_nm"),
            *meta_titles,
        )
    )
    if prefer_canonical:
        primary_source_titles: List[Any] = []
        secondary_source_titles: List[Any] = []
        fallback_titles: List[Any] = []
        for source in sources:
            primary_source_titles.append(source.get("title1"))
            secondary_source_titles.extend((source.get("title_text"), source.get("title2")))
            fallback_titles.append(source.get("title"))
        return _first_text(
            *primary_source_titles,
            *[source.get("kor_pjt_nm") for source in sources if isinstance(source, dict)],
            facts.get("title"),
            rank_item.get("project_title"),
            rank_item.get("org_name"),
            rank_item.get("hm_nm"),
            *meta_titles,
            *secondary_source_titles,
            *fallback_titles,
        )

    generic_titles: List[Any] = []
    for source in sources:
        generic_titles.extend(_source_title_candidates(source))
    return _first_text(
        *generic_titles,
        facts.get("title"),
        rank_item.get("project_title"),
        rank_item.get("org_name"),
        rank_item.get("hm_nm"),
        *meta_titles,
    )


def build_display_snapshot(
    *,
    conversation_id: str,
    turn_id: str,
    context_kind: str,
    requested_count: int,
    documents: Optional[List[Dict[str, Any]]] = None,
    canonical_evidence: Optional[List[Dict[str, Any]]] = None,
    items: Optional[List[Any]] = None,
    raw_count: int,
) -> DisplaySnapshot:
    if items is not None:
        normalized_items: List[DisplayItem] = []
        for index, item in enumerate(items[: max(0, int(requested_count or 0))], start=1):
            if isinstance(item, DisplayItem):
                normalized_items.append(item)
                continue
            display = getattr(item, "display", None) or {}
            canonical = getattr(item, "canonical", None) or {}
            ids = canonical.get("ids") or {}
            facts = canonical.get("facts") or {}
            roles = canonical.get("roles") or {}
            child_refs = _extract_child_refs(display, canonical)
            researchers = [str(v).strip() for v in (roles.get("participant_researcher_name") or []) if str(v).strip()] or _researcher_names_from_child_refs(child_refs)
            normalized_items.append(
                DisplayItem(
                    display_rank=index,
                    entity_kind=_normalize_kind(str((canonical.get("context_kind") or context_kind or "project")).strip().lower(), default="project"),
                    doc_type=_first_text(display.get("doc_type"), canonical.get("source_type"), display.get("source_type")),
                    doc_id=_first_text(display.get("doc_id"), ids.get("doc_id")),
                    col=_first_text(display.get("source_type"), canonical.get("source_type")),
                    title_text=_resolve_title_text(
                        doc=display,
                        facts=facts,
                        ids=ids,
                        rank_item={},
                        extra_sources=[canonical],
                    ) or "",
                    pjt_id=_first_text(display.get("pjt_id"), ids.get("pjt_id")),
                    pjt_no=_first_text(display.get("pjt_no"), ids.get("pjt_no")),
                    rst_id=_first_text(display.get("rst_id"), ids.get("rst_id")),
                    person_no=_first_text(display.get("person_no")),
                    org_id=_first_text(display.get("org_id")),
                    org_code=_first_text(display.get("org_code")),
                    biz_no=_first_text(display.get("biz_no")),
                    doi=_first_text(display.get("doi"), ids.get("doi")),
                    issn=_first_text(display.get("issn"), ids.get("issn")),
                    year=_to_int(facts.get("year")),
                    lead_org=_first_text(*list(roles.get("lead_org_name") or []), display.get("org_nm")),
                    participant_org=[str(v).strip() for v in (roles.get("participant_org_name") or []) if str(v).strip()],
                    researchers=researchers,
                    child_refs=child_refs,
                    score=(float(display.get("score")) if display.get("score") is not None else None),
                )
            )
        return DisplaySnapshot(
            view_id=f"{conversation_id}:{turn_id}:list",
            conversation_id=conversation_id,
            turn_id=turn_id,
            context_kind=str(context_kind or "project").strip().lower() or "project",
            requested_count=max(0, int(requested_count or 0)),
            visible_count=len(normalized_items),
            raw_count=max(len(normalized_items), int(raw_count or 0)),
            items=normalized_items,
        )

    documents = documents or []
    canonical_evidence = canonical_evidence or []
    visible_count = min(max(0, int(requested_count or 0)), max(len(documents), len(canonical_evidence)))
    items: List[DisplayItem] = []
    for index in range(visible_count):
        doc = documents[index] if index < len(documents) and isinstance(documents[index], dict) else {}
        evidence = canonical_evidence[index] if index < len(canonical_evidence) and isinstance(canonical_evidence[index], dict) else {}
        ids = evidence.get("ids") or {}
        facts = evidence.get("facts") or {}
        roles = evidence.get("roles") or {}
        rank_item = _extract_rank_item(doc)
        entity_kind = _infer_entity_kind(context_kind=context_kind, doc=doc, evidence=evidence, ids=ids, rank_item=rank_item)
        child_refs = _extract_child_refs(doc, rank_item, evidence)
        researchers = [str(v).strip() for v in (roles.get("participant_researcher_name") or []) if str(v).strip()] or _researcher_names_from_child_refs(child_refs)
        items.append(
            DisplayItem(
                display_rank=index + 1,
                entity_kind=entity_kind,
                doc_type=_first_text(doc.get("doc_type"), evidence.get("source_type"), doc.get("source_type")),
                doc_id=_first_text(doc.get("doc_id"), ids.get("doc_id"), rank_item.get("doc_id")),
                col=_first_text(doc.get("source_type"), evidence.get("source_type")),
                title_text=_resolve_title_text(
                    doc=doc,
                    facts=facts,
                    ids=ids,
                    rank_item=rank_item,
                    extra_sources=[evidence],
                ) or "",
                pjt_id=_first_text(doc.get("pjt_id"), ids.get("pjt_id"), rank_item.get("pjt_id")),
                pjt_no=_first_text(doc.get("pjt_no"), ids.get("pjt_no"), rank_item.get("pjt_no"), rank_item.get("group_key")),
                rst_id=_first_text(doc.get("rst_id"), ids.get("rst_id"), rank_item.get("rst_id")),
                person_no=_first_text(doc.get("person_no"), rank_item.get("person_no"), rank_item.get("hm_id"), doc.get("hm_id")),
                org_id=_first_text(doc.get("org_id"), rank_item.get("org_id")),
                org_code=_first_text(doc.get("org_code"), rank_item.get("org_code"), doc.get("org_cd"), rank_item.get("org_cd")),
                biz_no=_first_text(doc.get("biz_no"), rank_item.get("biz_no"), doc.get("org_no"), rank_item.get("org_no")),
                doi=_first_text(doc.get("doi"), ids.get("doi"), rank_item.get("doi"), (doc.get("meta_basic") or {}).get("doi"), (doc.get("meta_detail") or {}).get("doi")),
                issn=_first_text(doc.get("issn"), ids.get("issn"), rank_item.get("issn"), (doc.get("meta_basic") or {}).get("issn"), (doc.get("meta_detail") or {}).get("issn")),
                year=_to_int(facts.get("year") or rank_item.get("year")),
                lead_org=_first_text(*list(roles.get("lead_org_name") or []), doc.get("org_nm"), rank_item.get("lead_org_name"), rank_item.get("org_name")),
                participant_org=[str(v).strip() for v in (roles.get("participant_org_name") or []) if str(v).strip()],
                researchers=researchers,
                child_refs=child_refs,
                score=(float(doc.get("score")) if doc.get("score") is not None else None),
            )
        )
    return DisplaySnapshot(
        view_id=f"{conversation_id}:{turn_id}:list",
        conversation_id=conversation_id,
        turn_id=turn_id,
        context_kind=str(context_kind or "project").strip().lower() or "project",
        requested_count=max(0, int(requested_count or 0)),
        visible_count=len(items),
        raw_count=max(len(documents), len(canonical_evidence), int(raw_count or 0)),
        items=items,
    )


def focus_entity_from_item(*, item: DisplayItem, kind: str, source: str, view_id: Optional[str] = None) -> FocusEntity:
    return FocusEntity(
        kind=_normalize_kind(item.entity_kind or kind, default="project"),
        source=source,
        view_id=view_id,
        display_rank=item.display_rank,
        doc_type=item.doc_type,
        doc_id=item.doc_id,
        pjt_id=item.pjt_id,
        pjt_no=item.pjt_no,
        rst_id=item.rst_id,
        person_no=item.person_no,
        org_id=item.org_id,
        org_code=item.org_code,
        biz_no=item.biz_no,
        doi=item.doi,
        issn=item.issn,
        title_text=item.title_text or None,
        year=item.year,
        lead_org=item.lead_org,
        participant_org=list(item.participant_org or []),
        researchers=list(item.researchers or []),
        child_refs=list(item.child_refs or []),
    )


def focus_entity_from_detail(
    *,
    context_kind: str,
    document: Optional[Dict[str, Any]],
    canonical_item: Optional[Dict[str, Any]],
    source: str,
) -> Optional[FocusEntity]:
    doc = document or {}
    evidence = canonical_item or {}
    ids = evidence.get("ids") or {}
    facts = evidence.get("facts") or {}
    rank_item = _extract_rank_item(doc)
    entity_kind = _infer_entity_kind(context_kind=context_kind, doc=doc, evidence=evidence, ids=ids, rank_item=rank_item)
    child_refs = _extract_child_refs(doc, rank_item, evidence)
    title_text = _resolve_title_text(
        doc=doc,
        facts=facts,
        ids=ids,
        rank_item=rank_item,
        extra_sources=[evidence],
    )
    focus = FocusEntity(
        kind=entity_kind,
        source=source,
        doc_type=_first_text(doc.get("doc_type"), evidence.get("source_type"), doc.get("source_type")),
        doc_id=_first_text(doc.get("doc_id"), ids.get("doc_id"), rank_item.get("doc_id")),
        pjt_id=_first_text(doc.get("pjt_id"), ids.get("pjt_id"), rank_item.get("pjt_id")),
        pjt_no=_first_text(doc.get("pjt_no"), ids.get("pjt_no"), rank_item.get("pjt_no"), rank_item.get("group_key")),
        rst_id=_first_text(doc.get("rst_id"), ids.get("rst_id"), rank_item.get("rst_id")),
        person_no=_first_text(doc.get("person_no"), doc.get("hm_id"), rank_item.get("person_no"), rank_item.get("hm_id")),
        org_id=_first_text(doc.get("org_id"), rank_item.get("org_id")),
        org_code=_first_text(doc.get("org_code"), doc.get("org_cd"), rank_item.get("org_code"), rank_item.get("org_cd")),
        biz_no=_first_text(doc.get("biz_no"), doc.get("org_no"), rank_item.get("biz_no"), rank_item.get("org_no")),
        doi=_first_text(doc.get("doi"), ids.get("doi"), rank_item.get("doi"), (doc.get("meta_basic") or {}).get("doi"), (doc.get("meta_detail") or {}).get("doi")),
        issn=_first_text(doc.get("issn"), ids.get("issn"), rank_item.get("issn"), (doc.get("meta_basic") or {}).get("issn"), (doc.get("meta_detail") or {}).get("issn")),
        title_text=title_text,
        year=_to_int(facts.get("year") or doc.get("stan_yr") or (doc.get("meta_basic") or {}).get("stan_yr") or (doc.get("meta_detail") or {}).get("stan_yr")),
        lead_org=_first_text(*list((evidence.get("roles") or {}).get("lead_org_name") or []), doc.get("org_nm"), (doc.get("meta_detail") or {}).get("org_nm"), rank_item.get("lead_org_name"), rank_item.get("org_name")),
        participant_org=[str(v).strip() for v in ((evidence.get("roles") or {}).get("participant_org_name") or []) if str(v).strip()] or _list_texts(doc.get("prtcp_org"), "org_nm"),
        researchers=[str(v).strip() for v in ((evidence.get("roles") or {}).get("participant_researcher_name") or []) if str(v).strip()] or _researcher_names_from_child_refs(child_refs),
        child_refs=child_refs,
    )
    if not any([
        focus.title_text,
        focus.pjt_id,
        focus.pjt_no,
        focus.rst_id,
        focus.person_no,
        focus.org_id,
        focus.org_code,
        focus.biz_no,
        focus.doi,
        focus.issn,
        focus.doc_id,
    ]):
        return None
    return focus


def _focus_ids_map(entity: Optional[FocusEntity]) -> Dict[str, List[str]]:
    if entity is None:
        return {}
    return _normalize_child_ids_map(
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


def _display_item_ids_map(item: DisplayItem) -> Dict[str, List[str]]:
    return _normalize_child_ids_map(
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


def focus_entity_subject_id(entity: Optional[FocusEntity]) -> Optional[str]:
    if entity is None:
        return None
    kind = _normalize_kind(getattr(entity, "kind", None), default="project")
    ids_map = _focus_ids_map(entity)
    if kind == "project":
        if ids_map.get("pjt_id"):
            return f"project:pjt_id:{ids_map['pjt_id'][0]}"
        if ids_map.get("pjt_no"):
            return f"project:pjt_no:{ids_map['pjt_no'][0]}"
    return _build_subject_identity(
        kind=kind,
        display_name=getattr(entity, "title_text", None),
        ids_map=ids_map,
        role=None,
        affiliation=getattr(entity, "lead_org", None),
    )


def _build_subject_index_entry_from_child_ref(
    ref: ChildEntityRef,
    *,
    parent_subject_ids: Optional[List[str]] = None,
    source_turn_id: Optional[str] = None,
    last_seen_turn_id: Optional[str] = None,
) -> SubjectIndexEntry:
    return SubjectIndexEntry(
        kind=ref.kind,
        subject_id=ref.subject_id,
        display_name=ref.display_name,
        aliases=[ref.display_name],
        ids_map=ref.ids_map,
        role=ref.role,
        affiliation=ref.affiliation,
        parent_subject_ids=list(parent_subject_ids or []),
        source_turn_id=source_turn_id,
        last_seen_turn_id=last_seen_turn_id or source_turn_id,
        resolution_state=ref.resolution_state,
    )


def _build_subject_index_entry_from_item(
    item: DisplayItem,
    *,
    turn_id: Optional[str],
) -> Optional[SubjectIndexEntry]:
    kind = _normalize_kind(item.entity_kind, default="project")
    if kind not in {"people", "org", "perf"}:
        return None
    display_name = ""
    affiliation = None
    if kind == "people":
        display_name = _first_text(*(item.researchers or []), item.title_text, item.person_no) or ""
        affiliation = _first_text(item.lead_org)
    elif kind == "org":
        display_name = _first_text(item.lead_org, *(item.participant_org or []), item.title_text) or ""
    else:
        display_name = _first_text(item.title_text, item.rst_id, item.doi, item.issn) or ""
        affiliation = _first_text(item.lead_org)
    ids_map = _display_item_ids_map(item)
    if not display_name and not ids_map:
        return None
    parent_subject_ids: List[str] = []
    project_ids = _normalize_child_ids_map({"pjt_id": item.pjt_id, "pjt_no": item.pjt_no})
    if project_ids and kind != "project":
        if project_ids.get("pjt_id"):
            parent_subject_ids.append(f"project:pjt_id:{project_ids['pjt_id'][0]}")
        elif project_ids.get("pjt_no"):
            parent_subject_ids.append(f"project:pjt_no:{project_ids['pjt_no'][0]}")
    return SubjectIndexEntry(
        kind=kind,
        subject_id=_build_subject_identity(
            kind=kind,
            display_name=display_name,
            ids_map=ids_map,
            role=None,
            affiliation=affiliation,
        ),
        display_name=display_name,
        aliases=[display_name],
        ids_map=ids_map,
        role=None,
        affiliation=affiliation,
        parent_subject_ids=parent_subject_ids,
        source_turn_id=turn_id,
        last_seen_turn_id=turn_id,
        resolution_state="resolved" if _has_subject_ids(ids_map) else "provisional",
    )


def upsert_subject_index_entries(
    view_state: ConversationViewState,
    *,
    entries: List[SubjectIndexEntry],
) -> ConversationViewState:
    normalized = _normalize_view_state(view_state)
    subject_index = dict(normalized.subject_index or {})
    for entry in entries:
        if entry is None:
            continue
        key = _first_text(entry.subject_id)
        if not key:
            continue
        existing = subject_index.get(key)
        if existing is None:
            subject_index[key] = entry
            continue
        merged_ids = dict(existing.ids_map or {})
        for id_key, values in (entry.ids_map or {}).items():
            merged_ids[id_key] = _dedupe_texts([*(merged_ids.get(id_key) or []), *values])
        subject_index[key] = SubjectIndexEntry(
            kind=entry.kind or existing.kind,
            subject_id=key,
            display_name=_first_text(existing.display_name, entry.display_name) or entry.display_name,
            aliases=_dedupe_texts([*(existing.aliases or []), *(entry.aliases or []), existing.display_name, entry.display_name]),
            ids_map=merged_ids,
            role=_first_text(existing.role, entry.role) or None,
            affiliation=_first_text(existing.affiliation, entry.affiliation) or None,
            parent_subject_ids=_dedupe_texts([*(existing.parent_subject_ids or []), *(entry.parent_subject_ids or [])]),
            source_turn_id=_first_text(existing.source_turn_id, entry.source_turn_id) or None,
            last_seen_turn_id=_first_text(entry.last_seen_turn_id, existing.last_seen_turn_id) or None,
            resolution_state="resolved" if (_has_subject_ids(merged_ids) or entry.resolution_state == "resolved" or existing.resolution_state == "resolved") else "provisional",
        )
    return normalized.model_copy(update={"subject_index": subject_index})


def index_focus_subjects(
    view_state: ConversationViewState,
    *,
    focus: Optional[FocusEntity],
    turn_id: Optional[str],
) -> ConversationViewState:
    if focus is None:
        return _normalize_view_state(view_state)
    parent_subject_id = focus_entity_subject_id(focus)
    entries: List[SubjectIndexEntry] = []
    focus_kind = _normalize_kind(getattr(focus, "kind", None), default="project")
    if focus_kind in {"people", "org", "perf"}:
        entries.append(
            SubjectIndexEntry(
                kind=focus_kind,
                subject_id=parent_subject_id or "",
                display_name=_first_text(focus.title_text) or "",
                aliases=[_first_text(focus.title_text) or ""],
                ids_map=_focus_ids_map(focus),
                role=None,
                affiliation=_first_text(getattr(focus, "lead_org", None)) or None,
                parent_subject_ids=[],
                source_turn_id=turn_id,
                last_seen_turn_id=turn_id,
                resolution_state="resolved" if _has_subject_ids(_focus_ids_map(focus)) else "provisional",
            )
        )
    for ref in list(getattr(focus, "child_refs", []) or []):
        entries.append(
            _build_subject_index_entry_from_child_ref(
                ref,
                parent_subject_ids=[parent_subject_id] if parent_subject_id else [],
                source_turn_id=turn_id,
                last_seen_turn_id=turn_id,
            )
        )
    return upsert_subject_index_entries(view_state, entries=entries)


def index_snapshot_subjects(
    view_state: ConversationViewState,
    *,
    snapshot: Optional[DisplaySnapshot],
    turn_id: Optional[str],
) -> ConversationViewState:
    if snapshot is None:
        return _normalize_view_state(view_state)
    entries: List[SubjectIndexEntry] = []
    for item in list(snapshot.items or []):
        entry = _build_subject_index_entry_from_item(item, turn_id=turn_id)
        if entry is not None:
            entries.append(entry)
        parent_subject_ids: List[str] = []
        project_ids = _normalize_child_ids_map({"pjt_id": item.pjt_id, "pjt_no": item.pjt_no})
        if project_ids.get("pjt_id"):
            parent_subject_ids.append(f"project:pjt_id:{project_ids['pjt_id'][0]}")
        elif project_ids.get("pjt_no"):
            parent_subject_ids.append(f"project:pjt_no:{project_ids['pjt_no'][0]}")
        for ref in list(item.child_refs or []):
            entries.append(
                _build_subject_index_entry_from_child_ref(
                    ref,
                    parent_subject_ids=parent_subject_ids,
                    source_turn_id=turn_id,
                    last_seen_turn_id=turn_id,
                )
            )
    return upsert_subject_index_entries(view_state, entries=entries)


def set_visible_answer_manifest(
    view_state: ConversationViewState,
    *,
    snapshot: Optional[DisplaySnapshot],
) -> ConversationViewState:
    normalized = _normalize_view_state(view_state)
    return normalized.model_copy(update={"visible_answer_manifest": snapshot})


def set_active_result_scope(
    view_state: ConversationViewState,
    *,
    snapshot: DisplaySnapshot,
    output_type: Optional[str],
    context_kind: Optional[str],
    turn_id: Optional[str],
    scope_kind: Optional[str] = None,
    reason: Optional[str] = None,
) -> ConversationViewState:
    normalized = _normalize_view_state(view_state)
    active_scope = normalized.active_scope
    resolved_scope_kind = _normalize_scope_kind(scope_kind or ("child" if active_scope.child_anchor is not None else "list"))
    next_scope = active_scope.model_copy(
        update={
            "result_set": snapshot,
            "scope_kind": resolved_scope_kind,
            "parent_chain": _append_scope_transition(
                active_scope.parent_chain,
                scope_kind=resolved_scope_kind,
                turn_id=turn_id,
                view_id=getattr(snapshot, "view_id", None),
                entity_kind=str(context_kind or snapshot.context_kind or "").strip().lower() or None,
                anchor_kind=getattr(active_scope.child_anchor, "kind", None),
                reason=reason or str(output_type or "").strip().lower() or "result_set_update",
            ),
        }
    )
    return normalized.model_copy(
        update={
            "active_scope": next_scope,
            "active_result_set_kind": output_type,
            "active_result_view_id": getattr(snapshot, "view_id", None),
            "entity_scope": context_kind or snapshot.context_kind,
        }
    )


def set_active_focus_scope(
    view_state: ConversationViewState,
    *,
    focus: FocusEntity,
    turn_id: Optional[str],
    scope_kind: Optional[str] = None,
    reason: Optional[str] = None,
    preserve_child_anchor: bool = True,
) -> ConversationViewState:
    normalized = _normalize_view_state(view_state)
    active_scope = normalized.active_scope
    next_scope = active_scope.model_copy(
        update={
            "focus": focus,
            "scope_kind": _normalize_scope_kind(scope_kind or "detail"),
            "child_anchor": active_scope.child_anchor if preserve_child_anchor else None,
            "parent_chain": _append_scope_transition(
                active_scope.parent_chain,
                scope_kind=scope_kind or "detail",
                turn_id=turn_id,
                entity_kind=getattr(focus, "kind", None),
                view_id=getattr(focus, "view_id", None),
                anchor_kind=getattr(active_scope.child_anchor, "kind", None) if preserve_child_anchor else None,
                reason=reason or str(getattr(focus, "source", "") or "").strip().lower() or "focus_update",
            ),
        }
    )
    return normalized.model_copy(
        update={
            "active_scope": next_scope,
            "entity_scope": getattr(focus, "kind", None) or normalized.entity_scope,
        }
    )


def set_active_child_anchor_scope(
    view_state: ConversationViewState,
    *,
    anchor: FocusEntity,
    turn_id: Optional[str],
    reason: Optional[str] = None,
) -> ConversationViewState:
    normalized = _normalize_view_state(view_state)
    active_scope = normalized.active_scope
    next_scope = active_scope.model_copy(
        update={
            "child_anchor": anchor,
            "scope_kind": _normalize_scope_kind("child"),
            "parent_chain": _append_scope_transition(
                active_scope.parent_chain,
                scope_kind="child",
                turn_id=turn_id,
                entity_kind=getattr(active_scope.focus, "kind", None),
                view_id=getattr(active_scope.result_set, "view_id", None) or getattr(anchor, "view_id", None),
                anchor_kind=getattr(anchor, "kind", None),
                reason=reason or str(getattr(anchor, "source", "") or "").strip().lower() or "child_anchor_update",
            ),
        }
    )
    return normalized.model_copy(
        update={
            "active_scope": next_scope,
            "entity_scope": getattr(anchor, "kind", None) or normalized.entity_scope,
        }
    )


def clear_view_state_scope(view_state: ConversationViewState) -> ConversationViewState:
    normalized = _normalize_view_state(view_state)
    next_scope = normalized.active_scope.model_copy(
        update={
            "result_set": None,
            "focus": None,
            "child_anchor": None,
            "scope_kind": "fresh",
            "parent_chain": _append_scope_transition(
                normalized.active_scope.parent_chain,
                scope_kind="fresh",
                turn_id=None,
                reason="scope_reset",
            ),
        }
    )
    return normalized.model_copy(
        update={
            "active_scope": next_scope,
            "visible_answer_manifest": None,
            "active_result_set_kind": None,
            "active_result_view_id": None,
        }
    )


def render_display_snapshot_text(snapshot: Optional[DisplaySnapshot], *, max_chars: int = 1200) -> str:
    if snapshot is None or not snapshot.items:
        return "NONE"
    lines = [f"[DisplaySnapshot] view_id={snapshot.view_id} kind={snapshot.context_kind} visible={snapshot.visible_count}/{snapshot.raw_count}"]
    for item in snapshot.items:
        parts = [f"{item.display_rank}. {item.title_text or 'item'}", f"ENTITY={item.entity_kind}"]
        if item.pjt_id:
            parts.append(f"PJT_ID={item.pjt_id}")
        if item.pjt_no:
            parts.append(f"PJT_NO={item.pjt_no}")
        if item.rst_id:
            parts.append(f"RST_ID={item.rst_id}")
        if item.person_no:
            parts.append(f"PERSON_NO={item.person_no}")
        if item.org_id:
            parts.append(f"ORG_ID={item.org_id}")
        if item.org_code:
            parts.append(f"ORG_CODE={item.org_code}")
        if item.biz_no:
            parts.append(f"BIZ_NO={item.biz_no}")
        if item.doi:
            parts.append(f"DOI={item.doi}")
        if item.issn:
            parts.append(f"ISSN={item.issn}")
        if item.year is not None:
            parts.append(f"YEAR={item.year}")
        if item.lead_org:
            parts.append(f"LEAD_ORG={item.lead_org}")
        lines.append(" | ".join(parts))
        joined = "\n".join(lines)
        if max_chars > 0 and len(joined) >= max_chars:
            return joined[:max_chars]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# recent_mentions helpers
# ---------------------------------------------------------------------------

def _mention_dedup_key(m: RecentMentionRecord) -> str:
    """중복 판별용 키. project는 pjt_id 우선, perf는 rst_id/doi/issn 우선."""
    kind = str(m.entity_kind or "").strip().lower()
    if kind == "project":
        return f"project:{m.pjt_id or ''}/{m.pjt_no or ''}"
    if kind == "perf":
        return f"perf:{m.rst_id or ''}/{m.doi or ''}/{m.issn or ''}"
    if kind == "people":
        return f"people:{m.title_text or ''}"
    if kind == "org":
        return f"org:{m.lead_org or ''}/{m.title_text or ''}"
    return f"{kind}:{m.title_text or ''}"


def recent_mention_from_display_item(
    item: DisplayItem,
    *,
    source: Literal["list_snapshot", "detail_focus", "child_anchor"] = "list_snapshot",
    turn_index: Optional[int] = None,
) -> RecentMentionRecord:
    """DisplayItem에서 RecentMentionRecord를 생성한다."""
    return RecentMentionRecord(
        entity_kind=_normalize_kind(item.entity_kind, default="project"),  # type: ignore[arg-type]
        title_text=_first_text(item.title_text),
        pjt_id=_first_text(item.pjt_id),
        pjt_no=_first_text(item.pjt_no),
        rst_id=_first_text(item.rst_id),
        doi=_first_text(item.doi),
        issn=_first_text(item.issn),
        year=str(item.year) if item.year is not None else None,
        lead_org=_first_text(item.lead_org),
        participant_org=list(item.participant_org or []),
        researchers=list(item.researchers or []),
        source=source,
        display_rank=item.display_rank,
        turn_index=turn_index,
    )


def recent_mention_from_focus_entity(
    focus: FocusEntity,
    *,
    source: Literal["list_snapshot", "detail_focus", "child_anchor"] = "detail_focus",
    turn_index: Optional[int] = None,
) -> RecentMentionRecord:
    """FocusEntity에서 RecentMentionRecord를 생성한다."""
    return RecentMentionRecord(
        entity_kind=_normalize_kind(focus.kind, default="project"),  # type: ignore[arg-type]
        title_text=_first_text(focus.title_text),
        pjt_id=_first_text(focus.pjt_id),
        pjt_no=_first_text(focus.pjt_no),
        rst_id=_first_text(focus.rst_id),
        doi=_first_text(focus.doi),
        issn=_first_text(focus.issn),
        year=str(focus.year) if focus.year is not None else None,
        lead_org=_first_text(focus.lead_org),
        participant_org=list(focus.participant_org or []),
        researchers=list(focus.researchers or []),
        source=source,
        display_rank=focus.display_rank,
        turn_index=turn_index,
    )


def append_recent_mentions(
    view_state: ConversationViewState,
    mentions: List[RecentMentionRecord],
    *,
    max_items: int = 12,
) -> ConversationViewState:
    """view_state.recent_mentions에 mentions를 머지하여 추가한다.

    동일 entity는 최신 레코드로 교체하고, 최대 max_items개만 유지한다.
    """
    existing = list(view_state.recent_mentions or [])
    existing_by_key: Dict[str, RecentMentionRecord] = {}
    for m in existing:
        existing_by_key[_mention_dedup_key(m)] = m

    for m in mentions:
        existing_by_key[_mention_dedup_key(m)] = m

    merged = list(existing_by_key.values())[-max_items:]
    return view_state.model_copy(update={"recent_mentions": merged})


def get_recent_mentions(
    view_state: Optional[ConversationViewState],
    entity_kind: Optional[str] = None,
) -> List[RecentMentionRecord]:
    """view_state에서 recent_mentions를 반환한다. entity_kind로 필터 가능."""
    if view_state is None:
        return []
    mentions = list(getattr(view_state, "recent_mentions", []) or [])
    if entity_kind:
        kind = str(entity_kind).strip().lower()
        mentions = [m for m in mentions if str(m.entity_kind or "").strip().lower() == kind]
    return mentions
