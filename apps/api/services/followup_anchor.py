from __future__ import annotations

import re
from typing import Any, Dict, Optional

from apps.api.services.view_state import DisplaySnapshot, FocusEntity, focus_entity_from_item


_ORDINAL_PATTERNS = (
    re.compile(r"(?:\uc81c\s*)?(\d{1,3})\s*\ubc88\uc9f8"),
    re.compile(r"(?:\uc81c\s*)?(\d{1,3})\s*\ubc88"),
)
_ORDINAL_WORDS = {
    "\uccab": 1,
    "\uccab\ubc88\uc9f8": 1,
    "\uccab \ubc88\uc9f8": 1,
    "\uccab\uc9f8": 1,
    "\ub450": 2,
    "\ub450\ubc88\uc9f8": 2,
    "\ub450 \ubc88\uc9f8": 2,
    "\ub458\uc9f8": 2,
    "\uc138": 3,
    "\uc138\ubc88\uc9f8": 3,
    "\uc138 \ubc88\uc9f8": 3,
    "\uc14b\uc9f8": 3,
}
_DEICTIC_PATTERNS = {
    "project": (
        re.compile(r"\uadf8\s*\uacfc\uc81c"),
        re.compile(r"\uc774\s*\uacfc\uc81c"),
        re.compile(r"\ud574\ub2f9\s*\uacfc\uc81c"),
        re.compile(r"\ubc29\uae08\s*\uacfc\uc81c"),
    ),
    "perf": (
        re.compile(r"\uadf8\s*(?:\uc131\uacfc|\ub17c\ubb38|\ud2b9\ud5c8|\ubcf4\uace0\uc11c|\uae30\uc220)"),
        re.compile(r"\uc774\s*(?:\uc131\uacfc|\ub17c\ubb38|\ud2b9\ud5c8|\ubcf4\uace0\uc11c|\uae30\uc220)"),
        re.compile(r"\ud574\ub2f9\s*(?:\uc131\uacfc|\ub17c\ubb38|\ud2b9\ud5c8|\ubcf4\uace0\uc11c|\uae30\uc220)"),
    ),
    "people": (
        re.compile(r"\uadf8\s*(?:\uc5f0\uad6c\uc790|\uc5f0\uad6c\uc6d0|\uc0ac\ub78c)"),
        re.compile(r"\uc774\s*(?:\uc5f0\uad6c\uc790|\uc5f0\uad6c\uc6d0|\uc0ac\ub78c)"),
        re.compile(r"\ud574\ub2f9\s*(?:\uc5f0\uad6c\uc790|\uc5f0\uad6c\uc6d0|\uc0ac\ub78c)"),
    ),
    "org": (
        re.compile(r"\uadf8\s*(?:\uae30\uad00|\ud68c\uc0ac|\uc870\uc9c1)"),
        re.compile(r"\uc774\s*(?:\uae30\uad00|\ud68c\uc0ac|\uc870\uc9c1)"),
        re.compile(r"\ud574\ub2f9\s*(?:\uae30\uad00|\ud68c\uc0ac|\uc870\uc9c1)"),
    ),
    "generic": (
        re.compile(r"\uadf8\s*(?:\ud56d\ubaa9|\uacb0\uacfc|\uc774\uac70|\uc774\uac83)"),
        re.compile(r"\uc774\s*(?:\ud56d\ubaa9|\uacb0\uacfc|\uac83)"),
    ),
}
_COUNT_PATTERNS = (
    re.compile(r"\uc0c1\uc704\s*(\d{1,3})\s*\uac1c"),
    re.compile(r"(\d{1,3})\s*(?:\uac1c|\uac74)"),
    re.compile(r"(\ud55c|\ub450|\uc138)\s*\uac74"),
)
_KOREAN_COUNT = {"\ud55c": 1, "\ub450": 2, "\uc138": 3}
_EXPLICIT_ID_KEYS = ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn")


def _decode_token(token: str) -> str:
    return token.encode("utf-8").decode("unicode_escape")


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
        if _decode_token(token).replace(" ", "") in compact:
            return ordinal
    return None


def _entity_kind_matches_question(question: str, entity_kind: str) -> bool:
    patterns = list(_DEICTIC_PATTERNS.get(entity_kind, ())) + list(_DEICTIC_PATTERNS["generic"])
    return any(pattern.search(question) for pattern in patterns)


def is_referential_followup(question: str, *, entity_kind: Optional[str] = None) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    if entity_kind:
        return _entity_kind_matches_question(text, str(entity_kind or "").strip().lower())
    return any(pattern.search(text) for patterns in _DEICTIC_PATTERNS.values() for pattern in patterns)


def parse_display_limit(question: str, *, default: int) -> int:
    text = str(question or "").strip()
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = str(match.group(1)).strip()
        if raw.isdigit():
            return max(1, int(raw))
        raw_decoded = _decode_token(raw)
        for token, count in _KOREAN_COUNT.items():
            if raw_decoded == _decode_token(token):
                return count
    return max(1, int(default or 1))


def _normalize_ids_map(values: Any) -> Dict[str, list[str]]:
    if not isinstance(values, dict):
        return {}
    normalized: Dict[str, list[str]] = {}
    for key, raw_values in values.items():
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        elif not isinstance(raw_values, (list, tuple, set)):
            raw_values = [raw_values]
        deduped: list[str] = []
        seen: set[str] = set()
        for raw in raw_values:
            text = str(raw or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        if deduped:
            normalized[str(key).strip()] = deduped
    return normalized


def resolve_followup_anchor(*, question: str, normalized_intent: Any, latest_display_snapshot: Optional[DisplaySnapshot], latest_focus_entity: Optional[FocusEntity]) -> Optional[FocusEntity]:
    ids_map = _normalize_ids_map(getattr(normalized_intent, "ids_map", {}) or {})
    for key in _EXPLICIT_ID_KEYS:
        values = ids_map.get(key) or []
        if not values:
            continue
        return FocusEntity(
            kind=str(getattr(normalized_intent, "base_route", None) or "project").strip().lower() or "project",
            source="explicit_id",
            pjt_id=values[0] if key == "pjt_id" else None,
            pjt_no=values[0] if key == "pjt_no" else None,
            rst_id=values[0] if key == "rst_id" else None,
            person_no=values[0] if key == "person_no" else None,
            org_id=values[0] if key == "org_id" else None,
            org_code=values[0] if key == "org_code" else None,
            biz_no=values[0] if key == "biz_no" else None,
            doi=values[0] if key == "doi" else None,
            issn=values[0] if key == "issn" else None,
        )

    ordinal = parse_ordinal_reference(question)
    if ordinal is not None and latest_display_snapshot and 1 <= ordinal <= len(latest_display_snapshot.items):
        item = latest_display_snapshot.items[ordinal - 1]
        return focus_entity_from_item(item=item, kind=latest_display_snapshot.context_kind, source="display_snapshot", view_id=latest_display_snapshot.view_id)

    if latest_focus_entity is not None and is_referential_followup(question, entity_kind=latest_focus_entity.kind):
        return latest_focus_entity

    if is_referential_followup(question) and latest_display_snapshot and len(latest_display_snapshot.items) == 1:
        item = latest_display_snapshot.items[0]
        return focus_entity_from_item(item=item, kind=latest_display_snapshot.context_kind, source="display_snapshot", view_id=latest_display_snapshot.view_id)
    return None


def anchor_to_seed_map(anchor: Optional[FocusEntity]) -> Dict[str, list[str]]:
    if anchor is None:
        return {}
    seed_map = {
        "pjt_id": [anchor.pjt_id] if anchor.pjt_id else [],
        "pjt_no": [anchor.pjt_no] if anchor.pjt_no else [],
        "rst_id": [anchor.rst_id] if anchor.rst_id else [],
        "person_no": [anchor.person_no] if anchor.person_no else [],
        "org_id": [anchor.org_id] if anchor.org_id else [],
        "org_code": [anchor.org_code] if anchor.org_code else [],
        "biz_no": [anchor.biz_no] if anchor.biz_no else [],
        "doi": [anchor.doi] if anchor.doi else [],
        "issn": [anchor.issn] if anchor.issn else [],
    }
    return {key: values for key, values in seed_map.items() if values}
