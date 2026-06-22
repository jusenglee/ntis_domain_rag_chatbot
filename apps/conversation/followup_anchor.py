from __future__ import annotations

import re
from typing import Any, Dict, Optional

from apps.conversation.view_state import (
    DisplaySnapshot,
    FocusEntity,
    RecentMentionRecord,
    SubjectIndexEntry,
    focus_entity_from_item,
    focus_entity_subject_id,
)

# Removed per ADR-0015 Stage 4: the legacy `turn_interpreter.TurnCandidate`
# type was only referenced by the deleted helpers `_candidate_matches_ids`
# and `resolve_candidate_to_focus_entity` below.


_ORDINAL_PATTERNS = (
    re.compile("(?:제\\s*)?(\\d{1,3})\\s*번째"),
    re.compile("(?:제\\s*)?(\\d{1,3})\\s*번"),
    re.compile(r"\b(\d{1,3})(?:st|nd|rd|th)\b", re.IGNORECASE),
)
_SOURCE_REFERENCE_PATTERNS = (
    re.compile("(?:출처|source)\\s*(\\d{1,3})"),
    re.compile("(?:출처|source)\\s*(첫번째|첫\\s*번째|첫째|첫|두번째|두\\s*번째|둘째|두|세번째|세\\s*번째|셋째|세)"),
    re.compile("참고\\s*(?:문헌|자료)\\s*(\\d{1,3})"),
    re.compile("참고\\s*(?:문헌|자료)\\s*(첫번째|첫\\s*번째|첫째|첫|두번째|두\\s*번째|둘째|두|세번째|세\\s*번째|셋째|세)"),
)
_ORDINAL_WORDS = {
    "\uccab": 1,
    "\uccab\ubc88\uc9f8": 1,
    "\uccab \ubc88\uc9f8": 1,
    "\uccab\uc9f8": 1,
    "\ub450": 2,
    "\ub450\ubc88\uc9f8": 2,
    "\ub450 \ubc88\uc9f8": 2,
    "\ub458\uc9f8": 2,
    "\uc138": 3,
    "\uc138\ubc88\uc9f8": 3,
    "\uc138 \ubc88\uc9f8": 3,
    "\uc14b\uc9f8": 3,
}
_DEICTIC_PATTERNS = {
    "project": (
        re.compile("그\\s*과제"),
        re.compile("이\\s*과제"),
        re.compile("해당\\s*과제"),
        re.compile("방금\\s*과제"),
        re.compile(r"\b(?:that|this|the)\s+project\b", re.IGNORECASE),
    ),
    "perf": (
        re.compile("그\\s*(?:성과|논문|특허|보고서|기술)"),
        re.compile("이\\s*(?:성과|논문|특허|보고서|기술)"),
        re.compile("해당\\s*(?:성과|논문|특허|보고서|기술)"),
        re.compile(r"\b(?:that|this|the)\s+(?:performance|paper|patent|report|technology|result)\b", re.IGNORECASE),
    ),
    "people": (
        re.compile("그\\s*(?:연구자|연구원|사람)"),
        re.compile("이\\s*(?:연구자|연구원|사람)"),
        re.compile("해당\\s*(?:연구자|연구원|사람)"),
        re.compile(r"\b(?:that|this|the)\s+(?:researcher|research employee|person)\b", re.IGNORECASE),
    ),
    "org": (
        re.compile("그\\s*(?:기관|회사|조직)"),
        re.compile("이\\s*(?:기관|회사|조직)"),
        re.compile("해당\\s*(?:기관|회사|조직)"),
        re.compile(r"\b(?:that|this|the)\s+(?:organization|organisation|agency|company|institution)\b", re.IGNORECASE),
    ),
    "generic": (
        re.compile("그\\s*(?:항목|결과|이거|이것)"),
        re.compile("이\\s*(?:항목|결과|것)"),
        re.compile("그거"),
        re.compile("이전\\s*(?:결과|것)"),
        re.compile(r"\b(?:that|this|the)\s+(?:item|result|entry|one)\b", re.IGNORECASE),
    ),
}
_COUNT_PATTERNS = (
    re.compile(r"\uc0c1\uc704\s*(\d{1,3})\s*\uac1c"),
    re.compile(r"(\d{1,3})\s*(?:\uac1c|\uac74)"),
    re.compile(r"(\ud55c|\ub450|\uc138)\s*\uac74"),
)
_KOREAN_COUNT = {"\ud55c": 1, "\ub450": 2, "\uc138": 3}
_EXPLICIT_ID_KEYS = ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn")

_TITLE_DECORATION_PRESENT_RE = re.compile(r"\[\[.+?\]\]|\[[^\[\]]+?\]|\*\*.+?\*\*")


def _decode_token(token: str) -> str:
    return token.encode("utf-8").decode("unicode_escape")


def normalize_explicit_title_reference(text: str) -> Optional[str]:
    """Return inner title if text is a fully-decorated title reference ([[...]], [...], **...**), else None."""
    s = str(text or "").strip()
    if not s:
        return None
    if s.startswith("[[") and s.endswith("]]") and len(s) > 4:
        return s[2:-2].strip() or None
    if s.startswith("[") and s.endswith("]") and len(s) > 2:
        return s[1:-1].strip() or None
    if s.startswith("**") and s.endswith("**") and len(s) > 4:
        return s[2:-2].strip() or None
    return None


def _normalize_for_title_compare(text: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFC", str(text or "").lower())
    return re.sub(r"[\s ]+", " ", s).strip()


def _resolve_title_in_manifest(normalized_title: str, items: list) -> Optional[Any]:
    """Exact then substring match against DisplayItem list.

    Returns a single DisplayItem on unique match, None on 0 or ≥2 matches.
    """
    norm_q = _normalize_for_title_compare(normalized_title)
    if not norm_q:
        return None
    exact = [i for i in items if _normalize_for_title_compare(i.title_text) == norm_q]
    if len(exact) == 1:
        return exact[0]
    if not exact:
        sub = [
            i for i in items
            if norm_q in _normalize_for_title_compare(i.title_text)
            or _normalize_for_title_compare(i.title_text) in norm_q
        ]
        if len(sub) == 1:
            return sub[0]
    return None


def _is_hangul_syllable(ch: str) -> bool:
    """ch가 한글 음절 문자인지 확인한다."""
    return ('\uac00' <= ch <= '\ud7a3') or ('\u1100' <= ch <= '\u11ff') or ('\u3130' <= ch <= '\u318f')


def _is_name_match_in_text(name: str, text: str) -> bool:
    """name이 text 안에서 독립 토큰으로 등장하는지 확인한다."""
    if not name or len(name) < 2:
        # 1자 이하 이름은 조사/지시사 등과 비교하여 false positive 위험이 높아 처리하지 않는다.
        return False
    pos = 0
    while True:
        idx = text.find(name, pos)
        if idx < 0:
            return False
        if idx > 0 and _is_hangul_syllable(text[idx - 1]):
            # 매칭 직전 문자가 한글 음절이면 더 긴 단어의 일부이므로 건너맰다.
            pos = idx + 1
            continue
        return True


def _resolve_ordinal_value(raw: str) -> Optional[int]:
    token = str(raw or "").strip()
    if not token:
        return None
    if token.isdigit():
        ordinal = int(token)
        return ordinal if ordinal > 0 else None
    compact = re.sub(r"\s+", "", token)
    for candidate, ordinal in _ORDINAL_WORDS.items():
        if _decode_token(candidate).replace(" ", "") == compact:
            return ordinal
    return None


def parse_source_reference(question: str) -> Optional[int]:
    text = str(question or "").strip()
    if not text:
        return None
    for pattern in _SOURCE_REFERENCE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        resolved = _resolve_ordinal_value(match.group(1))
        if resolved is not None:
            return resolved
    return None


def parse_ordinal_reference(question: str) -> Optional[int]:
    text = str(question or "").strip()
    if not text:
        return None
    source_reference = parse_source_reference(text)
    if source_reference is not None:
        return source_reference
    for pattern in _ORDINAL_PATTERNS:
        match = pattern.search(text)
        if match:
            return _resolve_ordinal_value(match.group(1))
    compact = re.sub(r"\s+", "", text)
    for token, ordinal in _ORDINAL_WORDS.items():
        decoded = _decode_token(token).replace(" ", "")
        if len(decoded) <= 1:
            continue
        if decoded in compact:
            return ordinal
    return None


def _entity_kind_matches_question(question: str, entity_kind: str) -> bool:
    patterns = list(_DEICTIC_PATTERNS.get(entity_kind, ())) + list(_DEICTIC_PATTERNS["generic"])
    return any(pattern.search(question) for pattern in patterns)


def is_referential_followup(question: str, *, entity_kind: Optional[str] = None) -> bool:
    text = str(question or "").strip()
    if not text:
        return False
    if entity_kind:
        return _entity_kind_matches_question(text, str(entity_kind or "").strip().lower())
    return any(pattern.search(text) for patterns in _DEICTIC_PATTERNS.values() for pattern in patterns)


def parse_display_limit(question: str, *, default: int) -> int:
    text = str(question or "").strip()
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        raw = str(match.group(1)).strip()
        if raw.isdigit():
            return max(1, int(raw))
        raw_decoded = _decode_token(raw)
        for token, count in _KOREAN_COUNT.items():
            if raw_decoded == _decode_token(token):
                return count
    return max(1, int(default or 1))


def _normalize_ids_map(values: Any) -> Dict[str, list[str]]:
    if not isinstance(values, dict):
        return {}
    normalized: Dict[str, list[str]] = {}
    for key, raw_values in values.items():
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        elif not isinstance(raw_values, (list, tuple, set)):
            raw_values = [raw_values]
        deduped: list[str] = []
        seen: set[str] = set()
        for raw in raw_values:
            text = str(raw or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            deduped.append(text)
        if deduped:
            normalized[str(key).strip()] = deduped
    return normalized


def is_child_anchor_source(source: Any) -> bool:
    text = str(source or "").strip().lower()
    return text.startswith("detail_") or text.startswith("child_")


_CHILD_ANCHOR_SOURCES = {
    "people": "detail_participant_match",
    "org": "detail_org_match",
    "perf": "detail_perf_match",
}


def _build_child_focus_anchor(*, kind: str, ids_map: Dict[str, list[str]], ref: Any, focus: FocusEntity) -> Optional[FocusEntity]:
    if kind == "people":
        person_ids = ids_map.get("person_no") or []
        return FocusEntity(
            kind="people",
            source=_CHILD_ANCHOR_SOURCES["people"],
            view_id=focus.view_id,
            person_no=person_ids[0] if person_ids else None,
            title_text=str(getattr(ref, "display_name", "") or "").strip() or None,
            pjt_id=focus.pjt_id,
            pjt_no=focus.pjt_no,
        )
    if kind == "org":
        org_ids = ids_map.get("org_id") or []
        org_codes = ids_map.get("org_code") or []
        biz_nos = ids_map.get("biz_no") or []
        return FocusEntity(
            kind="org",
            source=_CHILD_ANCHOR_SOURCES["org"],
            view_id=focus.view_id,
            org_id=org_ids[0] if org_ids else None,
            org_code=org_codes[0] if org_codes else None,
            biz_no=biz_nos[0] if biz_nos else None,
            title_text=str(getattr(ref, "display_name", "") or "").strip() or None,
            pjt_id=focus.pjt_id,
            pjt_no=focus.pjt_no,
        )
    if kind == "perf":
        rst_ids = ids_map.get("rst_id") or []
        dois = ids_map.get("doi") or []
        issns = ids_map.get("issn") or []
        return FocusEntity(
            kind="perf",
            source=_CHILD_ANCHOR_SOURCES["perf"],
            view_id=focus.view_id,
            rst_id=rst_ids[0] if rst_ids else None,
            doi=dois[0] if dois else None,
            issn=issns[0] if issns else None,
            title_text=str(getattr(ref, "display_name", "") or "").strip() or None,
            pjt_id=focus.pjt_id,
            pjt_no=focus.pjt_no,
        )
    return None


def _resolve_named_child_anchor_from_focus(*, question: str, focus_entity: Optional[FocusEntity]) -> Optional[FocusEntity]:
    if focus_entity is None:
        return None
    if str(focus_entity.kind or "").strip().lower() != "project":
        return None

    refs = list(getattr(focus_entity, "child_refs", []) or [])
    if not refs:
        return None

    matches: list[FocusEntity] = []
    seen_keys: set[str] = set()
    text = str(question or "").strip()
    if not text:
        return None

    for ref in refs:
        kind = str(getattr(ref, "kind", "") or "").strip().lower()
        if kind not in _CHILD_ANCHOR_SOURCES:
            continue
        display_name = str(getattr(ref, "display_name", "") or "").strip()
        ids_map = _normalize_ids_map(getattr(ref, "ids_map", {}) or {})
        if not display_name or not _is_name_match_in_text(display_name, text):
            continue
        focus_anchor = _build_child_focus_anchor(kind=kind, ids_map=ids_map, ref=ref, focus=focus_entity)
        if focus_anchor is None:
            continue
        identity = "|".join(
            [
                kind,
                str(getattr(focus_anchor, "person_no", None) or ""),
                str(getattr(focus_anchor, "org_id", None) or ""),
                str(getattr(focus_anchor, "org_code", None) or ""),
                str(getattr(focus_anchor, "biz_no", None) or ""),
                str(getattr(focus_anchor, "rst_id", None) or ""),
                str(getattr(focus_anchor, "doi", None) or ""),
                str(getattr(focus_anchor, "issn", None) or ""),
            ]
        )
        if identity in seen_keys:
            continue
        seen_keys.add(identity)
        matches.append(focus_anchor)

    if len(matches) != 1:
        return None

    return matches[0]


def resolve_named_child_anchor_from_focus(*, question: str, focus_entity: Optional[FocusEntity]) -> Optional[FocusEntity]:
    return _resolve_named_child_anchor_from_focus(question=question, focus_entity=focus_entity)


def _iter_subject_index_entries(subject_index: Any) -> list[SubjectIndexEntry]:
    if not isinstance(subject_index, dict):
        return []
    entries: list[SubjectIndexEntry] = []
    for value in subject_index.values():
        try:
            entry = value if isinstance(value, SubjectIndexEntry) else SubjectIndexEntry.model_validate(value)
        except Exception:
            continue
        entries.append(entry)
    return entries


def _build_subject_index_anchor(*, entry: SubjectIndexEntry, focus_entity: Optional[FocusEntity]) -> FocusEntity:
    ids_map = _normalize_ids_map(entry.ids_map)
    if entry.kind == "people":
        person_ids = ids_map.get("person_no") or []
        return FocusEntity(
            kind="people",
            source="child_subject_index_match",
            view_id=getattr(focus_entity, "view_id", None),
            person_no=person_ids[0] if person_ids else None,
            title_text=entry.display_name or None,
            pjt_id=getattr(focus_entity, "pjt_id", None),
            pjt_no=getattr(focus_entity, "pjt_no", None),
        )
    if entry.kind == "org":
        org_ids = ids_map.get("org_id") or []
        org_codes = ids_map.get("org_code") or []
        biz_nos = ids_map.get("biz_no") or []
        return FocusEntity(
            kind="org",
            source="child_subject_index_match",
            view_id=getattr(focus_entity, "view_id", None),
            org_id=org_ids[0] if org_ids else None,
            org_code=org_codes[0] if org_codes else None,
            biz_no=biz_nos[0] if biz_nos else None,
            title_text=entry.display_name or None,
            pjt_id=getattr(focus_entity, "pjt_id", None),
            pjt_no=getattr(focus_entity, "pjt_no", None),
        )
    rst_ids = ids_map.get("rst_id") or []
    dois = ids_map.get("doi") or []
    issns = ids_map.get("issn") or []
    return FocusEntity(
        kind="perf",
        source="child_subject_index_match",
        view_id=getattr(focus_entity, "view_id", None),
        rst_id=rst_ids[0] if rst_ids else None,
        doi=dois[0] if dois else None,
        issn=issns[0] if issns else None,
        title_text=entry.display_name or None,
        pjt_id=getattr(focus_entity, "pjt_id", None),
        pjt_no=getattr(focus_entity, "pjt_no", None),
    )


def _resolve_named_subject_from_index(
    *,
    question: str,
    subject_index: Any,
    focus_entity: Optional[FocusEntity],
) -> Optional[FocusEntity]:
    text = str(question or "").strip()
    if not text:
        return None
    parent_subject_id = focus_entity_subject_id(focus_entity)
    candidates: list[SubjectIndexEntry] = []
    seen_subjects: set[str] = set()
    for entry in _iter_subject_index_entries(subject_index):
        aliases = [entry.display_name, *(entry.aliases or [])]
        matched_alias = next((alias for alias in aliases if _is_name_match_in_text(str(alias or "").strip(), text)), None)
        if not matched_alias:
            continue
        if (
            parent_subject_id
            and entry.parent_subject_ids
            and parent_subject_id not in set(entry.parent_subject_ids)
        ):
            continue
        if entry.subject_id in seen_subjects:
            continue
        seen_subjects.add(entry.subject_id)
        candidates.append(entry)
    if len(candidates) == 1:
        return _build_subject_index_anchor(entry=candidates[0], focus_entity=focus_entity)
    if len(candidates) > 1:
        first = candidates[0]
        return FocusEntity(
            kind=first.kind,
            source="ambiguity_subject_index",
            view_id=getattr(focus_entity, "view_id", None),
            title_text=first.display_name or None,
            pjt_id=getattr(focus_entity, "pjt_id", None),
            pjt_no=getattr(focus_entity, "pjt_no", None),
        )
    return None


_RELATIVE_LAST_PATTERNS = (
    re.compile(r"마지막\s*(?:과제|프로젝트|논문|특허|성과|보고서|결과|항목|것)?"),
    re.compile(r"맨\s*마지막"),
    re.compile(r"최근\s*(?:과제|프로젝트|논문|특허|성과|보고서|결과|항목|것)?"),
    re.compile(r"방금\s*(?:본|검색한|조회한|과제|프로젝트)?\s*(?:과제|프로젝트|논문|것)?"),
)
_RELATIVE_FIRST_PATTERNS = (
    re.compile(r"(?:제\s*)?첫\s*(?:번째)?\s*(?:과제|프로젝트|논문|성과|결과|항목)?"),
)
_RELATIVE_ORDINAL_PATTERNS = (
    re.compile(r"(?:제\s*)?(\d{1,3})\s*번째\s*(?:과제|프로젝트|논문|성과|결과|항목)?"),
)

# "최근"이 시간축/필터 의미로 쓰인 경우를 배제하는 가드 패턴
_CHOEGEUN_TEMPORAL_EXCLUSIONS = (
    re.compile(r"최근\s*\d+\s*(?:년|개월|일|주|건)"),       # "최근 3년", "최근 5건"
    re.compile(r"최근\s*(?:에|들어|동안|까지|부터)"),        # "최근에", "최근 들어"
    re.compile(r"최근\s*(?:추세|동향|트렌드|변화|현황)"),    # "최근 동향" 등 temporal context
)


def _is_temporal_choegeun(text: str) -> bool:
    """'최근'이 시간축/필터 의미로 쓰였는지 판별한다.

    True이면 "최근"을 relative-last 참조로 해석해서는 안 된다.
    """
    return any(pat.search(text) for pat in _CHOEGEUN_TEMPORAL_EXCLUSIONS)


def parse_relative_reference(question: str) -> Optional[Dict[str, Any]]:
    """'마지막', '최근', '방금', '첫 번째' 등 상대적 참조를 파싱한다.

    Returns:
        None: 상대 참조 없음
        {"position": "last"}: 마지막 항목 참조
        {"position": "first"}: 첫 번째 항목 참조
        {"position": "ordinal", "index": N}: N번째 항목 참조
    """
    text = str(question or "").strip()
    if not text:
        return None
    temporal_guard = _is_temporal_choegeun(text)
    for pattern in _RELATIVE_LAST_PATTERNS:
        if pattern.search(text):
            # "최근" 패턴이 시간축 의미인 경우 relative-last 해석 방지
            if temporal_guard and "최근" in pattern.pattern:
                continue
            return {"position": "last"}
    for pattern in _RELATIVE_FIRST_PATTERNS:
        if pattern.search(text):
            return {"position": "first"}
    for pattern in _RELATIVE_ORDINAL_PATTERNS:
        match = pattern.search(text)
        if match:
            return {"position": "ordinal", "index": int(match.group(1))}
    return None


def _detect_entity_kind_from_question(question: str) -> Optional[str]:
    """질문에서 참조 대상의 entity_kind를 추론한다."""
    text = str(question or "").strip()
    if not text:
        return None
    for kind, patterns in _DEICTIC_PATTERNS.items():
        if kind == "generic":
            continue
        for pattern in patterns:
            if pattern.search(text):
                return kind
    # keyword fallback
    if any(kw in text for kw in ("과제", "프로젝트")):
        return "project"
    if any(kw in text for kw in ("성과", "논문", "특허", "보고서")):
        return "perf"
    if any(kw in text for kw in ("연구자", "연구원", "사람")):
        return "people"
    if any(kw in text for kw in ("기관", "회사", "조직")):
        return "org"
    return None


def focus_entity_from_mention(mention: RecentMentionRecord) -> FocusEntity:
    """RecentMentionRecord를 FocusEntity로 변환한다."""
    kind = str(mention.entity_kind or "project").strip().lower() or "project"
    year_int = None
    if mention.year is not None:
        try:
            year_int = int(mention.year)
        except (ValueError, TypeError):
            pass
    return FocusEntity(
        kind=kind,
        source="recent_mention",
        pjt_id=mention.pjt_id,
        pjt_no=mention.pjt_no,
        rst_id=mention.rst_id,
        doi=mention.doi,
        issn=mention.issn,
        title_text=mention.title_text,
        year=year_int,
        lead_org=mention.lead_org,
        participant_org=list(mention.participant_org or []),
        researchers=list(mention.researchers or []),
        display_rank=mention.display_rank,
    )


def resolve_from_recent_mentions(
    *,
    question: str,
    recent_mentions: Optional[list[RecentMentionRecord]],
) -> Optional[FocusEntity]:
    """recent_mentions에서 상대적/직시적 참조를 해석하여 FocusEntity를 반환한다.

    entity_kind를 먼저 좁혀서 project/perf 혼합 해석을 금지한다.
    """
    if not recent_mentions:
        return None

    relative_ref = parse_relative_reference(question)
    is_deictic = is_referential_followup(question)

    if not relative_ref and not is_deictic:
        return None

    # entity_kind 좁히기
    target_kind = _detect_entity_kind_from_question(question)
    if target_kind:
        candidates = [m for m in recent_mentions if str(m.entity_kind or "").strip().lower() == target_kind]
    else:
        candidates = list(recent_mentions)

    if not candidates:
        return None

    if relative_ref:
        pos = relative_ref.get("position")
        if pos == "last":
            return focus_entity_from_mention(candidates[-1])
        if pos == "first":
            return focus_entity_from_mention(candidates[0])
        if pos == "ordinal":
            idx = relative_ref.get("index", 1)
            if 1 <= idx <= len(candidates):
                return focus_entity_from_mention(candidates[idx - 1])
            return None

    # deictic fallback: 후보가 1개면 즉시 확정
    if is_deictic and len(candidates) == 1:
        return focus_entity_from_mention(candidates[0])

    return None


def resolve_followup_anchor(
    *,
    question: str,
    normalized_intent: Any,
    display_snapshot: Optional[DisplaySnapshot],
    focus_entity: Optional[FocusEntity],
    scope_focus_entity: Optional[FocusEntity] = None,
    subject_index: Any = None,
    recent_mentions: Optional[list[RecentMentionRecord]] = None,
) -> Optional[FocusEntity]:
    ids_map = _normalize_ids_map(getattr(normalized_intent, "ids_map", {}) or {})
    for key in _EXPLICIT_ID_KEYS:
        values = ids_map.get(key) or []
        if not values:
            continue
        return FocusEntity(
            kind=str(getattr(normalized_intent, "base_route", None) or "project").strip().lower() or "project",
            source="explicit_id",
            pjt_id=values[0] if key == "pjt_id" else None,
            pjt_no=values[0] if key == "pjt_no" else None,
            rst_id=values[0] if key == "rst_id" else None,
            person_no=values[0] if key == "person_no" else None,
            org_id=values[0] if key == "org_id" else None,
            org_code=values[0] if key == "org_code" else None,
            biz_no=values[0] if key == "biz_no" else None,
            doi=values[0] if key == "doi" else None,
            issn=values[0] if key == "issn" else None,
        )

    # Step 1.5: Explicit decorated-title match against current manifest (pre-LLM strong path)
    if display_snapshot and display_snapshot.items and _TITLE_DECORATION_PRESENT_RE.search(question):
        normalized_title = normalize_explicit_title_reference(question)
        if normalized_title:
            title_item = _resolve_title_in_manifest(normalized_title, display_snapshot.items)
            if title_item is not None:
                return focus_entity_from_item(
                    item=title_item,
                    kind=display_snapshot.context_kind,
                    source="explicit_title_match",
                    view_id=display_snapshot.view_id,
                )

    active_focus = scope_focus_entity or focus_entity
    child_anchor = _resolve_named_child_anchor_from_focus(question=question, focus_entity=active_focus)
    if child_anchor is not None:
        return child_anchor

    subject_anchor = _resolve_named_subject_from_index(
        question=question,
        subject_index=subject_index,
        focus_entity=active_focus,
    )
    if subject_anchor is not None:
        return subject_anchor

    ordinal = parse_ordinal_reference(question)
    if ordinal is not None and display_snapshot and 1 <= ordinal <= len(display_snapshot.items):
        item = display_snapshot.items[ordinal - 1]
        return focus_entity_from_item(item=item, kind=display_snapshot.context_kind, source="display_snapshot", view_id=display_snapshot.view_id)

    if focus_entity is not None and is_referential_followup(question, entity_kind=focus_entity.kind):
        return focus_entity

    if scope_focus_entity is not None and is_referential_followup(question, entity_kind=scope_focus_entity.kind):
        return scope_focus_entity

    if is_referential_followup(question) and display_snapshot and len(display_snapshot.items) == 1:
        item = display_snapshot.items[0]
        return focus_entity_from_item(item=item, kind=display_snapshot.context_kind, source="display_snapshot", view_id=display_snapshot.view_id)

    # --- 5단계: recent_mentions 기반 상대적/직시적 참조 해석 ---
    mention_anchor = resolve_from_recent_mentions(
        question=question,
        recent_mentions=recent_mentions,
    )
    if mention_anchor is not None:
        return mention_anchor

    return None


# Removed per ADR-0015 Stage 4: `_candidate_matches_ids` and
# `resolve_candidate_to_focus_entity` materialized a `turn_interpreter.TurnCandidate`
# back into a `FocusEntity` for the legacy clarification path. The agentic
# tool executor builds anchors via `build_agent_manifest_item_lookup_intent_payload`
# and the deterministic helpers below; neither helper has any remaining caller.


def anchor_to_seed_map(anchor: Optional[FocusEntity]) -> Dict[str, list[str]]:
    if anchor is None:
        return {}
    seed_map = {
        "pjt_id": [anchor.pjt_id] if anchor.pjt_id else [],
        "pjt_no": [anchor.pjt_no] if anchor.pjt_no else [],
        "rst_id": [anchor.rst_id] if anchor.rst_id else [],
        "person_no": [anchor.person_no] if anchor.person_no else [],
        "org_id": [anchor.org_id] if anchor.org_id else [],
        "org_code": [anchor.org_code] if anchor.org_code else [],
        "biz_no": [anchor.biz_no] if anchor.biz_no else [],
        "doi": [anchor.doi] if anchor.doi else [],
        "issn": [anchor.issn] if anchor.issn else [],
    }
    return {key: values for key, values in seed_map.items() if values}


def group_anchor_context_to_seed_map(context: Any) -> Dict[str, list[str]]:
    """GroupAnchorContext → seed_map (pjt_no + 개별 pjt_ids 포함).

    DetailAnchorContext(FocusEntity 앵커) → anchor_to_seed_map() 사용.
    """
    anchor = getattr(context, "anchor", None)
    if anchor is None:
        return {}
    pjt_no = str(getattr(anchor, "pjt_no", "") or "").strip()
    pjt_ids = [str(x).strip() for x in (getattr(anchor, "pjt_ids", None) or []) if str(x).strip()]
    seed_map: Dict[str, list[str]] = {}
    if pjt_no:
        seed_map["pjt_no"] = [pjt_no]
    if pjt_ids:
        seed_map["pjt_id"] = pjt_ids
    return {key: values for key, values in seed_map.items() if values}
