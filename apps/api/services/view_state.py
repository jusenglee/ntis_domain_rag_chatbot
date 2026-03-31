from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

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
    role: Optional[str] = None
    affiliation: Optional[str] = None
    parent_relation: Optional[str] = None


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


class ConversationViewState(BaseModel):
    latest_display_snapshot: Optional[DisplaySnapshot] = None
    latest_focus_entity: Optional[FocusEntity] = None
    active_scope: ActiveScope = Field(default_factory=ActiveScope)
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


def _focus_entity_identity(entity: Optional[FocusEntity]) -> tuple[Any, ...]:
    if entity is None:
        return ()
    return (
        getattr(entity, "kind", None),
        getattr(entity, "pjt_id", None),
        getattr(entity, "pjt_no", None),
        getattr(entity, "rst_id", None),
        getattr(entity, "person_no", None),
        getattr(entity, "org_id", None),
        getattr(entity, "org_code", None),
        getattr(entity, "biz_no", None),
        getattr(entity, "doi", None),
        getattr(entity, "issn", None),
        getattr(entity, "doc_id", None),
        getattr(entity, "title_text", None),
    )


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


def _normalize_view_state(view_state: ConversationViewState) -> ConversationViewState:
    active_scope = getattr(view_state, "active_scope", None)
    if not isinstance(active_scope, ActiveScope):
        try:
            active_scope = ActiveScope.model_validate(active_scope or {})
        except Exception:
            active_scope = ActiveScope()

    result_set = active_scope.result_set or getattr(view_state, "latest_display_snapshot", None)
    child_anchor = active_scope.child_anchor
    focus = active_scope.focus
    legacy_focus = getattr(view_state, "latest_focus_entity", None)
    if focus is None and legacy_focus is not None:
        focus = legacy_focus
    if child_anchor is None and legacy_focus is not None and _focus_entity_identity(legacy_focus) != _focus_entity_identity(focus):
        child_anchor = legacy_focus

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
    if normalized_scope.scope_kind == "detail" and normalized_scope.focus is not None:
        legacy_focus_out = normalized_scope.focus
    elif normalized_scope.scope_kind == "child" and normalized_scope.child_anchor is not None:
        legacy_focus_out = normalized_scope.child_anchor
    else:
        legacy_focus_out = normalized_scope.focus or normalized_scope.child_anchor
    return view_state.model_copy(
        update={
            "active_scope": normalized_scope,
            "latest_display_snapshot": normalized_scope.result_set,
            "latest_focus_entity": legacy_focus_out,
            "active_result_view_id": getattr(normalized_scope.result_set, "view_id", None),
        }
    )


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


def _build_child_identity(*, kind: str, display_name: Optional[str], ids_map: Dict[str, List[str]], role: Optional[str], affiliation: Optional[str]) -> str:
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

        identity = _build_child_identity(
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

        identity = _build_child_identity(
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

            identity = _build_child_identity(
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


def _extract_child_refs(*sources: Any) -> List[ChildEntityRef]:
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
            "latest_display_snapshot": snapshot,
            "active_result_set_kind": output_type,
            "active_result_view_id": getattr(snapshot, "view_id", None),
            "entity_scope": context_kind or snapshot.context_kind,
            "latest_focus_entity": (next_scope.child_anchor if resolved_scope_kind == "child" else (next_scope.focus or next_scope.child_anchor)),
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
            "latest_focus_entity": focus,
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
            "latest_focus_entity": anchor,
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
            "latest_display_snapshot": None,
            "latest_focus_entity": None,
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
