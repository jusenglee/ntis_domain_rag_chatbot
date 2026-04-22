from __future__ import annotations

from dataclasses import dataclass
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
    """괄호가 없는 일반 텍스트에서 인명/기관명 후보를 추출합니다."""
    # NTIS 도메인에서 제외할 공통 키워드
    exclusions = {"과제", "성과", "연구자", "활동기록", "활동내역", "참여이력", "목록", "리스트", "보여줘", "알려줘", "찾아줘"}
    candidates: list[str] = []
    for token in _tokenize(question):
        # 2~4글자의 한글 토큰 중 제외 키워드가 아닌 것을 후보로 간주
        if 2 <= len(token) <= 5 and all("\uac00" <= c <= "\ud7a3" for c in token):
            if token not in exclusions:
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
    
    # 추가: 일반 텍스트 스캔 결과 병합
    unstructured_candidates = _scan_unstructured_entity_terms(question)
    
    people_terms = _dedupe(normalized_people or fallback_people or unstructured_candidates)
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
    )
