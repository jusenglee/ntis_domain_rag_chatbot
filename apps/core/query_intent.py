from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from apps.core.rag_constants import (
    COL_PERF,
    COL_PROJECT,
    PERF_TAGS,
    PROJECT_TAGS,
    RARE_TOKEN_RE,
    TAG_PJT_INFO,
    TAG_RI_COMPOUND,
    TAG_RI_FCLT_EQUIP,
    TAG_RI_IPR,
    TAG_RI_NVR,
    TAG_RI_ORGSM_INFO,
    TAG_RI_ORGSM_RES,
    TAG_RI_PAPER,
    TAG_RI_RSCH_RPT,
    TAG_RI_SW,
    TAG_RI_TECH_INFO,
    normalize_perf_types,
)

_YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_ISSN_RE = re.compile(r"\b\d{4}-\d{3}[\dXx]\b")
_RST_ID_RE = re.compile(r"\b[A-Z]{2,6}-\d{4}-\d{6,}\b")
_PJT_ID_RE = re.compile(r"\b\d{8,12}\b")
_PJT_NO_RE = re.compile(r"\bPJT[-_/]?[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+){1,}\b", re.IGNORECASE)
_PJT_ID_LABEL_RE = re.compile(r"(?:과제고유번호|과제\s*고유\s*번호|pjt[_\s-]*id|project\s*id)", re.IGNORECASE)
_PJT_NO_LABEL_RE = re.compile(r"(?:과제\s*그룹\s*번호|pjt[_\s-]*no|project\s*group\s*number|group\s*number)", re.IGNORECASE)
_AMBIGUOUS_PROJECT_KEY_RE = re.compile(r"(?:과제번호|과제\s*번호|project\s*number)", re.IGNORECASE)
_LABELED_ALNUM_VALUE_RE = re.compile(r"[=:：]\s*([A-Za-z0-9][A-Za-z0-9_-]{3,63})|\s+([A-Za-z0-9][A-Za-z0-9_-]{3,63})")
_TITLE_QUOTE_RE = re.compile(r'"([^"\n]{2,80})"|\'([^\'\n]{2,80})\'')

SUPERLATIVE_CUES = ["\ucd5c\uace0", "\ucd5c\ub300", "top", "\uac00\uc7a5", "1\uc704", "best", "most"]
DETAIL_CUES = ["\uc0c1\uc138", "\uc815\ubcf4", "\uc124\uba85", "\ub0b4\uc6a9", "\ubcf4\uae30"]
LIST_CUES = ["\ubaa9\ub85d", "\ub9ac\uc2a4\ud2b8", "\ub098\uc5f4", "\ubcf4\uc5ec\uc918", "\uc815\ub9ac"]
COUNT_CUES = ["\uac1c\uc218", "\uac74\uc218", "\uc218", "count", "\uba87"]
TOPIC_CUES = ["\ub3d9\ud5a5", "\uc8fc\uc81c", "\uc694\uc57d", "\uac1c\uc694", "\uc124\uba85", "\uc5f0\uad6c", "\uae30\uc220", "r&d", "rd"]
PEOPLE_CUES = ["\uc5f0\uad6c\uc790", "\uc5f0\uad6c\uc6d0", "\uc5f0\uad6c\ucc45\uc784\uc790", "\ucc38\uc5ec\uc5f0\uad6c\uc6d0", "\ucc38\uc5ec\uc790", "\uc774\ub984", "\uc131\uba85"]
ORG_CUES = ["\uae30\uad00", "\uc18c\uc18d\uae30\uad00", "\ucc38\uc5ec\uae30\uad00", "\uc8fc\uad00\uae30\uad00", "\uc218\ud589\uae30\uad00", "\uae30\uad00\uba85", "\uc0ac\uc5c5\uc790\ubc88\ud638", "\uae30\uad00\ucf54\ub4dc"]
SUPPORT_CUES = ["\ub85c\uadf8\uc778", "\uc624\ub958", "\uc5d0\ub7ec", "\uc811\uc18d", "\uad8c\ud55c", "\ub3c4\uc6c0", "\ubb38\uc758", "\uac00\uc774\ub4dc", "manual", "qna", "api"]
PERF_CUES = [
    "\uc131\uacfc",
    "\uc2e4\uc801",
    "\ub17c\ubb38",
    "\ud2b9\ud5c8",
    "\ubcf4\uace0\uc11c",
    "\uae30\uc220\uc774\uc804",
    "\uae30\uc220\uc694\uc57d",
    "\uc18c\ud504\ud2b8\uc6e8\uc5b4",
    "output",
    "outputs",
    "outcome",
    "paper",
    "papers",
    "patent",
    "patents",
    "report",
    "reports",
]
RELATION_CUES = ["\uc131\uacfc", "\uc5f0\uacc4", "\uad00\ub828", "related", "\uc5f0\ub3d9", "linked"]
PERF_TYPE_LABEL_TO_TAG = {
    "\ub17c\ubb38": TAG_RI_PAPER,
    "paper": TAG_RI_PAPER,
    "\ud2b9\ud5c8": TAG_RI_IPR,
    "patent": TAG_RI_IPR,
    "\ubcf4\uace0\uc11c": TAG_RI_RSCH_RPT,
    "report": TAG_RI_RSCH_RPT,
    "rpt": TAG_RI_RSCH_RPT,
    "\uc7a5\ube44": TAG_RI_FCLT_EQUIP,
    "\uc2dc\uc124": TAG_RI_FCLT_EQUIP,
    "equipment": TAG_RI_FCLT_EQUIP,
    "equip": TAG_RI_FCLT_EQUIP,
    "\uae30\uc220\uc694\uc57d": TAG_RI_TECH_INFO,
    "\uae30\uc220\uc815\ubcf4": TAG_RI_TECH_INFO,
    "tech": TAG_RI_TECH_INFO,
    "\uc18c\ud504\ud2b8\uc6e8\uc5b4": TAG_RI_SW,
    "software": TAG_RI_SW,
    "sw": TAG_RI_SW,
    "\uc0dd\uba85\uc815\ubcf4": TAG_RI_NVR,
    "nvr": TAG_RI_NVR,
    "\ud654\ud569\ubb3c": TAG_RI_COMPOUND,
    "compound": TAG_RI_COMPOUND,
    "\uc0dd\ubb3c\uc815\ubcf4": TAG_RI_ORGSM_INFO,
    "\uc0dd\ubb3c\uc790\uc6d0": TAG_RI_ORGSM_RES,
    "resource": TAG_RI_ORGSM_RES,
}


@dataclass
class QueryIntent:
    """Cheap precheck와 planner 입력이 공유하는 질의 요약 구조다.
    
    이 단계에서는 사람명과 기관명을 적극 복원하지 않고, 연도·식별자·성과유형처럼
    구조적으로 안전한 신호만 먼저 채운다. planner와 runtime은 이 값을 최종 truth가 아니라
    초기 추정값으로 다뤄야 한다."""
    base_route: str = "project"
    category: str = "project"
    categories: List[str] = field(default_factory=list)
    action: str = "topic"
    relation: Optional[Tuple[str, str]] = None
    people_terms: List[str] = field(default_factory=list)
    org_terms: List[str] = field(default_factory=list)
    org_role: Optional[str] = None
    lead_org_terms: List[str] = field(default_factory=list)
    participant_org_terms: List[str] = field(default_factory=list)
    people_affiliation_org_terms: List[str] = field(default_factory=list)
    years: List[str] = field(default_factory=list)
    year_from: Optional[str] = None
    year_to: Optional[str] = None
    perf_types: List[str] = field(default_factory=list)
    title: List[str] = field(default_factory=list)
    project_tag_filters: List[str] = field(default_factory=list)
    perf_tag_filters: List[str] = field(default_factory=list)
    wants_count: bool = False
    wants_list: bool = False
    wants_detail: bool = False
    wants_rank: bool = False
    output_type: str = "summary"
    limit: Optional[int] = None
    retrieval_query: Optional[str] = None
    confidence: float = 0.0
    ids_map: Dict[str, List[str]] = field(default_factory=dict)
    ids_flat: List[str] = field(default_factory=list)
    join_key_mode: Optional[Literal["instance", "group"]] = None


@dataclass(frozen=True)
class RelationRoute:
    """관계형 질의를 hop1/hop2 실행 정보로 고정한 라우팅 정의다."""
    relation: Tuple[str, str]
    hop1_col: str
    hop2_col: str
    hop1_kind: str
    hop2_kind: str
    hop1_tag_filters: Optional[List[str]]
    hop2_tag_filters: Optional[List[str]]
    hop2_label: str


def normalize_categories(values: Any) -> List[str]:
    """카테고리 후보를 소문자 중복 제거 목록으로 정규화한다."""
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value).strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def normalize_join_key_mode(value: Any) -> Optional[str]:
    """JOIN 키 모드를 `instance` 또는 `group`으로만 제한한다."""
    text = str(value or "").strip().lower()
    if text in {"instance", "group"}:
        return text
    return None


def has_explicit_project_id_label(text: str) -> bool:
    """질문에 `pjt_id`를 직접 가리키는 라벨이 있는지 판단한다."""
    return bool(_PJT_ID_LABEL_RE.search(text or ""))


def has_explicit_project_no_label(text: str) -> bool:
    """질문에 `pjt_no`를 직접 가리키는 라벨이 있는지 판단한다."""
    return bool(_PJT_NO_LABEL_RE.search(text or ""))


def has_ambiguous_project_key_label(text: str) -> bool:
    """`과제번호`처럼 instance/group을 가리지 않는 project key 표현을 감지한다."""
    raw = text or ""
    return bool(_AMBIGUOUS_PROJECT_KEY_RE.search(raw)) and not has_explicit_project_id_label(raw) and not has_explicit_project_no_label(raw)


def _extract_labeled_values(text: str, label_re: re.Pattern[str]) -> list[str]:
    """명시 라벨 뒤에 붙은 영문+숫자 식별자 토큰을 추출한다."""
    out: list[str] = []
    raw = text or ""
    for match in label_re.finditer(raw):
        tail = raw[match.end(): match.end() + 80]
        value_match = _LABELED_ALNUM_VALUE_RE.search(tail)
        if not value_match:
            continue
        value = (value_match.group(1) or value_match.group(2) or "").strip()
        if not value or value in out:
            continue
        if not re.fullmatch(r"(?=.*\d)[A-Za-z0-9][A-Za-z0-9_-]{3,63}", value):
            continue
        out.append(value)
    return out


def normalize_org_terms(values: Any) -> List[str]:
    """기관명 후보를 공백 정리와 중복 제거만 적용해 보존한다."""
    if values is None:
        return []
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    out: List[str] = []
    seen = set()
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(text)
    return out


def extract_years(q: str) -> List[str]:
    """질의 문자열에서 연도만 추출해 최대 4개까지 유지한다."""
    years: List[str] = []
    for match in _YEAR_RE.finditer(q or ""):
        year = match.group(1)
        if year not in years:
            years.append(year)
    return years[:4]


def extract_title_terms(q: str, kws: Optional[List[str]] = None) -> List[str]:
    """따옴표로 감싼 제목 후보만 분리해 title filter 입력으로 넘긴다."""
    titles: List[str] = []
    for match in _TITLE_QUOTE_RE.finditer(q or ""):
        value = match.group(1) or match.group(2)
        if value:
            titles.append(value.strip())
    return titles[:4]


def extract_perf_types(q: str, kws: Optional[List[str]] = None) -> List[str]:
    """성과 유형 단서를 정규화된 perf category 목록으로 바꾼다."""
    text = (q or "").lower()
    values = [label for label in PERF_TYPE_LABEL_TO_TAG if label in text]
    normalized = normalize_perf_types(values)
    return list(normalized.get("categories") or values)


def pick_perf_tag_filters(q: str) -> List[str]:
    """질의 단어를 perf tag 필터로 낮춰 서버 필터 입력으로 만든다."""
    text = (q or "").lower()
    tags: List[str] = []
    for label, tag in PERF_TYPE_LABEL_TO_TAG.items():
        if label in text and tag in PERF_TAGS and tag not in tags:
            tags.append(tag)
    return tags


def _cheap_precheck(q: str) -> Dict[str, Any]:
    """planner 호출 전에 안전한 구조 신호만 모은다.
    
    사람명·기관명처럼 의미 복원이 필요한 값은 여기서 확정하지 않는다.
    결과는 planner skip 여부와 초기 hint 조립에만 사용된다."""
    return {
        "years": extract_years(q),
        "perf_tag_filters": pick_perf_tag_filters(q),
        "perf_types": extract_perf_types(q),
        "ids_map": _extract_ids_map(q),
        "title_terms": extract_title_terms(q),
    }


def _has_superlative_cue(text: str) -> bool:
    """최상위/순위형 질문인지 가볍게 감지한다."""
    lowered = (text or "").lower()
    return any(cue in lowered for cue in SUPERLATIVE_CUES)


def relation_target_collections(relation: Optional[Tuple[str, str]]) -> Tuple[str, ...]:
    """relation tuple을 실제 컬렉션 순서로 변환한다."""
    if relation == ("project", "perf"):
        return (COL_PROJECT, COL_PERF)
    if relation == ("perf", "project"):
        return (COL_PERF, COL_PROJECT)
    return ()


def get_relation_route(relation: Optional[Tuple[str, str]]) -> Optional[RelationRoute]:
    """관계형 질의의 hop1/hop2 컬렉션과 기본 태그 정책을 반환한다."""
    if relation == ("project", "perf"):
        return RelationRoute(
            relation=("project", "perf"),
            hop1_col=COL_PROJECT,
            hop2_col=COL_PERF,
            hop1_kind="project",
            hop2_kind="perf",
            hop1_tag_filters=[TAG_PJT_INFO],
            hop2_tag_filters=None,
            hop2_label="performance outputs",
        )
    if relation == ("perf", "project"):
        return RelationRoute(
            relation=("perf", "project"),
            hop1_col=COL_PERF,
            hop2_col=COL_PROJECT,
            hop1_kind="perf",
            hop2_kind="project",
            hop1_tag_filters=None,
            hop2_tag_filters=[TAG_PJT_INFO],
            hop2_label="projects",
        )
    return None


def _extract_ids_map(text: str) -> Dict[str, List[str]]:
    """정규식으로 안전한 식별자 seed만 추출한다.

    숫자형 `pjt_id`와 canonical `pjt_no`는 강한 패턴으로 바로 추출한다.
    영문+숫자 project key는 `과제고유번호`나 `PJT_ID`처럼 slot 라벨이 있을 때만
    `ids_map`에 넣고, `과제번호` 같은 모호 표현만 있는 경우에는 seed로 승격하지 않는다.
    """
    text = text or ""
    ids_map: Dict[str, List[str]] = {}
    pjt_ids = list(dict.fromkeys(match.group(0) for match in _PJT_ID_RE.finditer(text)))
    labeled_pjt_ids = _extract_labeled_values(text, _PJT_ID_LABEL_RE)
    pjt_nos = list(dict.fromkeys(match.group(0) for match in _PJT_NO_RE.finditer(text)))
    labeled_pjt_nos = _extract_labeled_values(text, _PJT_NO_LABEL_RE)
    dois = list(dict.fromkeys(match.group(0) for match in _DOI_RE.finditer(text)))
    issns = list(dict.fromkeys(match.group(0) for match in _ISSN_RE.finditer(text)))
    rst_ids = list(dict.fromkeys(match.group(0) for match in _RST_ID_RE.finditer(text)))

    all_pjt_ids = list(dict.fromkeys([*pjt_ids, *labeled_pjt_ids]))
    all_pjt_nos = list(dict.fromkeys([*pjt_nos, *labeled_pjt_nos]))
    if all_pjt_ids:
        ids_map["pjt_id"] = all_pjt_ids
    if all_pjt_nos:
        ids_map["pjt_no"] = all_pjt_nos
    if dois:
        ids_map["doi"] = dois
    if issns:
        ids_map["issn"] = issns
    if rst_ids:
        ids_map["rst_id"] = rst_ids
    return ids_map


def classify_query(
    q: str,
    kws: List[str],
    *,
    domain_hint: Optional[str] = None,
    hint: Optional[Any] = None,
) -> QueryIntent:
    """질의를 cheap intent로 분류해 planner와 runtime의 초기 입력을 만든다.
    
    이 함수는 SEARCH/LOOKUP/JOIN을 최종 확정하는 판정기가 아니라,
    구조적 단서와 cue 기반 추정을 조립하는 전처리 단계다. 사람명이나
    기관 역할처럼 의미 복원이 필요한 값은 후단 planner에 맡긴다."""
    text = q or ""
    lowered = text.lower()
    ids_map = _extract_ids_map(text)
    years = extract_years(text)
    people_terms: List[str] = []
    org_terms: List[str] = []
    perf_types = extract_perf_types(text)
    perf_tag_filters = pick_perf_tag_filters(text)
    title_terms = extract_title_terms(text)
    org_role = None

    has_support = any(cue in lowered for cue in SUPPORT_CUES)
    has_perf = bool(
        perf_tag_filters
        or perf_types
        or ids_map.get("doi")
        or ids_map.get("issn")
        or ids_map.get("rst_id")
        or any(cue in lowered for cue in PERF_CUES)
    )
    has_project = bool(ids_map.get("pjt_id") or ids_map.get("pjt_no") or "\uacfc\uc81c" in lowered or "project" in lowered or "pjt" in lowered)
    has_people_focus = bool(people_terms) or any(cue in lowered for cue in PEOPLE_CUES) or "researcher" in lowered
    has_org_focus = bool(org_terms) or any(cue in lowered for cue in ORG_CUES)

    relation: Optional[Tuple[str, str]] = None
    if has_project and has_perf and any(cue in lowered for cue in RELATION_CUES):
        relation = ("project", "perf")

    if domain_hint in {"support", "project", "perf", "people", "org"}:
        base_route = domain_hint
    elif has_support:
        base_route = "support"
    elif has_perf and not has_project:
        base_route = "perf"
    else:
        base_route = "project"

    wants_count = any(cue in lowered for cue in COUNT_CUES)
    wants_detail = any(cue in lowered for cue in DETAIL_CUES)
    wants_list = any(cue in lowered for cue in LIST_CUES)
    wants_rank = _has_superlative_cue(lowered)

    if relation:
        action = "list"
    elif wants_count:
        action = "stats"
    elif wants_detail or ids_map:
        action = "detail"
    elif wants_list or ((has_people_focus or has_org_focus) and (has_project or has_perf)):
        action = "list"
    else:
        action = "topic"

    output_type = "relation" if relation else "summary"
    if not relation:
        if action == "detail":
            output_type = "detail"
        elif action == "list":
            output_type = "list"
        elif action == "stats":
            output_type = "stats"

    join_key_mode = None
    if relation:
        if ids_map.get("pjt_no"):
            join_key_mode = "group"
        elif ids_map.get("pjt_id") or ids_map.get("doi") or ids_map.get("issn") or ids_map.get("rst_id"):
            join_key_mode = "instance"

    categories = normalize_categories([base_route] + (["perf"] if has_perf else []) + (["project"] if has_project else []))
    ids_flat = [value for values in ids_map.values() for value in values]
    confidence = 0.85 if ids_map else 0.6

    return QueryIntent(
        base_route=base_route,
        category=base_route,
        categories=categories,
        action=action,
        relation=relation,
        people_terms=people_terms,
        org_terms=normalize_org_terms(org_terms),
        org_role=org_role,
        lead_org_terms=normalize_org_terms(org_terms if org_role == "lead" else []),
        participant_org_terms=normalize_org_terms(org_terms if org_role == "participant" else []),
        people_affiliation_org_terms=normalize_org_terms(org_terms if org_role == "affiliation" else []),
        years=years,
        year_from=years[0] if years else None,
        year_to=years[-1] if years else None,
        perf_types=perf_types,
        title=title_terms,
        project_tag_filters=[TAG_PJT_INFO] if has_project else [],
        perf_tag_filters=perf_tag_filters,
        wants_count=wants_count,
        wants_list=wants_list,
        wants_detail=wants_detail,
        wants_rank=wants_rank,
        output_type=output_type,
        limit=None,
        retrieval_query=text.strip() or None,
        confidence=confidence,
        ids_map=ids_map,
        ids_flat=ids_flat,
        join_key_mode=normalize_join_key_mode(join_key_mode),
    )
