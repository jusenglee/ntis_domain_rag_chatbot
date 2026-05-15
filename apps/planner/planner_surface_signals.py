from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.planner.query_intent import normalize_korean_temporal_years


_COUNT_SUFFIXES = ("\uac1c", "\uac74", "\uba85", "\ud3b8", "\uc885")
_ORDINAL_SUFFIXES = ("\ubc88", "\ubc88\uc9f8")
_YEAR_SUFFIXES = ("\ub144",)
_ID_LABELS = ("PJT_ID", "PJT_NO", "RST_ID", "DOI", "ISSN")
_FOLLOWUP_CUES = ("\uc774", "\uadf8", "\uc800", "\uc55e\uc758", "\uc774\uc804", "\uc704", "\ud574\ub2f9")
_ORG_HINTS = (
    "\uc5f0\uad6c\uc6d0",
    "\ub300\ud559",
    "\uc0b0\ud559\ud611\ub825\ub2e8",
    "\uc13c\ud130",
    "\uc7ac\ub2e8",
    "\ud559\uad50",
    "\uae30\uad00",
    "\ud68c\uc0ac",
)

# \ud55c\uad6d \ub2e8\uc131/\ubcf5\uc131 \ud654\uc774\ud2b8\ub9ac\uc2a4\ud2b8. \uc77c\ubc18 \uba85\uc0ac \ud1a0\ud070\uc744 \uc0ac\ub78c\uba85 \ud6c4\ubcf4\ub85c \ub04c\uc5b4\uc62c\ub9ac\uc9c0 \uc54a\ub3c4\ub85d
# `_scan_unstructured_entity_terms`\uac00 \uc774 set\uc73c\ub85c \uc2dc\uc791\ud558\ub294 \ud1a0\ud070\ub9cc raw_person_hint\ub85c \ucc44\ud0dd\ud55c\ub2e4.
_KOREAN_SURNAMES = frozenset(
    {
        # \ub2e8\uc131
        "\uae40", "\uc774", "\ubc15", "\ucd5c", "\uc815", "\uc870", "\uac15", "\uc7a5",
        "\uc724", "\uc784", "\ud55c", "\uc624", "\uc11c", "\uc2e0", "\uad8c", "\ud669",
        "\uc548", "\uc1a1", "\uc720", "\ud64d", "\uc804", "\uace0", "\ubb38", "\uc190",
        "\uc591", "\ubc30", "\ubc31", "\ud5c8", "\ub0a8", "\uc2ec", "\ub178", "\ud558",
        "\uacfd", "\uc131", "\ucc28", "\uc8fc", "\uc6b0", "\uad6c", "\ubbfc", "\ub098",
        "\uc9c4", "\uc9c0", "\uc5c4", "\ubcc0", "\ucc44", "\uc6d0", "\ucc9c", "\ubc29",
        "\uacf5", "\ud604", "\ud568", "\ubcf5", "\ud45c", "\uc2dc", "\ub3c4", "\uacbd",
        "\uc5f0", "\uc5ec", "\ucd94", "\uc5b4", "\ub77c", "\uae30", "\ubc18", "\uc655", "\uae08",
        "\uc625", "\uc721", "\uc778", "\ub9f9", "\uc81c", "\ubaa8", "\ud0c1", "\uad6d",
        "\uc5ec", "\uc9c4", "\ud3b8", "\uc6a9", "\uc608", "\ubd09",
        # \uc8fc\uc694 \ubcf5\uc131
        "\ub0a8\uad81", "\ud669\ubcf4", "\uc81c\uac08", "\uc0ac\uacf5", "\uc120\uc6b0",
        "\uc11c\ubb38", "\ub3c5\uace0",
    }
)


@dataclass(frozen=True)
class SurfaceSignals:
    explicit_count: int | None
    ordinal_ref: int | None
    years: list[str]
    id_like_terms: list[str]
    people_terms: list[str]
    org_terms: list[str]
    perf_types: list[str]
    followup_cues: list[str]
    high_salience_terms: list[str]
    raw_person_hint_terms: list[str] = field(default_factory=list)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _tokenize(question: str) -> list[str]:
    return [token.strip() for token in str(question or "").replace(",", " ").split() if token.strip()]


def _looks_org_like(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return any(marker in normalized for marker in _ORG_HINTS)


def _looks_internal_perf_tag(text: str) -> bool:
    normalized = str(text or "").strip().upper()
    return normalized.startswith("IRD_")


def _scan_parenthesized_entity_terms(question: str) -> tuple[list[str], list[str]]:
    people_terms: list[str] = []
    org_terms: list[str] = []
    for token in _tokenize(question):
        if "(" not in token or ")" not in token:
            continue
        left, _, rest = token.partition("(")
        inner, _, _ = rest.partition(")")
        left = left.strip()
        inner = inner.strip()
        if left and not _looks_org_like(left):
            people_terms.append(left)
        if inner and _looks_org_like(inner):
            org_terms.append(inner)
    return _dedupe(people_terms), _dedupe(org_terms)


def _scan_explicit_count(question: str) -> int | None:
    for token in _tokenize(question):
        for suffix in _COUNT_SUFFIXES:
            if token.endswith(suffix):
                number = token[: -len(suffix)].strip()
                if number.isdigit():
                    return int(number)
    return None


def _scan_ordinal_ref(question: str) -> int | None:
    for token in _tokenize(question):
        for suffix in _ORDINAL_SUFFIXES:
            if token.endswith(suffix):
                number = token[: -len(suffix)].strip()
                if number.isdigit():
                    return int(number)
    return None


def _scan_years(question: str, normalized_intent: Any) -> list[str]:
    existing = [str(value).strip() for value in (getattr(normalized_intent, "years", None) or []) if str(value).strip()]
    years = list(existing)
    # 한국어 상대 시간 표현 → 절대 연도
    for y in normalize_korean_temporal_years(question):
        years.append(y)
    # 기존 4자리 숫자 연도 추출
    for token in _tokenize(question):
        if token.endswith(_YEAR_SUFFIXES):
            number = token[:-1].strip()
            if number.isdigit() and len(number) == 4:
                years.append(number)
        elif token.isdigit() and len(token) == 4 and token.startswith(("19", "20")):
            years.append(token)
    return _dedupe(years)


def _scan_id_labels(question: str, normalized_intent: Any) -> list[str]:
    values = list((getattr(normalized_intent, "ids_map", None) or {}).keys())
    question_upper = str(question or "").upper()
    values.extend(label for label in _ID_LABELS if label in question_upper)
    return _dedupe([str(value).strip() for value in values if str(value).strip()])


def _scan_followup_cues(question: str) -> list[str]:
    q = str(question or "")
    return [cue for cue in _FOLLOWUP_CUES if cue in q]


def _scan_unstructured_entity_terms(question: str) -> list[str]:
    """질문에서 한국 성씨로 시작하는 2~4자 한글 토큰만 raw 인명 후보로 추출한다.

    여기서 추출된 토큰은 확정 인명이 아니라 Stage 1.5 LLM이 explicit cue와
    함께 판단하도록 raw_person_hint_terms 채널로만 노출된다.
    """
    candidates: list[str] = []
    for token in _tokenize(question):
        if not (2 <= len(token) <= 4):
            continue
        if not all("\uac00" <= c <= "\ud7a3" for c in token):
            continue
        # \ubcf5\uc131(2\uc790) \uc6b0\uc120 \ub9e4\uce6d \ud6c4 \ub2e8\uc131(1\uc790) \ub9e4\uce6d
        if token[:2] in _KOREAN_SURNAMES or token[0] in _KOREAN_SURNAMES:
            candidates.append(token)
    return candidates


def collect_surface_signals(question: str, normalized_intent: Any) -> SurfaceSignals:
    normalized_people = [
        str(value).strip()
        for value in (getattr(normalized_intent, "people_terms", None) or [])
        if str(value).strip()
    ]
    normalized_orgs = [
        str(value).strip()
        for value in (getattr(normalized_intent, "org_terms", None) or [])
        if str(value).strip()
    ]
    fallback_people, fallback_orgs = _scan_parenthesized_entity_terms(question)

    # 성씨 휴리스틱은 raw_hint 채널로만 노출하고, people_terms(확정 인용 신호)에는
    # normalized/fallback 만 채택해 노이즈가 sole source로 승격되는 경로를 차단한다.
    raw_person_hint_terms = _dedupe(_scan_unstructured_entity_terms(question))

    people_terms = _dedupe(normalized_people or fallback_people)
    org_terms = _dedupe(normalized_orgs or fallback_orgs)
    perf_types = _dedupe(
        [
            str(value).strip()
            for value in (getattr(normalized_intent, "perf_types", None) or [])
            if str(value).strip() and not _looks_internal_perf_tag(value)
        ]
    )
    years = _scan_years(question, normalized_intent)
    id_like_terms = _scan_id_labels(question, normalized_intent)
    followup_cues = _scan_followup_cues(question)
    high_salience_terms = _dedupe(people_terms + org_terms + perf_types + years + id_like_terms)
    return SurfaceSignals(
        explicit_count=_scan_explicit_count(question),
        ordinal_ref=_scan_ordinal_ref(question),
        years=years,
        id_like_terms=id_like_terms,
        people_terms=people_terms,
        org_terms=org_terms,
        perf_types=perf_types,
        followup_cues=followup_cues,
        high_salience_terms=high_salience_terms,
        raw_person_hint_terms=raw_person_hint_terms,
    )
