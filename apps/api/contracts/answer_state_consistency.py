from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from apps.api.contracts.answer_groundedness import BYPASS_ANSWER_KINDS


AnswerStateConsistencyStatus = Literal[
    "not_applicable",
    "insufficient_snapshot",
    "no_structured_list",
    "supported",
    "unsupported_count",
    "unsupported_order",
    "unsupported_item_identity",
]


class AnswerStateSnapshotItem(BaseModel):
    display_rank: int
    entity_kind: str = "project"
    title_text: str = ""
    ids_map: dict[str, list[str]] = Field(default_factory=dict)
    year: Optional[int] = None
    lead_org: Optional[str] = None


class AnswerStateMismatch(BaseModel):
    rank: Optional[int] = None
    expected_rank: Optional[int] = None
    observed_rank: Optional[int] = None
    expected_title: Optional[str] = None
    observed_title: Optional[str] = None
    expected_ids: dict[str, list[str]] = Field(default_factory=dict)
    observed_ids: dict[str, list[str]] = Field(default_factory=dict)
    reason: str


class AnswerStateSnapshot(BaseModel):
    available: bool = False
    snapshot_source: Literal["active_result_set", "none"] = "none"
    context_kind: str = "project"
    visible_count: int = 0
    ordered_items: list[AnswerStateSnapshotItem] = Field(default_factory=list)


class AnswerStateConsistencyVerdict(BaseModel):
    status: AnswerStateConsistencyStatus
    reason_codes: list[str] = Field(default_factory=list)
    checked_items: int = 0
    mismatches: list[AnswerStateMismatch] = Field(default_factory=list)


class _ParsedAnswerItem(BaseModel):
    observed_rank: int
    title_text: str = ""
    ids_map: dict[str, list[str]] = Field(default_factory=dict)
    year: Optional[int] = None
    lead_org: Optional[str] = None


_TOP_LEVEL_NUMBERED = re.compile(r"^\s*(\d+)[\.\)]\s+(.*\S)?\s*$")
_TOP_LEVEL_BULLET = re.compile(r"^\s*[-*•]\s+(.*\S)?\s*$")
_TITLE_CLEANUP = re.compile(r"\[(?:출처|source)\s*\d+\]", re.IGNORECASE)
_PROJECT_ID_PATTERNS = (
    re.compile(r"(?:pjt[_\s-]?id|project[_\s-]?id)\s*(?:[:=]\s*|\()\s*([A-Za-z0-9-]{4,})", re.IGNORECASE),
    re.compile(r"\bPJT[_\s-]?ID\s*[:=]\s*([A-Za-z0-9-]{4,})", re.IGNORECASE),
)
_PROJECT_NO_PATTERNS = (
    re.compile(r"(?:pjt[_\s-]?no|project[_\s-]?no)\s*(?:[:=]\s*|\()\s*([A-Za-z0-9-]{4,})", re.IGNORECASE),
)
_PERF_ID_PATTERNS = (
    re.compile(r"(?:rst[_\s-]?id|perf[_\s-]?id)\s*(?:[:=]\s*|\()\s*([A-Za-z0-9-]{4,})", re.IGNORECASE),
)
_DOI_PATTERNS = (
    re.compile(r"\bdoi\s*(?:[:=]\s*|\()\s*([^\s\),]+)", re.IGNORECASE),
)
_ISSN_PATTERNS = (
    re.compile(r"\bissn\s*(?:[:=]\s*|\()\s*([0-9Xx-]{4,})", re.IGNORECASE),
)
_YEAR_PATTERNS = (
    re.compile(r"\b(19|20)\d{2}\b"),
)
_LEAD_ORG_PATTERNS = (
    re.compile(r"(?:주관기관|수행기관|소속|기관)\s*[:=]\s*([^\n\.,;]{2,80})", re.IGNORECASE),
)
_COUNT_PATTERNS = (
    re.compile(r"(?:총|모두)\s*(\d+)\s*(?:건|개|명)", re.IGNORECASE),
    re.compile(r"(\d+)\s*(?:건|개|명)\s*(?:은|는|입니다|이다)", re.IGNORECASE),
)


def _coerce_dict(source: Any) -> dict[str, Any]:
    if isinstance(source, dict):
        return source
    if hasattr(source, "model_dump"):
        try:
            payload = source.model_dump()
            if isinstance(payload, dict):
                return payload
        except Exception:
            return {}
    if hasattr(source, "__dict__"):
        try:
            return dict(vars(source))
        except Exception:
            return {}
    return {}


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _normalize_ids_map(raw_ids_map: dict[str, Any]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for key, raw_values in (raw_ids_map or {}).items():
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        seen: set[str] = set()
        items: list[str] = []
        for value in values:
            text = _first_text(value)
            if not text:
                continue
            text = text.strip()
            if text in seen:
                continue
            seen.add(text)
            items.append(text)
        if items:
            normalized[str(key).strip().lower()] = items
    return normalized


def _normalized_title(value: Any) -> str:
    text = str(value or "")
    text = _TITLE_CLEANUP.sub(" ", text)
    text = text.replace("**", " ").replace("__", " ").replace("`", " ")
    text = re.sub(r"^\s*(\d+[\.\)]|[-*•])\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^0-9A-Za-z가-힣]+", " ", text)
    return " ".join(text.split()).strip().lower()


def _titles_compatible(observed: str, expected: str) -> bool:
    observed_norm = _normalized_title(observed)
    expected_norm = _normalized_title(expected)
    if not observed_norm or not expected_norm:
        return False
    return (
        observed_norm == expected_norm
        or observed_norm in expected_norm
        or expected_norm in observed_norm
    )


def _extract_first_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> Optional[str]:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = _first_text(match.group(1))
            if value:
                return value
    return None


def _extract_ids_map(text: str) -> dict[str, list[str]]:
    return _normalize_ids_map(
        {
            "pjt_id": _extract_first_match(text, _PROJECT_ID_PATTERNS),
            "pjt_no": _extract_first_match(text, _PROJECT_NO_PATTERNS),
            "rst_id": _extract_first_match(text, _PERF_ID_PATTERNS),
            "doi": _extract_first_match(text, _DOI_PATTERNS),
            "issn": _extract_first_match(text, _ISSN_PATTERNS),
        }
    )


def _extract_year(text: str) -> Optional[int]:
    match = _YEAR_PATTERNS[0].search(text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except Exception:
        return None


def _extract_lead_org(text: str) -> Optional[str]:
    value = _extract_first_match(text, _LEAD_ORG_PATTERNS)
    return _first_text(value)


def _extract_declared_count(text: str) -> Optional[int]:
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            return int(match.group(1))
        except Exception:
            continue
    return None


def _split_list_blocks(answer_text: str) -> list[str]:
    lines = [line.rstrip() for line in str(answer_text or "").splitlines()]
    numbered_indices = [index for index, line in enumerate(lines) if _TOP_LEVEL_NUMBERED.match(line)]
    bullet_indices = [index for index, line in enumerate(lines) if _TOP_LEVEL_BULLET.match(line)]

    block_starts = numbered_indices or bullet_indices
    if not block_starts:
        return []

    blocks: list[str] = []
    for position, start in enumerate(block_starts):
        end = block_starts[position + 1] if position + 1 < len(block_starts) else len(lines)
        block_lines = lines[start:end]
        text = "\n".join(line for line in block_lines if str(line).strip())
        if text.strip():
            blocks.append(text.strip())
    return blocks


def _extract_title_from_block(block: str) -> str:
    first_line = ""
    for line in str(block or "").splitlines():
        text = str(line or "").strip()
        if text:
            first_line = text
            break
    if not first_line:
        return ""
    first_line = re.sub(r"^\s*(\d+[\.\)]|[-*•])\s+", "", first_line)
    first_line = _TITLE_CLEANUP.sub(" ", first_line)
    first_line = first_line.replace("**", " ").replace("__", " ").replace("`", " ")
    first_line = re.sub(r"\s+", " ", first_line).strip(" -:;,")
    return first_line.strip()


def _parse_answer_items(answer_text: str) -> list[_ParsedAnswerItem]:
    blocks = _split_list_blocks(answer_text)
    parsed: list[_ParsedAnswerItem] = []
    for index, block in enumerate(blocks, start=1):
        parsed.append(
            _ParsedAnswerItem(
                observed_rank=index,
                title_text=_extract_title_from_block(block),
                ids_map=_extract_ids_map(block),
                year=_extract_year(block),
                lead_org=_extract_lead_org(block),
            )
        )
    return parsed


def _ids_overlap(observed_ids: dict[str, list[str]], expected_ids: dict[str, list[str]]) -> bool:
    for key, observed_values in (observed_ids or {}).items():
        expected_values = set(expected_ids.get(key) or [])
        if expected_values and any(value in expected_values for value in observed_values):
            return True
    return False


def _item_identity_matches(
    observed: _ParsedAnswerItem,
    expected: AnswerStateSnapshotItem,
) -> bool:
    if observed.ids_map:
        return _ids_overlap(observed.ids_map, expected.ids_map)
    if observed.title_text:
        return _titles_compatible(observed.title_text, expected.title_text)
    return False


def _assign_snapshot_indices(
    items: list[_ParsedAnswerItem],
    snapshot_items: list[AnswerStateSnapshotItem],
) -> Optional[list[int]]:
    candidate_indices: list[list[int]] = []
    for item in items:
        matches = [index for index, expected in enumerate(snapshot_items) if _item_identity_matches(item, expected)]
        if not matches:
            return None
        candidate_indices.append(matches)

    order = sorted(range(len(candidate_indices)), key=lambda idx: len(candidate_indices[idx]))
    assignments: list[Optional[int]] = [None] * len(candidate_indices)
    used: set[int] = set()

    def _backtrack(position: int) -> bool:
        if position >= len(order):
            return True
        item_index = order[position]
        for snapshot_index in candidate_indices[item_index]:
            if snapshot_index in used:
                continue
            used.add(snapshot_index)
            assignments[item_index] = snapshot_index
            if _backtrack(position + 1):
                return True
            assignments[item_index] = None
            used.remove(snapshot_index)
        return False

    if not _backtrack(0):
        return None
    return [int(index) for index in assignments if index is not None]


def build_state_snapshot_from_result_set(result_set: Any) -> AnswerStateSnapshot:
    payload = _coerce_dict(result_set)
    items_payload = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items_payload, list):
        items_payload = getattr(result_set, "items", None)
    if not isinstance(items_payload, list):
        return AnswerStateSnapshot(available=False, snapshot_source="none")

    ordered_items: list[AnswerStateSnapshotItem] = []
    for index, raw_item in enumerate(items_payload, start=1):
        item = _coerce_dict(raw_item)
        ids_map = _normalize_ids_map(
            {
                "pjt_id": _first_text(item.get("pjt_id")),
                "pjt_no": _first_text(item.get("pjt_no")),
                "rst_id": _first_text(item.get("rst_id")),
                "person_no": _first_text(item.get("person_no")),
                "org_id": _first_text(item.get("org_id")),
                "org_code": _first_text(item.get("org_code")),
                "biz_no": _first_text(item.get("biz_no")),
                "doi": _first_text(item.get("doi")),
                "issn": _first_text(item.get("issn")),
            }
        )
        year = item.get("year")
        try:
            normalized_year = int(year) if year not in (None, "") else None
        except Exception:
            normalized_year = None
        ordered_items.append(
            AnswerStateSnapshotItem(
                display_rank=int(item.get("display_rank") or index),
                entity_kind=str(item.get("entity_kind") or "project").strip().lower() or "project",
                title_text=_first_text(item.get("title_text")) or "",
                ids_map=ids_map,
                year=normalized_year,
                lead_org=_first_text(item.get("lead_org")),
            )
        )

    visible_count = _first_text(payload.get("visible_count"), getattr(result_set, "visible_count", None))
    try:
        normalized_visible_count = int(visible_count) if visible_count is not None else len(ordered_items)
    except Exception:
        normalized_visible_count = len(ordered_items)
    context_kind = _first_text(payload.get("context_kind"), getattr(result_set, "context_kind", None)) or "project"
    return AnswerStateSnapshot(
        available=bool(ordered_items),
        snapshot_source="active_result_set" if ordered_items else "none",
        context_kind=context_kind,
        visible_count=max(0, normalized_visible_count),
        ordered_items=ordered_items,
    )


def evaluate_answer_state_consistency(
    *,
    answer_text: str,
    answer_kind: str,
    state_snapshot: AnswerStateSnapshot | dict[str, Any] | None,
) -> AnswerStateConsistencyVerdict:
    if answer_kind in BYPASS_ANSWER_KINDS:
        return AnswerStateConsistencyVerdict(status="not_applicable")

    snapshot = (
        state_snapshot
        if isinstance(state_snapshot, AnswerStateSnapshot)
        else AnswerStateSnapshot.model_validate(state_snapshot or {})
    )
    if not snapshot.available or not snapshot.ordered_items:
        return AnswerStateConsistencyVerdict(
            status="insufficient_snapshot",
            reason_codes=["insufficient_snapshot"],
        )

    parsed_items = _parse_answer_items(answer_text)
    if not parsed_items:
        return AnswerStateConsistencyVerdict(
            status="no_structured_list",
            reason_codes=["no_structured_list"],
        )

    declared_count = _extract_declared_count(answer_text)
    visible_count = int(snapshot.visible_count or 0)
    if declared_count is not None and declared_count != visible_count:
        return AnswerStateConsistencyVerdict(
            status="unsupported_count",
            reason_codes=["unsupported_count"],
            checked_items=len(parsed_items),
            mismatches=[
                AnswerStateMismatch(
                    reason="count_mismatch",
                    expected_rank=visible_count,
                    observed_rank=declared_count,
                )
            ],
        )
    if len(parsed_items) != visible_count:
        return AnswerStateConsistencyVerdict(
            status="unsupported_count",
            reason_codes=["unsupported_count"],
            checked_items=len(parsed_items),
            mismatches=[
                AnswerStateMismatch(
                    reason="item_count_mismatch",
                    expected_rank=visible_count,
                    observed_rank=len(parsed_items),
                )
            ],
        )

    same_rank_supported = True
    mismatches: list[AnswerStateMismatch] = []
    for index, parsed_item in enumerate(parsed_items):
        expected_item = snapshot.ordered_items[index]
        if not _item_identity_matches(parsed_item, expected_item):
            same_rank_supported = False
            mismatches.append(
                AnswerStateMismatch(
                    rank=index + 1,
                    expected_rank=expected_item.display_rank,
                    observed_rank=parsed_item.observed_rank,
                    expected_title=expected_item.title_text,
                    observed_title=parsed_item.title_text,
                    expected_ids=expected_item.ids_map,
                    observed_ids=parsed_item.ids_map,
                    reason="same_rank_identity_mismatch",
                )
            )

    if same_rank_supported:
        return AnswerStateConsistencyVerdict(
            status="supported",
            checked_items=len(parsed_items),
        )

    assignments = _assign_snapshot_indices(parsed_items, snapshot.ordered_items)
    if assignments is None:
        return AnswerStateConsistencyVerdict(
            status="unsupported_item_identity",
            reason_codes=["unsupported_item_identity"],
            checked_items=len(parsed_items),
            mismatches=mismatches,
        )

    if any(assigned_index != index for index, assigned_index in enumerate(assignments)):
        order_mismatches = [
            AnswerStateMismatch(
                rank=index + 1,
                expected_rank=snapshot.ordered_items[index].display_rank,
                observed_rank=parsed_items[index].observed_rank,
                expected_title=snapshot.ordered_items[index].title_text,
                observed_title=parsed_items[index].title_text,
                expected_ids=snapshot.ordered_items[index].ids_map,
                observed_ids=parsed_items[index].ids_map,
                reason="rank_order_mismatch",
            )
            for index, assigned_index in enumerate(assignments)
            if assigned_index != index
        ]
        return AnswerStateConsistencyVerdict(
            status="unsupported_order",
            reason_codes=["unsupported_order"],
            checked_items=len(parsed_items),
            mismatches=order_mismatches,
        )

    return AnswerStateConsistencyVerdict(
        status="unsupported_item_identity",
        reason_codes=["unsupported_item_identity"],
        checked_items=len(parsed_items),
        mismatches=mismatches,
    )
