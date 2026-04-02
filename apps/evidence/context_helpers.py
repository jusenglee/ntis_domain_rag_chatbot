"""Pure context/title helpers used by retrieval and answer assembly."""

from __future__ import annotations

from typing import Any


def resolve_title_from_payload(payload: dict[str, Any]) -> str:
    """payload 안에서 우선순위가 높은 제목 필드를 찾아 문자열로 돌려준다."""
    for key in ("title1", "title_text", "title2"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text or text == "None":
            continue
        return text
    return ""


def apply_title_preference(mapped_doc: dict[str, Any]) -> None:
    """payload 기반 제목이 있으면 매핑된 문서의 top-level title을 그 값으로 덮어쓴다."""
    preferred_title = resolve_title_from_payload(mapped_doc)
    if preferred_title:
        mapped_doc["title"] = preferred_title


def collect_priority_field_lines(mapped_doc: dict[str, Any], priority_context_fields: tuple[str, ...]) -> list[str]:
    """우선 노출할 필드들을 - field: value 형식의 context line으로 모은다.

    빈 값과 중복 문장은 제거해 context renderer가 같은 사실을 반복하지 않게 한다.
    """
    lines: list[str] = []
    seen: set[str] = set()

    for field in priority_context_fields:
        value = mapped_doc.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() == "none":
            continue

        normalized = f"{field}:{text}".lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        lines.append(f"- {field}: {text}")

    return lines
