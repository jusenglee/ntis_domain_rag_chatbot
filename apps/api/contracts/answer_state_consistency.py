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
    effective_context_kind: str = "project"
    visible_count: int = 0
    ordered_items: list[AnswerStateSnapshotItem] = Field(default_factory=list)


class AnswerStateConsistencyPolicy(BaseModel):
    policy_name: str = "exact_count"
    enforce_exact_count: bool = True
    allow_prefix_subset: bool = False
    min_required_items: int = 1
    allow_manifest_publish_on_subset: bool = False


class AnswerStateConsistencyVerdict(BaseModel):
    status: AnswerStateConsistencyStatus
    reason_codes: list[str] = Field(default_factory=list)
    checked_items: int = 0
    mismatches: list[AnswerStateMismatch] = Field(default_factory=list)
    snapshot_visible_count: int = 0
    parsed_item_count: int = 0
    declared_count: Optional[int] = None
    mismatch_reason: Optional[str] = None
    parsed_titles_preview: list[str] = Field(default_factory=list)
    snapshot_titles_preview: list[str] = Field(default_factory=list)
    policy_name: str = "exact_count"
    subset_accepted: bool = False
    manifest_publish_allowed: bool = False
    accepted_item_count: int = 0
    required_visible_count: int = 0


class _ParsedAnswerItem(BaseModel):
    observed_rank: int
    title_text: str = ""
    ids_map: dict[str, list[str]] = Field(default_factory=dict)
    year: Optional[int] = None
    lead_org: Optional[str] = None
    referenced_ranks: list[int] = Field(default_factory=list)


_TOP_LEVEL_NUMBERED = re.compile(r"^\s*(\d+)[\.\)]\s+(.*\S)?\s*$")
_TOP_LEVEL_BULLET = re.compile(r"^\s*[-*•]\s+(.*\S)?\s*$")
_TITLE_CLEANUP = re.compile(r"\[(?:출처|source)\s*\d+\]", re.IGNORECASE)
# rank citation: "[1]", "[2, 3, 4]", "[ 2 , 3 ]" — 답변 블록 내부에서 snapshot rank를 인용하는 형태.
# 단독 숫자(또는 콤마로 구분된 숫자들)만 있는 대괄호만 인정. 출처 프리픽스 등은 _TITLE_CLEANUP이 처리함.
_RANK_CITATION_PATTERN = re.compile(r"\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]")
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


def _extract_rank_citations(block: str, *, max_rank: int) -> list[int]:
    """답변 블록 내부의 `[N]` 또는 `[N, M, ...]` 패턴을 snapshot rank 인용으로 해석해 추출한다.

    max_rank는 snapshot.visible_count (or ordered_items 길이)로, 범위를 벗어나는 값은 버려
    연도·잡음 숫자가 rank로 오해되는 걸 막는다. 중복은 제거하되 등장 순서는 유지한다.
    """
    if max_rank <= 0:
        return []
    collected: list[int] = []
    seen: set[int] = set()
    for match in _RANK_CITATION_PATTERN.finditer(str(block or "")):
        raw = match.group(1) or ""
        for token in raw.split(","):
            candidate = token.strip()
            if not candidate.isdigit():
                continue
            try:
                value = int(candidate)
            except Exception:
                continue
            if value < 1 or value > max_rank or value in seen:
                continue
            seen.add(value)
            collected.append(value)
    return collected


def _parse_answer_items(answer_text: str, *, max_rank: int = 0) -> list[_ParsedAnswerItem]:
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
                referenced_ranks=_extract_rank_citations(block, max_rank=max_rank),
            )
        )
    return parsed


def _expand_parsed_items_by_rank_citations(
    parsed_items: list[_ParsedAnswerItem],
    *,
    snapshot_size: int,
) -> tuple[list[_ParsedAnswerItem], bool]:
    """rank citation이 여러 개인 parsed item을 rank별 virtual item으로 전개한다.

    LLM이 같은 과제의 연차 row들을 하나의 항목으로 묶으면서 `[2, 3, 4]` 식으로 rank를 인용하면,
    guard는 여전히 snapshot row-level granularity(M vs N)로 비교하므로 count·identity가 깨진다.
    여기서는 인용된 rank가 모두 유효(1..snapshot_size)할 때만 해당 item을 가상 확장해
    snapshot과 같은 세밀도로 비교할 수 있게 만든다.

    전개가 일어나지 않으면 원본을 그대로 돌려준다. 두 번째 반환값은 "전개가 실제로 적용됐는가" 플래그.
    """
    if snapshot_size <= 0:
        return list(parsed_items), False

    expanded: list[_ParsedAnswerItem] = []
    mutated = False
    virtual_rank = 0
    for item in parsed_items:
        ranks = list(item.referenced_ranks or [])
        if len(ranks) <= 1:
            virtual_rank += 1
            expanded.append(item.model_copy(update={"observed_rank": virtual_rank}))
            continue
        mutated = True
        for rank in ranks:
            virtual_rank += 1
            expanded.append(
                item.model_copy(
                    update={
                        "observed_rank": virtual_rank,
                        "referenced_ranks": [int(rank)],
                    }
                )
            )
    return expanded, mutated


def _titles_preview(values: list[str], *, limit: int = 3) -> list[str]:
    preview: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        preview.append(text)
        if len(preview) >= limit:
            break
    return preview


def _build_consistency_verdict(
    *,
    status: AnswerStateConsistencyStatus,
    reason_codes: list[str] | None = None,
    checked_items: Optional[int] = None,
    mismatches: list[AnswerStateMismatch] | None = None,
    snapshot: AnswerStateSnapshot | None = None,
    parsed_items: list[_ParsedAnswerItem] | None = None,
    declared_count: Optional[int] = None,
    mismatch_reason: Optional[str] = None,
    policy: AnswerStateConsistencyPolicy | None = None,
    subset_accepted: bool = False,
    manifest_publish_allowed: Optional[bool] = None,
) -> AnswerStateConsistencyVerdict:
    parsed = list(parsed_items or [])
    normalized_snapshot = snapshot or AnswerStateSnapshot(available=False, snapshot_source="none")
    normalized_policy = policy or AnswerStateConsistencyPolicy()
    accepted = status == "supported"
    publish_allowed = (
        bool(manifest_publish_allowed)
        if manifest_publish_allowed is not None
        else bool(accepted and (not subset_accepted or normalized_policy.allow_manifest_publish_on_subset))
    )
    return AnswerStateConsistencyVerdict(
        status=status,
        reason_codes=list(reason_codes or []),
        checked_items=(len(parsed) if checked_items is None else int(checked_items)),
        mismatches=list(mismatches or []),
        snapshot_visible_count=int(normalized_snapshot.visible_count or 0),
        parsed_item_count=len(parsed),
        declared_count=declared_count,
        mismatch_reason=mismatch_reason,
        parsed_titles_preview=_titles_preview([item.title_text for item in parsed]),
        snapshot_titles_preview=_titles_preview(
            [item.title_text for item in list(normalized_snapshot.ordered_items or [])]
        ),
        policy_name=str(normalized_policy.policy_name or "exact_count").strip().lower() or "exact_count",
        subset_accepted=bool(accepted and subset_accepted),
        manifest_publish_allowed=bool(publish_allowed),
        accepted_item_count=(len(parsed) if accepted else 0),
        required_visible_count=int(normalized_snapshot.visible_count or 0),
    )


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
    referenced_ranks = list(getattr(observed, "referenced_ranks", None) or [])
    if referenced_ranks:
        if expected.display_rank in referenced_ranks:
            return True
        # rank citation이 있는데 해당 snapshot을 가리키지 않으면 identity 불일치로 간주.
        # 단, ids_map/title이 확실히 일치하는 경우 rank 누락은 용인 (citation이 안전망 역할).
    if observed.ids_map:
        if _ids_overlap(observed.ids_map, expected.ids_map):
            return True
    if observed.title_text and _titles_compatible(observed.title_text, expected.title_text):
        return True
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


def _normalize_state_consistency_policy(
    source: AnswerStateConsistencyPolicy | dict[str, Any] | None,
) -> AnswerStateConsistencyPolicy:
    if isinstance(source, AnswerStateConsistencyPolicy):
        return source
    return AnswerStateConsistencyPolicy.model_validate(source or {})


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
    effective_context_kind = _derive_effective_context_kind(
        declared_context_kind=context_kind,
        ordered_items=ordered_items,
    )
    return AnswerStateSnapshot(
        available=bool(ordered_items),
        snapshot_source="active_result_set" if ordered_items else "none",
        context_kind=context_kind,
        effective_context_kind=effective_context_kind,
        visible_count=max(0, normalized_visible_count),
        ordered_items=ordered_items,
    )


def _derive_effective_context_kind(
    *,
    declared_context_kind: str,
    ordered_items: list[AnswerStateSnapshotItem],
) -> str:
    """item\uc758 entity_kind \ub2e4\uc218\uacb0\ub85c snapshot-level context_kind\ub97c \ubcf4\uc815\ud55c\ub2e4.

    render_profile\uc774 "lookup+project + \uc0ac\ub78c/\uae30\uad00 \uc870\uac74"\uc5d0\uc11c context_kind\ub97c
    people/org\ub85c \ub36e\uc5b4\uc4f0\ub294\ub370, retrieval\uc774 \ubc18\ud658\ud55c actual rows\ub294 project\uc77c \uc218 \uc788\ub2e4.
    \uac00\ub4dc \ub85c\uc9c1\uc740 item\uc758 \uc2e4\uc81c entity_kind \uacc4\ubcc4\uc744 \ub530\ub77c\uc57c \uc815\ud655\ud788 \ud310\ub2e8\ud560 \uc218 \uc788\uc73c\ubbc0\ub85c,
    50%\ub97c \ub118\uac8c \ub36e\ub294 \ub2e8\uc77c entity_kind\uac00 \uc788\uc73c\uba74 \uadf8\uac83\uc744 effective\uc73c\ub85c \uc32c\ub2e4.
    \uadf8\ub807\uc9c0 \uc54a\uc73c\uba74 declared \uac12\uc744 \uadf8\ub300\ub85c \uc0ac\uc6a9\ud55c\ub2e4.
    """
    declared = str(declared_context_kind or "").strip().lower() or "project"
    if not ordered_items:
        return declared
    counts: dict[str, int] = {}
    for item in ordered_items:
        kind = str(getattr(item, "entity_kind", "") or "").strip().lower() or "project"
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return declared
    dominant_kind, dominant_count = max(counts.items(), key=lambda pair: pair[1])
    total = sum(counts.values())
    if total > 0 and dominant_count * 2 > total:
        return dominant_kind
    return declared


def evaluate_answer_state_consistency(
    *,
    answer_text: str,
    answer_kind: str,
    state_snapshot: AnswerStateSnapshot | dict[str, Any] | None,
    state_policy: AnswerStateConsistencyPolicy | dict[str, Any] | None = None,
) -> AnswerStateConsistencyVerdict:
    policy = _normalize_state_consistency_policy(state_policy)
    if policy.policy_name == "bypass" or answer_kind in BYPASS_ANSWER_KINDS:
        return _build_consistency_verdict(status="not_applicable", policy=policy)

    snapshot = (
        state_snapshot
        if isinstance(state_snapshot, AnswerStateSnapshot)
        else AnswerStateSnapshot.model_validate(state_snapshot or {})
    )
    if not snapshot.available or not snapshot.ordered_items:
        return _build_consistency_verdict(
            status="insufficient_snapshot",
            reason_codes=["insufficient_snapshot"],
            snapshot=snapshot,
            mismatch_reason="insufficient_snapshot",
            policy=policy,
        )

    parsed_items_raw = _parse_answer_items(
        answer_text,
        max_rank=len(snapshot.ordered_items),
    )
    if not parsed_items_raw:
        return _build_consistency_verdict(
            status="no_structured_list",
            reason_codes=["no_structured_list"],
            snapshot=snapshot,
            declared_count=_extract_declared_count(answer_text),
            mismatch_reason="no_structured_list",
            policy=policy,
        )

    parsed_items, rank_citations_expanded = _expand_parsed_items_by_rank_citations(
        parsed_items_raw,
        snapshot_size=len(snapshot.ordered_items),
    )

    declared_count = _extract_declared_count(answer_text)
    visible_count = int(snapshot.visible_count or 0)
    # LLM이 pjt_no로 그룹핑하면 user-visible count(raw)와 snapshot row count(visible_count)가 다를 수 있다.
    # rank_citations_expanded가 True면 두 값 모두 정당한 declared_count로 수용.
    acceptable_declared_counts = {visible_count}
    if rank_citations_expanded:
        acceptable_declared_counts.add(len(parsed_items_raw))
    if declared_count is not None and declared_count not in acceptable_declared_counts:
        return _build_consistency_verdict(
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
            snapshot=snapshot,
            parsed_items=parsed_items,
            declared_count=declared_count,
            mismatch_reason="declared_count_mismatch",
            policy=policy,
        )
    if len(parsed_items) > visible_count:
        return _build_consistency_verdict(
            status="unsupported_count",
            reason_codes=["unsupported_count"],
            checked_items=len(parsed_items),
            mismatches=[
                AnswerStateMismatch(
                    reason="item_count_exceeds_visible_count",
                    expected_rank=visible_count,
                    observed_rank=len(parsed_items),
                )
            ],
            snapshot=snapshot,
            parsed_items=parsed_items,
            declared_count=declared_count,
            mismatch_reason="item_count_exceeds_visible_count",
            policy=policy,
        )
    min_required_items = max(1, int(policy.min_required_items or 1))
    if len(parsed_items) < min_required_items:
        return _build_consistency_verdict(
            status="unsupported_count",
            reason_codes=["unsupported_count"],
            checked_items=len(parsed_items),
            mismatches=[
                AnswerStateMismatch(
                    reason="below_min_required_items",
                    expected_rank=min_required_items,
                    observed_rank=len(parsed_items),
                )
            ],
            snapshot=snapshot,
            parsed_items=parsed_items,
            declared_count=declared_count,
            mismatch_reason="below_min_required_items",
            policy=policy,
        )
    if bool(policy.enforce_exact_count) and len(parsed_items) != visible_count:
        return _build_consistency_verdict(
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
            snapshot=snapshot,
            parsed_items=parsed_items,
            declared_count=declared_count,
            mismatch_reason="item_count_mismatch",
            policy=policy,
        )

    same_rank_supported = True
    mismatches: list[AnswerStateMismatch] = []
    comparison_items = list(snapshot.ordered_items[: len(parsed_items)])
    for index, parsed_item in enumerate(parsed_items):
        expected_item = comparison_items[index]
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
        subset_accepted = len(parsed_items) != visible_count
        return _build_consistency_verdict(
            status="supported",
            checked_items=len(parsed_items),
            snapshot=snapshot,
            parsed_items=parsed_items,
            declared_count=declared_count,
            mismatch_reason=("prefix_subset_accepted" if subset_accepted else None),
            policy=policy,
            subset_accepted=subset_accepted,
        )

    assignments = _assign_snapshot_indices(parsed_items, snapshot.ordered_items)
    if assignments is None:
        return _build_consistency_verdict(
            status="unsupported_item_identity",
            reason_codes=["unsupported_item_identity"],
            checked_items=len(parsed_items),
            mismatches=mismatches,
            snapshot=snapshot,
            parsed_items=parsed_items,
            declared_count=declared_count,
            mismatch_reason="unsupported_item_identity",
            policy=policy,
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
        return _build_consistency_verdict(
            status="unsupported_order",
            reason_codes=["unsupported_order"],
            checked_items=len(parsed_items),
            mismatches=order_mismatches,
            snapshot=snapshot,
            parsed_items=parsed_items,
            declared_count=declared_count,
            mismatch_reason="unsupported_order",
            policy=policy,
        )

    return _build_consistency_verdict(
        status="unsupported_item_identity",
        reason_codes=["unsupported_item_identity"],
        checked_items=len(parsed_items),
        mismatches=mismatches,
        snapshot=snapshot,
        parsed_items=parsed_items,
        declared_count=declared_count,
        mismatch_reason="unsupported_item_identity",
        policy=policy,
    )
