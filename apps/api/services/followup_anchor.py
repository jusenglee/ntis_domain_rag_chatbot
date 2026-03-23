from __future__ import annotations

import re
from typing import Any, Dict, Optional

from apps.api.services.view_state import DisplaySnapshot, FocusEntity, focus_entity_from_item


_ORDINAL_PATTERNS = (
    re.compile(r"(?:제\s*)?(\d{1,3})\s*번째"),
    re.compile(r"(?:제\s*)?(\d{1,3})\s*번"),
)
_ORDINAL_WORDS = {
    "첫": 1,
    "첫번째": 1,
    "첫 번째": 1,
    "첫째": 1,
    "두": 2,
    "두번째": 2,
    "두 번째": 2,
    "둘째": 2,
    "세": 3,
    "세번째": 3,
    "세 번째": 3,
    "셋째": 3,
}
_DEICTIC_PATTERNS = (
    re.compile(r"그\s*과제"),
    re.compile(r"이\s*과제"),
    re.compile(r"해당\s*과제"),
    re.compile(r"방금\s*과제"),
)
_COUNT_PATTERNS = (
    re.compile(r"상위\s*(\d{1,3})\s*개"),
    re.compile(r"(\d{1,3})\s*(?:개|건)"),
    re.compile(r"(한|두|세)\s*건"),
)
_KOREAN_COUNT = {"한": 1, "두": 2, "세": 3}


def parse_ordinal_reference(question: str) -> Optional[int]:
    text = str(question or "").strip()
    if not text:
        return None
    for pattern in _ORDINAL_PATTERNS:
        match = pattern.search(text)
        if match:
            ordinal = int(match.group(1))
            return ordinal if ordinal > 0 else None
    compact = re.sub(r"\s+", "", text)
    for token, ordinal in _ORDINAL_WORDS.items():
        if token.replace(" ", "") in compact:
            return ordinal
    return None


def is_referential_followup(question: str) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in _DEICTIC_PATTERNS)


def parse_display_limit(question: str, *, default: int) -> int:
    text = str(question or "").strip()
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = str(match.group(1)).strip()
        if raw.isdigit():
            return max(1, int(raw))
        if raw in _KOREAN_COUNT:
            return _KOREAN_COUNT[raw]
    return max(1, int(default or 1))


def resolve_followup_anchor(
    *,
    question: str,
    normalized_intent: Any,
    latest_display_snapshot: Optional[DisplaySnapshot],
    latest_focus_entity: Optional[FocusEntity],
) -> Optional[FocusEntity]:
    ids_map = getattr(normalized_intent, "ids_map", {}) or {}
    pjt_ids = list(ids_map.get("pjt_id") or [])
    pjt_nos = list(ids_map.get("pjt_no") or [])
    if pjt_ids or pjt_nos:
        return FocusEntity(
            kind=str(getattr(normalized_intent, "base_route", None) or "project").strip().lower() or "project",
            source="explicit_id",
            pjt_id=str(pjt_ids[0]).strip() if pjt_ids else None,
            pjt_no=str(pjt_nos[0]).strip() if pjt_nos else None,
        )

    ordinal = parse_ordinal_reference(question)
    if ordinal is not None and latest_display_snapshot and 1 <= ordinal <= len(latest_display_snapshot.items):
        item = latest_display_snapshot.items[ordinal - 1]
        return focus_entity_from_item(
            item=item,
            kind=latest_display_snapshot.context_kind,
            source="display_snapshot",
            view_id=latest_display_snapshot.view_id,
        )

    if is_referential_followup(question) and latest_focus_entity is not None:
        return latest_focus_entity

    if is_referential_followup(question) and latest_display_snapshot and len(latest_display_snapshot.items) == 1:
        item = latest_display_snapshot.items[0]
        return focus_entity_from_item(
            item=item,
            kind=latest_display_snapshot.context_kind,
            source="display_snapshot",
            view_id=latest_display_snapshot.view_id,
        )
    return None


def anchor_to_seed_map(anchor: Optional[FocusEntity]) -> Dict[str, list[str]]:
    if anchor is None:
        return {}
    if anchor.pjt_id:
        return {"pjt_id": [anchor.pjt_id]}
    if anchor.pjt_no:
        return {"pjt_no": [anchor.pjt_no]}
    return {}
