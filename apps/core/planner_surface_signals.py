from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_COUNT_SUFFIXES = ("개", "건", "명", "편", "종")
_ORDINAL_SUFFIXES = ("번", "번째")
_YEAR_SUFFIXES = ("년",)
_ID_LABELS = ("PJT_ID", "PJT_NO", "RST_ID", "DOI", "ISSN")
_FOLLOWUP_CUES = ("이", "그", "저", "앞의", "이전", "위", "해당")


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


def collect_surface_signals(question: str, normalized_intent: Any) -> SurfaceSignals:
    people_terms = _dedupe([str(value).strip() for value in (getattr(normalized_intent, "people_terms", None) or []) if str(value).strip()])
    org_terms = _dedupe([str(value).strip() for value in (getattr(normalized_intent, "org_terms", None) or []) if str(value).strip()])
    perf_types = _dedupe([str(value).strip() for value in (getattr(normalized_intent, "perf_types", None) or []) if str(value).strip()])
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
    )
