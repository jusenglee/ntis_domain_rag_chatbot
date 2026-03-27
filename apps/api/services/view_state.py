from __future__ import annotations

from typing import Any, Dict, List, Optional

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


class ConversationViewState(BaseModel):
    latest_display_snapshot: Optional[DisplaySnapshot] = None
    latest_focus_entity: Optional[FocusEntity] = None
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
        return payload
    if isinstance(payload, dict):
        try:
            return ConversationViewState.model_validate(payload)
        except Exception:
            return ConversationViewState()
    return ConversationViewState()


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
                    researchers=[str(v).strip() for v in (roles.get("participant_researcher_name") or []) if str(v).strip()],
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
                researchers=[str(v).strip() for v in (roles.get("participant_researcher_name") or []) if str(v).strip()],
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
        researchers=[str(v).strip() for v in ((evidence.get("roles") or {}).get("participant_researcher_name") or []) if str(v).strip()] or _list_texts(doc.get("prtcp_mp"), "hm_nm"),
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
