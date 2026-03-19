"""LLM JSON extraction helpers for planner and answer-generation flows."""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional


class LLMJSONExtractionError(ValueError):
    """LLM 응답에서 유효한 JSON 후보를 끝내 찾지 못했을 때 쓰는 오류다."""


def summarize_text(text: str, head: int = 160, tail: int = 160) -> str:
    """긴 응답 문자열을 로그용 앞뒤 요약으로 압축한다."""
    compact = " ".join(text.split())
    if len(compact) <= head + tail + 20:
        return compact
    return f"{compact[:head]} ... {compact[-tail:]}"


def iter_json_candidates(text: str) -> List[str]:
    """LLM 출력에서 JSON으로 보이는 후보 문자열을 순서대로 추출한다.

    코드펜스 안의 블록과 중괄호/대괄호로 둘러싸인 부분을 모두 수집해, 파서가 여러 후보를 차례로 시도하게 만든다.
    """
    candidates: List[tuple[int, str]] = []

    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE):
        block = match.group(1).strip()
        if block:
            candidates.append((match.start(), block))

    def find_matching_end(start_idx: int, open_ch: str, close_ch: str) -> Optional[int]:
        """문자열 리터럴과 escape를 고려해 JSON 블록의 닫는 괄호 위치를 찾는다."""
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
        candidates.append((start_idx, text[start_idx:end_idx + 1].strip()))

    seen: set[str] = set()
    ordered: List[str] = []
    for _, candidate in sorted(candidates, key=lambda item: item[0]):
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


def sanitize_llm_json(msg: Any, *, logger: Any) -> str:
    """LLM 메시지에서 실제로 파싱 가능한 첫 JSON 후보를 골라 반환한다.

    모든 후보가 실패하면 축약 미리보기를 로그에 남기고 ExtractionError를 던져 상위 단계가 재시도나 실패 처리를 선택하게 한다.
    """
    text = msg.content if hasattr(msg, "content") else str(msg)
    last_error: Optional[Exception] = None

    for candidate in iter_json_candidates(text):
        try:
            json.loads(candidate)
            return candidate
        except Exception as exc:
            last_error = exc
            continue

    preview = summarize_text(text)
    logger.warning("[llm_json] no valid JSON candidate found: %s", preview)
    raise LLMJSONExtractionError(f"No valid JSON candidate found. preview={preview!r} last_error={last_error!r}")
