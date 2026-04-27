from __future__ import annotations

import re
from re import Pattern
from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_tuple(values: Any, *, lowercase: bool = False) -> tuple[str, ...]:
    if values is None:
        return ()
    raw_values = values if isinstance(values, (list, tuple, set)) else (values,)
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        text = _normalize_text(value)
        if lowercase:
            text = text.lower()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


class EntityRegistryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    keywords: tuple[str, ...] = Field(default_factory=tuple)
    deictic_patterns: tuple[str, ...] = Field(default_factory=tuple)
    id_keys: tuple[str, ...] = Field(default_factory=tuple)
    display_label: str = ""
    allowed_child_kinds: tuple[str, ...] = Field(default_factory=tuple)
    priority: int = 100
    temporal_keys: tuple[str, ...] = ("year",)

    @field_validator("kind", mode="before")
    @classmethod
    def _validate_kind(cls, value: Any) -> str:
        text = _normalize_text(value).lower()
        if not text:
            raise ValueError("entity kind must not be empty")
        return text

    @field_validator("keywords", "deictic_patterns", "id_keys", mode="before")
    @classmethod
    def _validate_text_tuple(cls, value: Any) -> tuple[str, ...]:
        return _normalize_tuple(value)

    @field_validator("allowed_child_kinds", "temporal_keys", mode="before")
    @classmethod
    def _validate_lower_tuple(cls, value: Any) -> tuple[str, ...]:
        return _normalize_tuple(value, lowercase=True)

    @field_validator("display_label", mode="before")
    @classmethod
    def _validate_display_label(cls, value: Any) -> str:
        return _normalize_text(value)

    @model_validator(mode="after")
    def _validate_patterns_and_temporal_keys(self) -> "EntityRegistryEntry":
        for pattern in self.deictic_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid deictic regex pattern: {pattern}") from exc
        if not self.temporal_keys:
            raise ValueError("temporal_keys must not be empty")
        return self


_GENERIC_DEICTIC_PATTERNS: tuple[str, ...] = (
    r"그\s*(?:항목|대상|건|것|거)",
    r"이\s*(?:항목|대상|건|것|거)",
    r"해당\s*(?:항목|대상|건|것|거)",
)

_ALLOWED_SHARED_KEYWORDS: Mapping[str, tuple[str, ...]] = {}

_REGISTRY_SOURCE: tuple[EntityRegistryEntry, ...] = (
    EntityRegistryEntry(
        kind="project",
        keywords=("과제", "프로젝트", "project"),
        deictic_patterns=(
            r"그\s*과제",
            r"이\s*과제",
            r"해당\s*과제",
            r"방금\s*과제",
        ),
        id_keys=("pjt_id", "pjt_no"),
        display_label="과제",
        allowed_child_kinds=("people", "org", "perf"),
        priority=10,
        temporal_keys=("year",),
    ),
    EntityRegistryEntry(
        kind="perf",
        keywords=("성과", "논문", "특허", "보고서", "기술", "paper", "patent", "performance", "result"),
        deictic_patterns=(
            r"그\s*(?:성과|논문|특허|보고서|기술)",
            r"이\s*(?:성과|논문|특허|보고서|기술)",
            r"해당\s*(?:성과|논문|특허|보고서|기술)",
            r"방금\s*(?:성과|논문|특허|보고서|기술)",
        ),
        id_keys=("rst_id", "doi", "issn"),
        display_label="성과",
        allowed_child_kinds=("people", "org"),
        priority=20,
        temporal_keys=("year",),
    ),
    EntityRegistryEntry(
        kind="people",
        keywords=("연구자", "연구원", "사람", "인물", "person", "researcher"),
        deictic_patterns=(
            r"그\s*(?:연구자|연구원|사람|인물)",
            r"이\s*(?:연구자|연구원|사람|인물)",
            r"해당\s*(?:연구자|연구원|사람|인물)",
        ),
        id_keys=("person_no",),
        display_label="연구자",
        allowed_child_kinds=("project", "perf", "org"),
        priority=30,
        temporal_keys=("year",),
    ),
    EntityRegistryEntry(
        kind="org",
        keywords=("기관", "회사", "조직", "대학", "기업", "org", "organization", "institution", "agency"),
        deictic_patterns=(
            r"그\s*(?:기관|회사|조직|대학|기업)",
            r"이\s*(?:기관|회사|조직|대학|기업)",
            r"해당\s*(?:기관|회사|조직|대학|기업)",
        ),
        id_keys=("org_id", "org_code", "biz_no"),
        display_label="기관",
        allowed_child_kinds=("project", "perf", "people"),
        priority=40,
        temporal_keys=("year",),
    ),
)


def validate_entity_registry(
    entries: Iterable[EntityRegistryEntry | Mapping[str, Any]],
    *,
    allowed_shared_keywords: Optional[Mapping[str, Iterable[str]]] = None,
) -> dict[str, EntityRegistryEntry]:
    normalized_entries = [
        entry if isinstance(entry, EntityRegistryEntry) else EntityRegistryEntry.model_validate(entry)
        for entry in entries
    ]
    by_kind: dict[str, EntityRegistryEntry] = {}
    for entry in normalized_entries:
        if entry.kind in by_kind:
            raise ValueError(f"duplicate entity kind: {entry.kind}")
        by_kind[entry.kind] = entry

    allowed_shared = {
        _normalize_text(keyword).lower(): {normalize_entity_kind(kind, default="") for kind in kinds}
        for keyword, kinds in dict(allowed_shared_keywords or _ALLOWED_SHARED_KEYWORDS).items()
    }
    keyword_owners: dict[str, list[EntityRegistryEntry]] = {}
    for entry in normalized_entries:
        for keyword in entry.keywords:
            keyword_owners.setdefault(keyword.lower(), []).append(entry)

    for keyword, owners in keyword_owners.items():
        if len(owners) <= 1:
            continue
        owner_kinds = {entry.kind for entry in owners}
        if not owner_kinds <= allowed_shared.get(keyword, set()):
            raise ValueError(f"unapproved keyword collision: {keyword} -> {sorted(owner_kinds)}")
        priorities = {entry.priority for entry in owners}
        if len(priorities) != len(owners):
            raise ValueError(f"shared keyword collision requires distinct priorities: {keyword}")

    return {
        entry.kind: entry
        for entry in sorted(normalized_entries, key=lambda item: (int(item.priority), item.kind))
    }


_REGISTRY: dict[str, EntityRegistryEntry] = validate_entity_registry(_REGISTRY_SOURCE)


def _registry_values(registry: Optional[Mapping[str, EntityRegistryEntry]] = None) -> tuple[EntityRegistryEntry, ...]:
    source = registry or _REGISTRY
    return tuple(sorted(source.values(), key=lambda item: (int(item.priority), item.kind)))


def all_entity_kinds() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def known_entity_kind(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in _REGISTRY


def normalize_entity_kind(value: Any, *, default: str = "project") -> str:
    text = str(value or "").strip().lower()
    if text:
        return text
    return str(default).strip().lower() if default is not None else ""


def entity_registry_entry(
    kind: Any,
    *,
    registry: Optional[Mapping[str, EntityRegistryEntry]] = None,
) -> Optional[EntityRegistryEntry]:
    return (registry or _REGISTRY).get(normalize_entity_kind(kind, default=""))


def entity_keywords_for(kind: Any) -> tuple[str, ...]:
    entry = entity_registry_entry(kind)
    return entry.keywords if entry else ()


def entity_id_keys_for(kind: Any) -> tuple[str, ...]:
    entry = entity_registry_entry(kind)
    return entry.id_keys if entry else ()


def temporal_keys_for(
    kind: Any,
    *,
    registry: Optional[Mapping[str, EntityRegistryEntry]] = None,
) -> tuple[str, ...]:
    entry = entity_registry_entry(kind, registry=registry)
    return entry.temporal_keys if entry else ("year",)


def candidate_temporal_value(
    candidate: Any,
    kind: Any = None,
    *,
    registry: Optional[Mapping[str, EntityRegistryEntry]] = None,
) -> Optional[str]:
    candidate_kind = normalize_entity_kind(kind or getattr(candidate, "entity_kind", None), default="")
    for key in temporal_keys_for(candidate_kind, registry=registry):
        value = candidate.get(key) if isinstance(candidate, Mapping) else getattr(candidate, key, None)
        text = _normalize_text(value)
        if text:
            return text
    return None


def deictic_patterns_for(kind: Any, *, include_generic: bool = False) -> tuple[Pattern[str], ...]:
    entry = entity_registry_entry(kind)
    pattern_sources = entry.deictic_patterns if entry else ()
    if include_generic:
        pattern_sources = (*pattern_sources, *_GENERIC_DEICTIC_PATTERNS)
    return tuple(re.compile(pattern) for pattern in pattern_sources)


def detect_entity_kind_from_text(
    text: Any,
    *,
    allowed_kinds: Optional[Iterable[str]] = None,
    registry: Optional[Mapping[str, EntityRegistryEntry]] = None,
) -> Optional[str]:
    question = str(text or "").strip().lower()
    if not question:
        return None
    allowed = {normalize_entity_kind(kind, default="") for kind in allowed_kinds or ()}
    for entry in _registry_values(registry):
        if allowed and entry.kind not in allowed:
            continue
        if any(keyword and keyword.lower() in question for keyword in entry.keywords):
            return entry.kind
    return None
