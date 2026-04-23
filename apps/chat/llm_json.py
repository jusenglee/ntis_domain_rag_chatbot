"""LLM JSON extraction helpers for planner and answer-generation flows."""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from apps.api.runtime_helpers import logger


class LLMJSONExtractionError(ValueError):
    """Raised when no valid JSON candidate can be recovered from LLM output."""


def summarize_text(text: str, head: int = 160, tail: int = 160) -> str:
    """Compress long text into a head/tail preview for logs."""

    compact = " ".join(text.split())
    if len(compact) <= head + tail + 20:
        return compact
    return f"{compact[:head]} ... {compact[-tail:]}"


def iter_json_candidates(text: str) -> List[str]:
    """Collect likely JSON substrings from an LLM response in encounter order."""

    candidates: List[tuple[int, str]] = []

    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE):
        block = match.group(1).strip()
        if block:
            candidates.append((match.start(), block))

    def find_matching_end(start_idx: int, open_ch: str, close_ch: str) -> Optional[int]:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start_idx, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                    continue
                if ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return idx
        return None

    for match in re.finditer(r"[\{\[]", text):
        start_idx = match.start()
        open_ch = text[start_idx]
        close_ch = "}" if open_ch == "{" else "]"
        end_idx = find_matching_end(start_idx, open_ch, close_ch)
        if end_idx is None:
            continue
        candidates.append((start_idx, text[start_idx : end_idx + 1].strip()))

    seen: set[str] = set()
    ordered: List[str] = []
    for _, candidate in sorted(candidates, key=lambda item: item[0]):
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


def sanitize_llm_json(
    msg: Any,
    *,
    source_stage: Optional[str] = None,
    request_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """Return the first parseable JSON candidate from an LLM message."""

    text = msg.content if hasattr(msg, "content") else str(msg)
    last_error: Optional[Exception] = None
    candidates = iter_json_candidates(text)

    for candidate in candidates:
        try:
            json.loads(candidate)
            return candidate
        except Exception as exc:
            last_error = exc
            continue

    preview = summarize_text(text)
    logger.warning(
        "[llm_json] no valid JSON candidate found: source_stage={} request_id={} conversation_id={} text_chars={} empty_text={} candidate_count={} preview={}",
        source_stage,
        request_id,
        conversation_id,
        len(text),
        int(not str(text or "").strip()),
        len(candidates),
        preview,
    )
    raise LLMJSONExtractionError(f"No valid JSON candidate found. preview={preview!r} last_error={last_error!r}")
