from __future__ import annotations

import re
from typing import Any, Dict, Optional


_ORDINAL_WORD_TO_INDEX = {
    "첫": 0,
    "첫번째": 0,
    "첫 번째": 0,
    "첫째": 0,
    "두": 1,
    "두번째": 1,
    "두 번째": 1,
    "둘째": 1,
    "세": 2,
    "세번째": 2,
    "세 번째": 2,
    "셋째": 2,
}

_EXPLICIT_ORDINAL_PATTERNS = (
    re.compile(r"\b(?:제\s*)?(\d{1,3})\s*번째\b"),
    re.compile(r"\b(?:제\s*)?(\d{1,3})\s*번\b"),
    re.compile(r"\b(첫번째|첫\s*번째|첫째|첫)\b"),
    re.compile(r"\b(두번째|두\s*번째|둘째|두)\b"),
    re.compile(r"\b(세번째|세\s*번째|셋째|세)\b"),
)
_LAST_ITEM_RE = re.compile(r"(?:맨\s*마지막|마지막)")
_DEICTIC_PROJECT_PATTERNS = (
    re.compile("\uadf8 \uacfc\uc81c"),
    re.compile("\uc774 \uacfc\uc81c"),
    re.compile("\ubc29\uae08 \uacfc\uc81c"),
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def is_ordinal_reference_token(value: Any) -> bool:
    text = _normalize_text(value)
    if not text:
        return False
    if _LAST_ITEM_RE.search(text):
        return True
    for pattern in _DEICTIC_PROJECT_PATTERNS:
        if pattern.search(text):
            return True
    for pattern in _EXPLICIT_ORDINAL_PATTERNS:
        if pattern.search(text):
            return True
    return False


def strip_ordinal_reference_terms(values: Any) -> tuple[list[str], list[str]]:
    if values is None:
        return [], []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = [values]

    kept: list[str] = []
    stripped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        if is_ordinal_reference_token(text):
            stripped.append(text)
        else:
            kept.append(text)
    return kept, stripped


def _infer_context_kind(item: Dict[str, Any], default_context_kind: str) -> str:
    ids = item.get("ids") or {}
    if _normalize_text(ids.get("pjt_id")) or _normalize_text(ids.get("pjt_no")):
        return "project"
    if _normalize_text(ids.get("rst_id")):
        return "perf"
    return default_context_kind


def build_reference_items(
    *,
    canonical_evidence: list[dict[str, Any]],
    prev_context: list[dict[str, Any]],
    default_context_kind: str = "project",
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if canonical_evidence:
        for index, item in enumerate(canonical_evidence, start=1):
            if not isinstance(item, dict):
                continue
            ids = item.get("ids") or {}
            facts = item.get("facts") or {}
            items.append(
                {
                    "index": index,
                    "pjt_id": _normalize_text(ids.get("pjt_id")) or None,
                    "pjt_no": _normalize_text(ids.get("pjt_no")) or None,
                    "rst_id": _normalize_text(ids.get("rst_id")) or None,
                    "title": _normalize_text(facts.get("title") or item.get("identity")) or None,
                    "context_kind": _infer_context_kind(item, default_context_kind),
                    "source": "canonical_evidence",
                }
            )
        return items

    for index, item in enumerate(prev_context or [], start=1):
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "index": index,
                "pjt_id": _normalize_text(item.get("pjt_id")) or None,
                "pjt_no": _normalize_text(item.get("pjt_no")) or None,
                "rst_id": _normalize_text(item.get("rst_id")) or None,
                "title": _normalize_text(item.get("title_text")) or None,
                "context_kind": "perf" if _normalize_text(item.get("rst_id")) and not (_normalize_text(item.get("pjt_id")) or _normalize_text(item.get("pjt_no"))) else default_context_kind,
                "source": "prev_context",
            }
        )
    return items


def _parse_explicit_ordinal(question: str) -> Optional[dict[str, Any]]:
    text = _normalize_text(question)
    if not text:
        return None
    last_match = _LAST_ITEM_RE.search(text)
    if last_match:
        return {"kind": "last", "index": -1, "token": last_match.group(0)}
    for pattern in _EXPLICIT_ORDINAL_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        token = _normalize_text(match.group(0))
        raw = _normalize_text(match.group(1))
        if raw.isdigit():
            ordinal = int(raw)
            if ordinal <= 0:
                return None
            return {"kind": "index", "index": ordinal - 1, "token": token}
        normalized_raw = raw.replace(" ", "")
        mapped = _ORDINAL_WORD_TO_INDEX.get(normalized_raw)
        if mapped is not None:
            return {"kind": "index", "index": mapped, "token": token}
    return None


def _parse_deictic_followup(question: str, *, default_context_kind: str) -> Optional[dict[str, Any]]:
    text = _normalize_text(question)
    if not text:
        return None
    if str(default_context_kind or "").strip().lower() != "project" and "과제" not in text:
        return None
    for pattern in _DEICTIC_PROJECT_PATTERNS:
        match = pattern.search(text)
        if match:
            return {"kind": "deictic", "index": None, "token": _normalize_text(match.group(0))}
    return None


def resolve_reference_context_followup(
    *,
    question: str,
    canonical_evidence: list[dict[str, Any]],
    prev_context: list[dict[str, Any]],
    default_context_kind: str = "project",
) -> dict[str, Any]:
    parsed = _parse_explicit_ordinal(question)
    if parsed is None:
        parsed = _parse_deictic_followup(question, default_context_kind=default_context_kind)
    if parsed is None:
        return {
            "followup_resolution_status": "none",
            "explicit_ordinal": False,
            "explicit_followup": False,
            "requested_token": None,
            "requested_index": None,
            "available_count": len(build_reference_items(canonical_evidence=canonical_evidence, prev_context=prev_context, default_context_kind=default_context_kind)),
            "selected_prev_item": None,
            "seed_map": {},
            "seed_source": None,
            "followup_reference_kind": None,
        }

    items = build_reference_items(
        canonical_evidence=canonical_evidence,
        prev_context=prev_context,
        default_context_kind=default_context_kind,
    )
    if not items:
        return {
            "followup_resolution_status": "missing_context",
            "explicit_ordinal": parsed["kind"] != "deictic",
            "explicit_followup": True,
            "requested_token": parsed["token"],
            "requested_index": parsed["index"],
            "available_count": 0,
            "selected_prev_item": None,
            "seed_map": {},
            "seed_source": None,
            "followup_reference_kind": parsed["kind"],
        }

    if parsed["kind"] == "deictic":
        if len(items) != 1:
            return {
                "followup_resolution_status": "unresolved",
                "explicit_ordinal": False,
                "explicit_followup": True,
                "requested_token": parsed["token"],
                "requested_index": None,
                "available_count": len(items),
                "selected_prev_item": None,
                "seed_map": {},
                "seed_source": None,
                "followup_reference_kind": parsed["kind"],
            }
        index = 0
    else:
        index = len(items) - 1 if parsed["kind"] == "last" else int(parsed["index"])
    if index < 0 or index >= len(items):
        return {
            "followup_resolution_status": "out_of_range",
            "explicit_ordinal": True,
            "explicit_followup": True,
            "requested_token": parsed["token"],
            "requested_index": index,
            "available_count": len(items),
            "selected_prev_item": None,
            "seed_map": {},
            "seed_source": None,
            "followup_reference_kind": parsed["kind"],
        }

    selected_item = dict(items[index])
    seed_map: dict[str, list[str]] = {}
    if selected_item.get("pjt_id"):
        seed_map["pjt_id"] = [selected_item["pjt_id"]]
    elif selected_item.get("pjt_no"):
        seed_map["pjt_no"] = [selected_item["pjt_no"]]

    status = "resolved" if seed_map else "unresolved"
    seed_source = None
    if status == "resolved":
        seed_source = "reference_context_deictic" if parsed["kind"] == "deictic" else "reference_context_ordinal"
    return {
        "followup_resolution_status": status,
        "explicit_ordinal": parsed["kind"] != "deictic",
        "explicit_followup": True,
        "requested_token": parsed["token"],
        "requested_index": index,
        "available_count": len(items),
        "selected_prev_item": selected_item if seed_map else None,
        "seed_map": seed_map,
        "seed_source": seed_source,
        "followup_reference_kind": parsed["kind"],
    }


def build_followup_clarification_message(strategy_meta: Dict[str, Any]) -> Optional[str]:
    status = _normalize_text((strategy_meta or {}).get("followup_resolution_status")).lower()
    if not status or status in {"none", "resolved"}:
        return None

    selected_prev_item = (strategy_meta or {}).get("selected_prev_item") or {}
    context_kind = _normalize_text(selected_prev_item.get("context_kind") or (strategy_meta or {}).get("selected_prev_context_kind") or "project").lower()
    subject = "과제" if context_kind == "project" else "항목"
    available_count = int((strategy_meta or {}).get("available_count") or 0)

    if status == "missing_context":
        return f"이전 목록이 없어 몇 번째 {subject}인지 판단하기 어렵습니다. 먼저 목록을 확인한 뒤 다시 질문해 주세요."
    if status == "out_of_range":
        return f"이전 목록에는 {available_count}개만 있습니다. 몇 번째 {subject}를 말씀하시는지 다시 알려주세요."
    if status == "unresolved":
        return f"이전 목록에서 어느 {subject}를 말씀하시는지 확인해 주세요."
    return None
