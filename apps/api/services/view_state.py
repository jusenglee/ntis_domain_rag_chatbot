from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DisplayItem(BaseModel):
    display_rank: int
    doc_id: Optional[str] = None
    col: Optional[str] = None
    title_text: str = ""
    pjt_id: Optional[str] = None
    pjt_no: Optional[str] = None
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
    doc_id: Optional[str] = None
    pjt_id: Optional[str] = None
    pjt_no: Optional[str] = None
    title_text: Optional[str] = None


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


class ConversationViewState(BaseModel):
    latest_display_snapshot: Optional[DisplaySnapshot] = None
    latest_focus_entity: Optional[FocusEntity] = None
    detail_cache: Dict[str, DetailCacheEntry] = Field(default_factory=dict)
    raw_candidates_cache: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)


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


def build_display_snapshot(
    *,
    conversation_id: str,
    turn_id: str,
    context_kind: str,
    requested_count: int,
    documents: List[Dict[str, Any]],
    canonical_evidence: List[Dict[str, Any]],
    raw_count: int,
) -> DisplaySnapshot:
    visible_count = min(max(0, int(requested_count or 0)), len(documents))
    items: List[DisplayItem] = []
    for index in range(visible_count):
        doc = documents[index] if index < len(documents) and isinstance(documents[index], dict) else {}
        evidence = canonical_evidence[index] if index < len(canonical_evidence) and isinstance(canonical_evidence[index], dict) else {}
        ids = evidence.get("ids") or {}
        facts = evidence.get("facts") or {}
        roles = evidence.get("roles") or {}
        items.append(
            DisplayItem(
                display_rank=index + 1,
                doc_id=_first_text(doc.get("doc_id"), ids.get("doc_id")),
                col=_first_text(doc.get("source_type"), evidence.get("source_type")),
                title_text=_first_text(doc.get("title"), facts.get("title")) or "",
                pjt_id=_first_text(doc.get("pjt_id"), ids.get("pjt_id")),
                pjt_no=_first_text(doc.get("pjt_no"), ids.get("pjt_no")),
                year=_to_int(facts.get("year")),
                lead_org=_first_text(*list(roles.get("lead_org_name") or [])),
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
        kind=str(kind or "project").strip().lower() or "project",
        source=source,
        view_id=view_id,
        display_rank=item.display_rank,
        doc_id=item.doc_id,
        pjt_id=item.pjt_id,
        pjt_no=item.pjt_no,
        title_text=item.title_text or None,
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
    title_text = _first_text(doc.get("title"), facts.get("title"))
    pjt_id = _first_text(doc.get("pjt_id"), ids.get("pjt_id"))
    pjt_no = _first_text(doc.get("pjt_no"), ids.get("pjt_no"))
    doc_id = _first_text(doc.get("doc_id"), ids.get("doc_id"))
    if not any([title_text, pjt_id, pjt_no, doc_id]):
        return None
    return FocusEntity(
        kind=str(context_kind or "project").strip().lower() or "project",
        source=source,
        doc_id=doc_id,
        pjt_id=pjt_id,
        pjt_no=pjt_no,
        title_text=title_text,
    )


def render_display_snapshot_text(snapshot: Optional[DisplaySnapshot], *, max_chars: int = 1200) -> str:
    if snapshot is None or not snapshot.items:
        return "NONE"
    lines = [f"[DisplaySnapshot] view_id={snapshot.view_id} kind={snapshot.context_kind} visible={snapshot.visible_count}/{snapshot.raw_count}"]
    for item in snapshot.items:
        parts = [f"{item.display_rank}. {item.title_text or 'item'}"]
        if item.pjt_id:
            parts.append(f"PJT_ID={item.pjt_id}")
        if item.pjt_no:
            parts.append(f"PJT_NO={item.pjt_no}")
        if item.year is not None:
            parts.append(f"YEAR={item.year}")
        if item.lead_org:
            parts.append(f"LEAD_ORG={item.lead_org}")
        lines.append(" | ".join(parts))
        joined = "`n".join(lines)
        if max_chars > 0 and len(joined) >= max_chars:
            return joined[:max_chars]
    return "`n".join(lines)
