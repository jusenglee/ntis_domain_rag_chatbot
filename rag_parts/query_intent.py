# -*- coding: utf-8 -*-
"""
Query intent analysis (fine-grained)

Goal:
- 질의를 "도메인(route)" + "행위(action)" + "구조(intent)" 로 분해
- 추출된 엔티티(org/year/id/perf_type/person 등)를 기반으로 검색 파라미터/필터/조기종료를 조정

핵심 개선
- base_route: support/project/perf 뿐 아니라 people/org 를 1급 엔티티로 승격
- ids: 타입별 dict(ids_map) + 랭킹용 flat(ids_flat) 동시 제공
- tag_filters: project(per-tag) / perf(per-tag) 둘 다 제공
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Literal, Optional, Tuple

from .constants import (
    RARE_TOKEN_RE,
    COL_PROJECT,
    COL_PERF,
    TAG_PJT_INFO,
    PROJECT_TAGS,
    PERF_TAGS,
    TAG_RI_PAPER,
    TAG_RI_IPR,
    TAG_RI_RSCH_RPT,
    TAG_RI_FCLT_EQUIP,
    TAG_RI_TECH_INFO,
    TAG_RI_SW,
    TAG_RI_NVR,
    TAG_RI_COMPOUND,
    TAG_RI_ORGSM_INFO,
    TAG_RI_ORGSM_RES,
)
logger = logging.getLogger(__name__)

# -----------------------------
# Regex
# -----------------------------
_YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_ISSN_RE = re.compile(r"\b\d{4}-\d{3}[\dXx]\b")

# RST_ID 패턴(논문/특허/보고서/기술요약/화합물 등): REP-2025-..., PTR-2018-..., TAI-2012-..., COM-2015-...
_RST_ID_RE = re.compile(r"\b[A-Z]{2,6}-\d{4}-\d{6,}\b")

# 특허 "등록번호"에서 흔한 형태: 10-1830958-0000 (두 번째 그룹 길이 가변)
_PATENT_REG_NO_RE = re.compile(r"\b10-\d{4,8}-\d{4}\b")

# 사업자등록번호: 220-88-92965
_BIZ_NO_RE = re.compile(r"\b\d{3}-\d{2}-\d{5}\b")

# 기관코드 예: ABW6901
_ORG_CODE_RE = re.compile(r"\b[A-Z]{2,5}\d{3,6}\b")

# PJT_ID는 보통 8~12자리 숫자
_PJT_ID_NUM_RE = re.compile(r"\b\d{8,12}\b")
# PJT_NO 패턴: 라벨 기반 또는 PJT 접두 + 하이픈/슬래시 포함
_PJT_NO_LABEL_RE = re.compile(
    r"(?:PJT[_\s-]?NO|PROJECT[_\s-]?NO|과제번호|과제\s*번호)\s*[:：]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-\/]{3,})",
    re.IGNORECASE,
)
_PJT_NO_TOKEN_RE = re.compile(r"\bPJT[-_/]?[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+){1,}\b", re.IGNORECASE)

# 사람 이름 후보: 한글 2~4자 (단독으로는 오탐이 많아서 '연구자/연구원/참여인력' 등 주변 신호와 결합)
_NAME_NEAR_CUE_RE = re.compile(r"(?<![가-힣])([가-힣]{2,4})\s+(?:연구자|연구원|교수|박사|PI|책임자|연구책임자|참여연구원|참여인력)")
_NAME_LABEL_RE = re.compile(r"(?:인물명|연구자명|성명|이름)\s*[:：]\s*([가-힣]{2,4})")

# 기관명 후보 (suffix 기반 + 라벨 기반)
_ORG_SUFFIXES = [
    "대학교", "대학", "산학협력단", "연구원", "연구소", "센터", "재단", "병원",
    "공사", "공단", "협회", "청", "부", "처", "원",
    "주식회사", "㈜", "회사", "Corp", "Inc", "Ltd", "LLC",
]
_ORG_NEAR_LABEL_RE = re.compile(
    r"(?:기관|소속|주관|수행|참여)\s*(?:기관명)?\s*(?:[:：]\s*)?"
    r"([가-힣A-Za-z0-9㈜().·\-\s]{2,40})"
)
# suffix로 끝나는 덩어리(공백 포함 허용)
_ORG_SUFFIX_RE = re.compile(
    r"([가-힣A-Za-z0-9㈜().·\-\s]{2,40}(?:"
    + "|".join(map(re.escape, _ORG_SUFFIXES))
    + r"))"
)
_ORG_ACRONYM_RE = re.compile(r"^[A-Z]{3,10}$")
_ORG_TERM_STOPWORDS = {
    "이력",
    "현황",
    "목록",
    "참여목록",
    "정보",
    "과제",
    "과제정보",
    "과제명",
    "리스트",
    "조회",
    "명단",
    "안내",
    "내용",
    "상세",
    "참여",
}


_ORG_ALIAS_GROUPS = [
    ["etri", "한국전자통신연구원"],
    ["kist", "한국과학기술연구원"],
    ["kaist", "한국과학기술원"],
]

_ORG_LEGAL_PREFIX_RE = re.compile(r"^(?:\(주\)|㈜|주식회사|\(재\)|재단법인)\s*")
_ORG_PAREN_RE = re.compile(r"[\(\[\{<].*?[\)\]\}>]")
_ORG_SUFFIX_TRIM_RE = re.compile(r"(?:대학교|대학|연구원|연구소)$")


def _normalize_org_term(term: str) -> str:
    text = re.sub(r"\s+", " ", (term or "")).strip()
    if not text:
        return ""
    text = _ORG_LEGAL_PREFIX_RE.sub("", text)
    text = _ORG_PAREN_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" .,-")
    return text


def normalize_org_terms(values: Any, *, with_alias: bool = True) -> List[str]:
    if values is None:
        seq: List[Any] = []
    elif isinstance(values, str):
        seq = [values]
    elif isinstance(values, (list, tuple, set)):
        seq = list(values)
    else:
        seq = [values]

    base_terms: List[str] = []
    seen_base: set[str] = set()
    for raw in seq:
        term = _normalize_org_term(str(raw))
        if not term:
            continue
        key = term.lower()
        if key in seen_base:
            continue
        seen_base.add(key)
        base_terms.append(term)

    if not with_alias:
        return base_terms

    out: List[str] = []
    seen: set[str] = set()

    def _push(term: str) -> None:
        t = _normalize_org_term(term)
        if not t:
            return
        k = t.lower()
        if k in seen:
            return
        seen.add(k)
        out.append(t)

    for term in base_terms:
        _push(term)
        trimmed = _ORG_SUFFIX_TRIM_RE.sub("", term).strip()
        if trimmed and len(trimmed) >= 2:
            _push(trimmed)
        tl = term.lower()
        for group in _ORG_ALIAS_GROUPS:
            if any(alias in tl for alias in group):
                for alias in group:
                    _push(alias)

    return out


def _is_rare_token(tok: str) -> bool:
    t = (tok or "").strip()
    if not t:
        return False
    tl = t.lower()
    if RARE_TOKEN_RE.search(tl):
        return True
    has_alpha = any("a" <= ch <= "z" for ch in tl)
    has_digit = any(ch.isdigit() for ch in tl)
    if len(tl) >= 4 and has_alpha and has_digit:
        return True
    return False


# -----------------------------
# Cue lists
# -----------------------------
INTENT_MAX_LIMIT = 20
INTENT_MAX_RETRIEVAL_QUERY = 120

CHEAP_GREETING_CUES = [
    "안녕", "안녕하세요", "hello", "hi", "반가워", "문의드립니다", "질문이요",
]
CHEAP_SYSTEM_CUES = [
    "로그인", "비밀번호", "아이디", "인증", "권한", "승인",
    "오류", "에러", "실패", "안돼", "안되", "안됨", "안됩니다", "접속", "접속불가",
]

SUPPORT_STRONG_CUES = [
    "회원가입", "가입", "로그인", "비밀번호", "아이디", "인증", "재설정", "변경", "탈퇴", "권한", "승인",
    "오류", "에러", "실패", "안돼", "안되", "안됨", "안됩니다", "접속", "접속불가", "권한없음", "access denied", "forbidden",
    "버튼", "메뉴", "화면", "가이드", "매뉴얼", "메뉴얼", "qna", "manual",
    "api", "openapi", "연계",
    "문의", "고객센터", "전화", "메일",
]
SUPPORT_WEAK_CUES = ["다운로드", "엑셀", "추출", "간편추출", "절차", "방법", "어떻게"]

PROJECT_CUES = [
    "과제", "과제정보", "과제명",
    "과제번호", "과제 번호", "pjt", "pjt_id", "project",
    "연구기간", "시작일", "종료일",
    "주관기관", "참여기관", "참여인력",
    "협약", "단계", "상태",
]
PERF_CUES = [
    "성과", "성과목록", "성과 목록", "성과리스트", "성과 리스트",
    "논문", "paper",
    "특허", "patent",
    "연구보고서", "보고서", "rpt", "report",
    "시설장비", "장비", "equip", "equipment",
    "기술요약", "기술정보", "tech",
    "소프트웨어", "sw", "software",
    "신품종", "nvr",
    "화합물", "compound",
    "생명정보", "생물자원", "orgsm", "resource",
]

PEOPLE_CUES = [
    "연구자", "연구원", "참여인력", "참여 인력", "참여연구원", "참여 연구원",
    "연구책임자", "연구 책임자", "책임자", "참여자", "인력", "연구진", "인물",
    "국가연구자번호", "과학기술인등록번호", "인물id", "인물 id", "참여인력일련번호",
    "성별", "남자", "여자", "남성", "여성",
]


def _normalize_people_term(term: str) -> str:
    return re.sub(r"\s+", "", (term or "")).strip().lower()


_PEOPLE_CUE_SET = {_normalize_people_term(term) for term in PEOPLE_CUES}
_PEOPLE_TERM_STOPWORDS = {
    *_PEOPLE_CUE_SET,
    _normalize_people_term("교수"),
    _normalize_people_term("박사"),
    _normalize_people_term("PI"),
    _normalize_people_term("책임자"),
    _normalize_people_term("연구책임자"),
    _normalize_people_term("참여자"),
    _normalize_people_term("연구진"),
    _normalize_people_term("인물"),
}


ORG_CUES = [
    "기관", "소속기관", "소속 기관", "주관기관", "주관 기관", "수행기관", "수행 기관",
    "참여기관", "참여 기관", "기관정보", "기관 정보", "산학협력단", "소속",
    "사업자등록번호", "기관코드",
]
ORG_ROLE_AFFILIATION_CUES = ["소속", "소속기관", "소속 기관"]
ORG_ROLE_PERFORMER_CUES = ["주관", "주관기관", "주관 기관", "수행", "수행기관", "수행 기관"]
ORG_ROLE_PARTICIPANT_CUES = ["참여", "참여기관", "참여 기관"]

ORG_ROLE_AFFILIATION_CUES = [
    "소속", "소속기관", "소속 기관", "재직", "근무",
]
ORG_ROLE_PARTICIPANT_CUES = [
    "참여", "참여기관", "참여 기관", "참여연구기관", "참여 연구기관", "공동기관", "협력기관",
    "공동", "협력", "컨소시엄",
]
ORG_ROLE_PERFORMER_CUES = [
    "수행기관", "수행 기관", "주관기관", "주관 기관", "과제수행기관", "과제 수행기관",
    "주관", "수행", "대표", "전담", "총괄",
]

REL_PEOPLE_CUES = PEOPLE_CUES[:]  # join relation용
REL_ORG_CUES = ORG_CUES[:]
PERF_TO_PROJECT_CUES = ["어느 과제", "어떤 과제", "관련 과제", "소속 과제", "과제 정보", "과제번호", "pjt_id", "pjt id", "project id"]

ID_QUERY_CUES = [
    "pjt_id",
    "pjt id",
    "pjt_no",
    "pjt no",
    "project id",
    "project no",
    "과제 고유번호",
    "과제번호",
    "과제 번호",
]

FILTER_CUES = ["목록", "리스트", "현황", "통계", "건수", "몇건", "기간", "시작", "종료", "연도", "년도", "기관", "주관", "참여", "상태", "단계", "추출", "다운로드", "엑셀"]
TOPIC_CUES = ["주제", "관련", "분야", "키워드", "동향", "트렌드", "이슈", "기술", "연구", "r&d", "rd", "사례", "핵심", "정리", "요약", "분석"]

COUNT_CUES = ["건수", "몇건", "통계", "count", "총 몇", "총몇", "몇 개", "몇개"]
DETAIL_CUES = ["상세", "세부", "자세히", "정보", "내용", "설명", "프로필"]
LIST_CUES = ["목록", "리스트", "현황", "조회", "보여", "찾아줘", "이력", "내역"]
SUPERLATIVE_CUES = ["가장", "최다", "top", "상위", "1위", "best", "most"]

_REQUEST_LIMIT_RE = re.compile(r"(\d{1,2})\s*(개년|개|건|명)")
_REQUEST_LIMIT_POSITIVE_CUES = ["목록", "리스트", "보여", "보여줘", "조회", "상위", "대표", "과제", "성과", "최대"]
_REQUEST_LIMIT_NEGATIVE_CUES = ["개년", "단계", "분류", "유형"]


def _extract_requested_limit(q: str) -> Optional[int]:
    text = (q or "").strip().lower()
    if not text:
        return None

    if not any(cue in text for cue in _REQUEST_LIMIT_POSITIVE_CUES):
        return None

    for match in _REQUEST_LIMIT_RE.finditer(text):
        unit = (match.group(2) or "").strip()
        if unit == "개년":
            continue

        context_start = max(0, match.start() - 8)
        context_end = min(len(text), match.end() + 8)
        context = text[context_start:context_end]
        if any(cue in context for cue in _REQUEST_LIMIT_NEGATIVE_CUES):
            continue

        limit = _coerce_int(match.group(1), INTENT_MAX_LIMIT)
        limit = max(1, min(limit, INTENT_MAX_LIMIT))
        return limit

    return None


def _has_superlative_cue(text: str) -> bool:
    tl = (text or "").lower()
    return any(cue in tl for cue in SUPERLATIVE_CUES)


def pick_perf_tag_filters(q: str) -> List[str]:
    """질의에서 성과 유형(논문/특허/...)을 감지해 tag 필터 리스트를 만든다."""
    t = (q or "").lower()
    tags: List[str] = []

    def _add(tag: str):
        if tag and (tag in PERF_TAGS) and (tag not in tags):
            tags.append(tag)

    if "논문" in t or "paper" in t:
        _add(TAG_RI_PAPER)
    if "특허" in t or "patent" in t:
        _add(TAG_RI_IPR)
    if ("보고서" in t) or ("연구보고서" in t) or ("report" in t) or ("rpt" in t):
        _add(TAG_RI_RSCH_RPT)
    if ("장비" in t) or ("시설" in t) or ("equip" in t) or ("equipment" in t):
        _add(TAG_RI_FCLT_EQUIP)
    if ("기술요약" in t) or ("기술" in t and "요약" in t) or ("tech" in t):
        _add(TAG_RI_TECH_INFO)
    if ("소프트웨어" in t) or ("software" in t) or (" sw" in t) or (t.strip() == "sw"):
        _add(TAG_RI_SW)
    if ("신품종" in t) or ("nvr" in t):
        _add(TAG_RI_NVR)
    if ("화합물" in t) or ("compound" in t):
        _add(TAG_RI_COMPOUND)
    if ("생명정보" in t):
        _add(TAG_RI_ORGSM_INFO)
    if ("생물자원" in t) or ("resource" in t):
        _add(TAG_RI_ORGSM_RES)

    return tags


def pick_project_tag_filters(q: str) -> List[str]:
    """질의에서 project 하위 엔티티(과제/참여인력/참여기관) 감지."""
    t = (q or "").lower()
    tags: List[str] = []

    def _add(tag: str):
        if tag and (tag in PROJECT_TAGS) and (tag not in tags):
            tags.append(tag)

    project_like = any(
        x in t
        for x in [
            "과제정보",
            "연구목표",
            "연구내용",
            "연구기간",
            "연구비",
            "과제명",
            "영문과제명",
            "국문과제명",
            "참여인력",
            "참여 인력",
            "참여연구원",
            "참여 연구원",
            "연구자",
            "연구원",
            "연구책임자",
            "책임자",
            "참여기관",
            "참여 기관",
            "주관기관",
            "주관 기관",
            "수행기관",
            "수행 기관",
            "기관정보",
            "기관 정보",
            "기관코드",
            "사업자등록번호",
        ]
    )
    if project_like:
        _add(TAG_PJT_INFO)

    return tags


def _hit_count(t: str, cues: List[str]) -> int:
    tl = (t or "").lower()
    return sum(1 for c in cues if c and c.lower() in tl)


def extract_years(q: str) -> List[str]:
    years = []
    for m in _YEAR_RE.finditer(q or ""):
        y = m.group(1)
        if y and y not in years:
            years.append(y)
        if len(years) >= 4:
            break
    return years

def extract_gender_terms(q: str, kws: List[str]) -> List[str]:
    text = " ".join([q or ""] + list(kws or []))
    t = text.lower()
    if not t.strip():
        return []

    def _has_standalone(pattern: str) -> bool:
        return re.search(pattern, t) is not None

    male = any(x in t for x in ["남자", "남성", "male"]) or _has_standalone(
        r"(?<![가-힣A-Za-z0-9])남(?![가-힣A-Za-z0-9])"
    ) or _has_standalone(r"(?<![a-z0-9])m(?![a-z0-9])")
    female = any(x in t for x in ["여자", "여성", "female"]) or _has_standalone(
        r"(?<![가-힣A-Za-z0-9])여(?![가-힣A-Za-z0-9])"
    ) or _has_standalone(r"(?<![a-z0-9])f(?![a-z0-9])")

    out: List[str] = []
    if male:
        out.extend(["남", "남자", "남성", "M", "male"])
    if female:
        out.extend(["여", "여자", "여성", "F", "female"])

    seen: set[str] = set()
    deduped: List[str] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            deduped.append(v)
    return deduped


def extract_org_terms(q: str, kws: List[str], *, max_terms: int = 3) -> List[str]:
    """질의/키워드에서 기관명 N-gram 후보를 추출하고 heuristic으로 정제한다."""
    text = " ".join([q or ""] + list(kws or []))
    if not text.strip() or max_terms <= 0:
        return []

    cleaned = re.sub(r"[\[\]{}<>\"'`~!?@#$%^&*_+=|\\/:;,]", " ", text)
    chunks: List[str] = []
    chunks.extend((q or "").splitlines())
    chunks.extend(re.split(r"[\n\r\t]+", cleaned))
    chunks.extend(kws or [])

    terms: List[str] = []
    seen_norm: set[str] = set()

    def _append_term(raw_term: str):
        term = _normalize_org_term(raw_term)
        if not term:
            return

        term = re.sub(r"^(?:기관명|기관|소속|주관|수행|참여)\s*[:：]?\s*", "", term)
        term = term.strip(" .,-")
        if len(term) < 2:
            return

        if _BIZ_NO_RE.fullmatch(term) or _ORG_CODE_RE.fullmatch(term):
            return

        norm = term.lower()
        if norm in _ORG_TERM_STOPWORDS:
            return
        if any(sw in norm for sw in ("과제", "성과", "목록", "조회", "정보")) and not any(
                sf.lower() in norm for sf in _ORG_SUFFIXES
        ):
            return
        if not (
                _ORG_ACRONYM_RE.fullmatch(term)
                or any(sf in term for sf in _ORG_SUFFIXES)
                or re.search(r"[가-힣]{2,}(?:대학|대학교|연구원|연구소|센터|공단|청)", term)
        ):
            return

        if norm in seen_norm:
            return
        seen_norm.add(norm)
        terms.append(term)

    for m in _ORG_NEAR_LABEL_RE.finditer(text):
        _append_term(m.group(1))
    for m in _ORG_SUFFIX_RE.finditer(text):
        _append_term(m.group(1))

    for chunk in chunks:
        toks = [t for t in re.split(r"\s+", chunk.strip()) if t]
        if not toks:
            continue
        n = len(toks)
        for size in (3, 2, 1):
            if n < size:
                continue
            for i in range(n - size + 1):
                cand = " ".join(toks[i : i + size])
                _append_term(cand)
                if len(terms) >= max_terms:
                    return terms[:max_terms]

    return terms[:max_terms]

def extract_org_role(q: str) -> Optional[str]:
    t = (q or "").strip().lower()
    if not t:
        return None

    if any(x in t for x in ("소속기관", "소속 기관")) and any(x in t for x in ("과제", "project", "pjt")):
        if _hit_count(t, ORG_ROLE_PARTICIPANT_CUES) > 0:
            return "participant"
        if _hit_count(t, ORG_ROLE_PERFORMER_CUES) > 0:
            return "performer"
        return "performer"

    role_scores = {
        "affiliation": _hit_count(t, ORG_ROLE_AFFILIATION_CUES),
        "performer": _hit_count(t, ORG_ROLE_PERFORMER_CUES),
        "participant": _hit_count(t, ORG_ROLE_PARTICIPANT_CUES),
    }
    ranked = sorted(role_scores.items(), key=lambda x: x[1], reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return None
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def extract_id_candidates(q: str, kws: List[str]) -> Dict[str, List[str]]:
    """타입별 ID 후보 추출."""
    qtext = (q or "")
    out: Dict[str, List[str]] = {
        "pjt_id": [],
        "pjt_no": [],
        "rst_id": [],
        "doi": [],
        "issn": [],
        "patent_reg_no": [],
        "biz_no": [],
        "org_code": [],
        "person_no": [],  # 국가연구자번호/인물ID(숫자형) 등
    }

    def _add(key: str, val: str):
        if not val:
            return
        if val not in out[key]:
            out[key].append(val)

    for m in _RST_ID_RE.finditer(qtext):
        _add("rst_id", m.group(0))

    for m in _DOI_RE.finditer(qtext):
        _add("doi", m.group(0))

    for m in _ISSN_RE.finditer(qtext):
        _add("issn", m.group(0))

    for m in _PATENT_REG_NO_RE.finditer(qtext):
        _add("patent_reg_no", m.group(0))

    for m in _BIZ_NO_RE.finditer(qtext):
        _add("biz_no", m.group(0))

    for m in _ORG_CODE_RE.finditer(qtext):
        # 라벨 없이 잡으면 오탐 가능해서, 기관코드/기관구분 주변에서만 강하게 쓰는게 이상적.
        # 여기선 일단 후보로만 넣고, downstream에서 org cue 있을 때만 score를 더 준다.
        _add("org_code", m.group(0))

    # PJT_ID numeric
    toks = list(kws) if kws else re.split(r"\s+", qtext)
    for t in toks:
        tt = (t or "").strip()
        if tt.isdigit() and (8 <= len(tt) <= 12):
            _add("pjt_id", tt)

    # PJT_NO label-based
    for m in _PJT_NO_LABEL_RE.finditer(qtext):
        _add("pjt_no", m.group(1))

    # PJT_NO token-based (PJT 접두 + 하이픈/슬래시 포함)
    for m in _PJT_NO_TOKEN_RE.finditer(qtext):
        _add("pjt_no", m.group(0))

    # 사람 번호(숫자 8~10자리) - 라벨이 있을 때만
    if re.search(r"(국가연구자번호|인물ID|참여인력일련번호|과학기술인등록번호)\s*[:：]?\s*(\d{8,10})", qtext):
        m = re.search(r"(국가연구자번호|인물ID|참여인력일련번호|과학기술인등록번호)\s*[:：]?\s*(\d{8,10})", qtext)
        if m:
            _add("person_no", m.group(2))

    return out


def flatten_ids(ids_map: Dict[str, List[str]]) -> List[str]:
    out: List[str] = []
    for _, vs in (ids_map or {}).items():
        for v in (vs or []):
            s = str(v).strip()
            if s and s not in out:
                out.append(s)
    return out


def _choose_project_key_type(q: str, ids_map: Dict[str, List[str]], *, prefer: Optional[str] = None) -> Optional[str]:
    """후보 추출 결과에서 서버단 단일 프로젝트 키 타입을 확정한다."""
    has_pjt_id = bool((ids_map or {}).get("pjt_id"))
    has_pjt_no = bool((ids_map or {}).get("pjt_no"))
    if not (has_pjt_id or has_pjt_no):
        return None

    prefer_norm = str(prefer or "").strip().lower()
    if prefer_norm in ("pjt_id", "pjt_no"):
        return prefer_norm

    if has_pjt_id and not has_pjt_no:
        return "pjt_id"
    if has_pjt_no and not has_pjt_id:
        return "pjt_no"

    tl = (q or "").lower()
    pjt_no_cues = ("pjt_no", "pjt no", "project no", "과제번호", "과제 번호")
    if any(c in tl for c in pjt_no_cues):
        return "pjt_no"
    return "pjt_id"


def normalize_ids_map_for_strategy(
        q: str,
        ids_map: Dict[str, List[str]],
        *,
        prefer_project_key: Optional[str] = None,
) -> tuple[Dict[str, List[str]], Optional[str]]:
    """extract 단계 후보 ids_map을 normalize/plan 단계용으로 정규화한다."""
    normalized = {k: _normalize_str_list(v) for k, v in (ids_map or {}).items()}
    project_key_type = _choose_project_key_type(q, normalized, prefer=prefer_project_key)

    if project_key_type == "pjt_id":
        normalized["pjt_no"] = []
    elif project_key_type == "pjt_no":
        normalized["pjt_id"] = []

    return normalized, project_key_type


def is_support_query(q: str, *, has_project: bool, has_perf: bool, has_people: bool, has_org: bool) -> bool:
    t = (q or "").strip().lower()
    if not t:
        return False

    if ("qna" in t) or ("manual" in t) or ("매뉴얼" in t) or ("메뉴얼" in t):
        return True

    strong = _hit_count(t, SUPPORT_STRONG_CUES)
    weak = _hit_count(t, SUPPORT_WEAK_CUES)
    if strong == 0 and weak == 0:
        return False

    trouble = any(x in t for x in ["오류", "에러", "실패", "안돼", "안됩니다", "접속", "접속불가", "권한", "차단"])
    if strong > 0 and trouble:
        return True

    # 다른 도메인 시그널이 거의 없으면 support로
    if not (has_project or has_perf or has_people or has_org):
        return True

    account_like = any(x in t for x in ["회원가입", "로그인", "비밀번호", "아이디", "인증", "권한", "승인"])
    return (strong > 0 and account_like)


def _has_any_cue(q: str, cues: List[str]) -> bool:
    tl = (q or "").lower()
    return any(c.lower() in tl for c in cues if c)


def pick_base_route(q: str, kws: List[str], ids_map: Dict[str, List[str]], *, domain_hint: Optional[str] = None,
                    people_terms: Optional[List[str]] = None, org_terms: Optional[List[str]] = None) -> str:
    """
    base_route: support/project/perf/people/org
    우선순위:
      1) domain_hint(신뢰 가능한 경우)
      2) support(trouble/account-like)
      3) people/org (사람/기관 단독이거나 과제 탐색의 주어일 때)
      4) perf / project (사람/기관이 필터이고 목적 엔티티가 성과면 base_route=perf)
      5) default support
    """
    if domain_hint in ("support", "project", "perf", "people", "org"):
        return domain_hint

    t = (q or "").strip()
    tl = t.lower()

    people_terms = people_terms or []
    org_terms = org_terms or []

    has_pjt_id = bool((ids_map or {}).get("pjt_id") or (ids_map or {}).get("pjt_no"))
    has_perf_id = bool((ids_map or {}).get("doi") or (ids_map or {}).get("issn") or (ids_map or {}).get("rst_id") or (ids_map or {}).get("patent_reg_no"))
    has_people_id = bool((ids_map or {}).get("person_no"))

    has_org = bool(org_terms) or _has_any_cue(tl, ORG_CUES)
    has_people = bool(people_terms) or has_people_id

    has_project = has_pjt_id or _has_any_cue(tl, PROJECT_CUES)
    has_perf = has_perf_id or _has_any_cue(tl, PERF_CUES)

    if is_support_query(tl, has_project=has_project, has_perf=has_perf, has_people=has_people, has_org=has_org):
        return "support"

    if has_project and any(x in tl for x in ("소속기관", "소속 기관")):
        return "project"

    # project 의도가 명확하면 people/org 신호가 있어도 project 우선
    if has_project and (has_people or has_org):
        return "project"

    # 사람/기관 단독 의도는 head로 유지
    if has_people and (has_project or "과제" in tl or "pjt" in tl or "참여" in tl):
        return "people"
    if has_org and (has_project or "과제" in tl or "pjt" in tl or "참여" in tl):
        return "org"

    if has_people and not (has_project or has_perf):
        return "people"
    if has_org and not (has_project or has_perf):
        return "org"

    # 사람/기관 + 성과(논문/특허...) 요청은 성과 엔티티가 목적이므로 perf head를 사용
    if has_people and has_perf and not has_project:
        return "perf"
    if has_org and has_perf and not has_project:
        return "perf"

    if has_perf and not has_project:
        return "perf"
    if has_project:
        return "project"
    return "support"


def pick_relation(q: str, base_route: str, *, has_project: bool, has_perf: bool, has_people: bool, has_org: bool) -> Optional[Tuple[str, str]]:
    """
    relation 감지:
    - project -> perf : "~ 과제의 성과"
    - perf -> project : "이 성과가 어느 과제?"

    현재 people/org 관련 질의는 JOIN relation 대신
    prtcp_mp / prtcp_org 단일 컬렉션 필터로 처리한다.
    """
    t = (q or "").strip().lower()
    if not t:
        return None

    wants_perf_to_project = has_perf and (has_project or any(c in t for c in PERF_TO_PROJECT_CUES))

    if base_route in ("people", "org"):
        # 사람/기관 → 과제/성과는 relation JOIN 대신 단일 컬렉션 필터를 우선
        return None

    if base_route == "project":
        if has_perf:
            return ("project", "perf")
        # 사람/기관은 prtcp_mp/prtcp_org로 단일 컬렉션 처리 (JOIN 비활성화)
        return None

    if base_route == "perf":
        if wants_perf_to_project:
            return ("perf", "project")
        return None

    return None


def pick_structured_intent(base_route: str, q: str, is_id_query: bool) -> str:
    t = (q or "").strip().lower()
    if not t:
        return "content"
    if base_route == "support":
        return "support"
    if is_id_query:
        return "id"
    has_year = bool(_YEAR_RE.search(t))
    if has_year or any(c in t for c in FILTER_CUES):
        return "filter"
    if any(c in t for c in TOPIC_CUES) or ("관련" in t and ("과제" in t or "project" in t or "pjt" in t)):
        return "topic"
    return "content"


def pick_org_role(q: str) -> Optional[str]:
    t = (q or "").lower()
    if not t.strip():
        return None

    if any(x in t for x in ("소속기관", "소속 기관")) and any(x in t for x in ("과제", "project", "pjt")):
        if any(c in t for c in ORG_ROLE_PARTICIPANT_CUES):
            return "participant"
        if any(c in t for c in ORG_ROLE_PERFORMER_CUES):
            return "performer"
        return "performer"

    if any(c in t for c in ORG_ROLE_AFFILIATION_CUES):
        return "affiliation"
    if any(c in t for c in ORG_ROLE_PARTICIPANT_CUES):
        return "participant"
    if any(c in t for c in ORG_ROLE_PERFORMER_CUES):
        return "performer"
    return None


def _extract_people_terms_for_affiliation(q: str) -> List[str]:
    text = (q or "")
    out: List[str] = []
    noisy_tokens = ("소속", "연구원", "연구자", "기관", "대학", "연구소")
    for pattern in (_NAME_LABEL_RE, _NAME_NEAR_CUE_RE):
        for match in pattern.finditer(text):
            name = (match.group(1) or "").strip()
            if not name:
                continue
            if any(token in name for token in noisy_tokens):
                continue
            if name not in out:
                out.append(name)
    return out


def _apply_affiliation_intent(
        q: str,
        *,
        org_role: Optional[str],
        people_terms: List[str],
        org_terms: List[str],
        ids_map: Dict[str, List[str]],
        relation: Optional[Tuple[str, str]],
) -> Tuple[List[str], List[str], Optional[Tuple[str, str]]]:
    if org_role != "affiliation":
        return people_terms, org_terms, relation

    tl = (q or "").lower()
    has_name_signal = bool(_NAME_NEAR_CUE_RE.search(q or "")) or bool(_NAME_LABEL_RE.search(q or ""))
    has_people_signal = (
            bool(people_terms)
            or bool(ids_map.get("person_no"))
            or has_name_signal
            or _has_any_cue(tl, PEOPLE_CUES)
    )
    if not has_people_signal:
        return people_terms, org_terms, relation

    if not people_terms and not bool(ids_map.get("person_no")) and not has_name_signal:
        return people_terms, org_terms, relation

    if not people_terms:
        people_terms = _extract_people_terms_for_affiliation(q)

    return people_terms, org_terms, None


def _strip_non_join_relation(relation: Optional[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    if not relation:
        return None
    if any(part in ("people", "org") for part in relation):
        return None
    return relation


def _normalize_base_route_for_people_org_project_perf(
        *,
        base_route: str,
        has_people: bool,
        has_org: bool,
        has_project: bool,
        has_perf: bool,
        wants_list: bool,
) -> str:
    """
    사람/기관 + 과제 + 성과 신호가 동시에 있으면 head를 project/perf로 정규화한다.
    - 성과 목록 요청이면 perf
    - 그 외에는 project
    """
    if base_route not in ("people", "org"):
        return base_route
    if not ((has_people or has_org) and has_project and has_perf):
        return base_route
    if wants_list:
        return "perf"
    return "project"


def _resolve_action(
        *,
        base_route: str,
        relation: Optional[Tuple[str, str]],
        wants_count: bool,
        wants_list: bool,
        wants_detail: bool,
        wants_rank: bool,
        intent: str,
        ids_map: Dict[str, List[str]],
        is_id_query: bool,
) -> str:
    if base_route == "support":
        return "support"
    if base_route in ("people", "org") and wants_rank:
        return "stats"
    if relation is not None:
        if wants_count and not wants_list:
            return "stats"
        if wants_list:
            return "list"
        if wants_detail:
            return "detail"
        return "relation"

    exact_id = bool(
        ids_map.get("pjt_id")
        or ids_map.get("pjt_no")
        or ids_map.get("rst_id")
        or ids_map.get("doi")
        or ids_map.get("issn")
        or ids_map.get("patent_reg_no")
        or ids_map.get("biz_no")
    )
    if exact_id:
        return "id_exact"
    if is_id_query:
        return "id_fuzzy"
    if wants_count:
        return "stats"
    if wants_list or intent == "filter":
        return "list"
    if intent == "topic":
        return "topic"
    if wants_detail:
        return "detail"
    return "content"


@dataclass
class QueryIntent:
    # core
    base_route: str                               # support/project/perf/people/org
    relation: Optional[Tuple[str, str]]
    intent: str                                   # support/id/filter/topic/content
    action: str                                   # support/id_exact/id_fuzzy/list/stats/topic/detail/content/relation
    # profile flags
    is_id_query: bool
    long_query: bool
    rare_ratio: float
    # extracted entities
    people_terms: List[str] = field(default_factory=list)
    gender_terms: List[str] = field(default_factory=list)
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
    ids_map: Dict[str, List[str]] = field(default_factory=dict)
    ids_flat: List[str] = field(default_factory=list)
    # tag filters
    project_tag_filters: List[str] = field(default_factory=list)
    perf_tag_filters: List[str] = field(default_factory=list)
    tag_filters: List[str] = field(default_factory=list)
    # user ask signals
    wants_count: bool = False
    wants_list: bool = False
    wants_detail: bool = False
    wants_rank: bool = False
    output_type: Optional[str] = None
    join_key_mode: Optional[Literal["instance", "group"]] = None
    parsing_warnings: List[str] = field(default_factory=list)
    contract_violations: List[str] = field(default_factory=list)
    # planner meta
    categories: List[str] = field(default_factory=list)
    planner_limit: Optional[int] = None
    retrieval_query: Optional[str] = None
    planner_confidence: Optional[float] = None
    people_terms_match_mode: Optional[str] = None
    people_terms_min_should: Optional[int] = None
    lookup_filter_policy: Optional[str] = None

    def debug_dict(self) -> Dict[str, object]:
        return {
            "base_route": self.base_route,
            "relation": self.relation,
            "join_key_mode": self.join_key_mode,
            "parsing_warnings": self.parsing_warnings,
            "contract_violations": self.contract_violations,
            "intent": self.intent,
            "action": self.action,
            "is_id": int(self.is_id_query),
            "long": int(self.long_query),
            "rare_ratio": round(float(self.rare_ratio), 4),
            "people_terms": self.people_terms,
            "gender_terms": self.gender_terms,
            "org_terms": self.org_terms,
            "org_role": self.org_role,
            "lead_org_terms": self.lead_org_terms,
            "participant_org_terms": self.participant_org_terms,
            "people_affiliation_org_terms": self.people_affiliation_org_terms,
            "years": self.years,
            "year_from": self.year_from,
            "year_to": self.year_to,
            "perf_types": self.perf_types,
            "title": self.title,
            "ids_map": self.ids_map,
            "ids_flat": self.ids_flat,
            "project_tag_filters": self.project_tag_filters,
            "perf_tag_filters": self.perf_tag_filters,
            "tag_filters": self.tag_filters,
            "wants_count": self.wants_count,
            "wants_list": self.wants_list,
            "wants_detail": self.wants_detail,
            "wants_rank": self.wants_rank,
            "output_type": self.output_type,
            "categories": self.categories,
            "planner_limit": self.planner_limit,
            "retrieval_query": self.retrieval_query,
            "planner_confidence": self.planner_confidence,
            "people_terms_match_mode": self.people_terms_match_mode,
            "people_terms_min_should": self.people_terms_min_should,
            "lookup_filter_policy": self.lookup_filter_policy,
        }


def normalize_join_key_mode(
        join_key_mode: Optional[str],
        ids_map: Optional[Dict[str, List[str]]],
) -> tuple[Optional[Literal["instance", "group"]], list[str], list[str]]:
    """JOIN key mode와 ids_map 정합성을 정규화한다."""
    ids_map = ids_map if isinstance(ids_map, dict) else {}
    mode = str(join_key_mode or "").strip().lower() or None
    has_pjt_id = bool(ids_map.get("pjt_id"))
    has_pjt_no = bool(ids_map.get("pjt_no"))

    parsing_warnings: list[str] = []
    contract_violations: list[str] = []

    if mode == "group" and not has_pjt_no:
        mode = "instance"
        parsing_warnings.append("JOIN_KEY_MODE_GROUP_WITHOUT_PJT_NO_COERCED_TO_INSTANCE")

    if mode == "instance" and has_pjt_no and not has_pjt_id:
        contract_violations.append("JOIN_KEY_MODE_INSTANCE_WITH_PJT_NO_ONLY")

    if mode in ("instance", "group"):
        return mode, parsing_warnings, contract_violations
    return None, parsing_warnings, contract_violations

def normalize_categories(cat) -> list[str]:
    if cat is None:
        return []

    canonical_map = {
        "project": "project",
        "과제": "project",
        "performance": "perf",
        "perf": "perf",
        "성과": "perf",
        "researcher": "people",
        "people": "people",
        "연구자": "people",
        "organization": "org",
        "org": "org",
        "institution": "org",
        "기관": "org",
        "support": "support",
        "문의": "support",
        "지원": "support",
        "qna": "qna",
        "q&a": "qna",
        "etc": "etc",
    }

    xs = cat if isinstance(cat, (list, tuple, set)) else [cat]
    out = []
    seen = set()
    for x in xs:
        if hasattr(x, "value"):  # Enum
            s = str(x.value)
        else:
            s = str(x)
        s = s.strip().lower()
        if not s:
            continue
        norm = canonical_map.get(s, s)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out

def pick_domain_hint_from_categories(cats: list[str]) -> str | None:
    if "people" in cats:
        return "people"
    if "org" in cats:
        return "org"
    if "project" in cats:
        return "project"
    if "perf" in cats:
        return "perf"
    if "support" in cats:
        return "support"
    return None


def _fallback_categories_for_route(base_route: str) -> List[str]:
    if base_route == "project":
        return ["project"]
    if base_route == "perf":
        return ["perf"]
    if base_route == "people":
        return ["people"]
    if base_route == "org":
        return ["org"]
    if base_route == "support":
        return ["qna"]
    return ["etc"]


def _cheap_precheck(q: str) -> Optional[str]:
    t = (q or "").strip().lower()
    if not t:
        return "empty"
    if any(c in t for c in CHEAP_GREETING_CUES):
        return "greeting"
    if len(t) <= 2 or (len(t) <= 5 and len(t.split()) <= 1):
        return "too_short"
    if any(c in t for c in CHEAP_SYSTEM_CUES):
        return "system"
    return None


def _normalize_str_list(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    out: List[str] = []
    seen: set[str] = set()
    for v in values:
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _parse_relation(value: Any) -> Optional[Tuple[str, str]]:
    if not value:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (str(value[0]).strip().lower(), str(value[1]).strip().lower())
    text = str(value).strip().lower()
    if not text:
        return None
    if "_" in text:
        parts = [p.strip() for p in text.split("_") if p.strip()]
        if len(parts) == 2:
            return (parts[0], parts[1])
    if ">" in text:
        parts = [p.strip() for p in text.split(">") if p.strip()]
        if len(parts) == 2:
            return (parts[0], parts[1])
    return None


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_tag_filters(values: Any, allowed: set[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for v in _normalize_str_list(values):
        if v in allowed and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _plan_from_hint(hint: Any) -> Dict[str, Any]:
    if hint is None:
        return {}
    def _get_attr(obj: Any, name: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    plan: Dict[str, Any] = {
        "category": _get_attr(hint, "category") or _get_attr(hint, "categories"),
        "base_route": _get_attr(hint, "head"),
        "relation": _get_attr(hint, "relation"),
        "intent": _get_attr(hint, "intent"),
        "action": _get_attr(hint, "action"),
        "people_terms": _get_attr(hint, "people_terms"),
        "org_terms": _get_attr(hint, "organizations") or _get_attr(hint, "org_terms"),
        "org_role": _get_attr(hint, "org_role"),
        "lead_org_terms": _get_attr(hint, "lead_org_name") or _get_attr(hint, "lead_org_terms"),
        "participant_org_terms": _get_attr(hint, "participant_org_name") or _get_attr(hint, "participant_org_terms"),
        "people_affiliation_org_terms": _get_attr(hint, "people_affiliation_org_name") or _get_attr(hint, "people_affiliation_org_terms"),
        "years": _get_attr(hint, "years"),
        "year_from": _get_attr(hint, "year_from"),
        "year_to": _get_attr(hint, "year_to"),
        "perf_types": _get_attr(hint, "perf_types"),
        "title": _get_attr(hint, "title") or _get_attr(hint, "title_terms"),
        "project_tag_filters": _get_attr(hint, "project_tag_filters"),
        "perf_tag_filters": _get_attr(hint, "perf_tag_filters"),
        "wants_count": _get_attr(hint, "wants_count"),
        "wants_list": _get_attr(hint, "wants_list"),
        "wants_detail": _get_attr(hint, "wants_detail"),
        "wants_rank": _get_attr(hint, "wants_rank"),
        "output_type": _get_attr(hint, "output_type"),
        "limit": _get_attr(hint, "limit"),
        "retrieval_query": _get_attr(hint, "retrieval_query"),
        "confidence": _get_attr(hint, "confidence"),
    }
    return plan


@dataclass(frozen=True)
class RelationRoute:
    relation: Tuple[str, str]
    hop1_col: str
    hop2_col: str
    hop1_kind: str
    hop2_kind: str
    hop1_tag_filters: Optional[List[str]]
    hop2_tag_filters: Optional[List[str]]
    hop2_label: str

    def target_collections(self) -> List[str]:
        return list(dict.fromkeys([self.hop1_col, self.hop2_col]))


RELATION_ROUTE_TABLES: Dict[Tuple[str, str], RelationRoute] = {
    ("project", "perf"): RelationRoute(
        relation=("project", "perf"),
        hop1_col=COL_PROJECT,
        hop2_col=COL_PERF,
        hop1_kind="project",
        hop2_kind="perf",
        hop1_tag_filters=[TAG_PJT_INFO],
        hop2_tag_filters=None,
        hop2_label="성과(논문/특허/보고서 등) 목록",
    ),
    ("perf", "project"): RelationRoute(
        relation=("perf", "project"),
        hop1_col=COL_PERF,
        hop2_col=COL_PROJECT,
        hop1_kind="perf",
        hop2_kind="project",
        hop1_tag_filters=None,
        hop2_tag_filters=[TAG_PJT_INFO],
        hop2_label="연관 과제(프로젝트) 정보",
    ),
}


def get_relation_route(relation: Optional[Tuple[str, str]]) -> Optional[RelationRoute]:
    if not relation:
        return None
    return RELATION_ROUTE_TABLES.get(tuple(relation))


def relation_target_collections(relation: Optional[Tuple[str, str]]) -> List[str]:
    route = get_relation_route(relation)
    return route.target_collections() if route else []


def _classify_query_heuristic(
        q: str,
        kws: List[str],
        *,
        domain_hint: Optional[str] = None,
        ids_map: Optional[Dict[str, List[str]]] = None,
) -> QueryIntent:
    q = (q or "").strip()
    tl = q.lower()

    if ids_map is None:
        extracted_ids_map = extract_id_candidates(q, kws)
        ids_map, _ = normalize_ids_map_for_strategy(q, extracted_ids_map)
    else:
        ids_map, _ = normalize_ids_map_for_strategy(q, ids_map)

    # S1: rare/id/long
    rare_kws = [kw for kw in (kws or []) if _is_rare_token(kw)]
    rare_ratio = len(rare_kws) / max(1, len(kws or []))

    is_id_query = (
            len(rare_kws) >= 2
            or bool(_RST_ID_RE.search(q))
            or bool(_DOI_RE.search(q))
            or bool(_ISSN_RE.search(q))
            or bool(_PATENT_REG_NO_RE.search(q))
            or bool(_PJT_ID_NUM_RE.search(q))
            or bool(_PJT_NO_LABEL_RE.search(q))
            or _has_any_cue(tl, ID_QUERY_CUES)
            or any(bool(v) for v in (ids_map or {}).values())
    )
    long_query = (len(q.split()) >= 12) or (len(q) >= 40)

    # entities
    people_terms: List[str] = []
    gender_terms = extract_gender_terms(q, kws)
    org_terms = normalize_org_terms(extract_org_terms(q, kws))
    org_role = extract_org_role(q)
    years = extract_years(q)
    lead_org_terms: List[str] = []
    participant_org_terms: List[str] = []
    people_affiliation_org_terms: List[str] = []
    if org_role == "affiliation":
        has_name_signal = bool(_NAME_NEAR_CUE_RE.search(q or "")) or bool(_NAME_LABEL_RE.search(q or ""))
        if has_name_signal:
            people_terms = _extract_people_terms_for_affiliation(q)
        people_affiliation_org_terms = list(org_terms)
    elif org_role in ("lead", "performer", "performing"):
        lead_org_terms = list(org_terms)
    elif org_role == "participant":
        participant_org_terms = list(org_terms)

    has_project = _has_any_cue(tl, PROJECT_CUES) or bool(ids_map.get("pjt_id") or ids_map.get("pjt_no"))
    has_perf = _has_any_cue(tl, PERF_CUES) or bool(ids_map.get("doi") or ids_map.get("issn") or ids_map.get("rst_id") or ids_map.get("patent_reg_no"))
    has_org_cue = _has_any_cue(tl, ORG_CUES)
    has_people_cue = _has_any_cue(tl, PEOPLE_CUES)
    has_org = bool(org_terms) or has_org_cue or bool(ids_map.get("biz_no") or ids_map.get("org_code"))
    has_people = bool(people_terms) or bool(ids_map.get("person_no")) or has_people_cue

    base_route = pick_base_route(
        q, kws, ids_map,
        domain_hint=domain_hint,
        people_terms=people_terms,
        org_terms=org_terms,
    )

    wants_count = any(c in tl for c in COUNT_CUES)
    wants_list = any(c in tl for c in LIST_CUES)
    wants_detail = any(c in tl for c in DETAIL_CUES)
    wants_rank = (base_route in ("people", "org") or has_people or has_org or has_people_cue or has_org_cue) and _has_superlative_cue(tl)

    base_route = _normalize_base_route_for_people_org_project_perf(
        base_route=base_route,
        has_people=has_people,
        has_org=has_org,
        has_project=has_project,
        has_perf=has_perf,
        wants_list=wants_list,
    )

    relation = pick_relation(
        q, base_route,
        has_project=has_project,
        has_perf=has_perf,
        has_people=has_people,
        has_org=has_org,
    )
    people_terms, org_terms, relation = _apply_affiliation_intent(
        q,
        org_role=org_role,
        people_terms=people_terms,
        org_terms=org_terms,
        ids_map=ids_map,
        relation=relation,
    )

    if (has_people or has_org) and has_project and has_perf:
        relation = ("project", "perf")

    intent = pick_structured_intent(base_route, q, is_id_query=is_id_query)

    # tag filters
    project_tag_filters = pick_project_tag_filters(q) if base_route in ("project", "people", "org") else []
    perf_tag_filters = pick_perf_tag_filters(q) if base_route in ("perf", "project") else []

    action = _resolve_action(
        base_route=base_route,
        relation=relation,
        wants_count=wants_count,
        wants_list=wants_list,
        wants_detail=wants_detail,
        wants_rank=wants_rank,
        intent=intent,
        ids_map=ids_map,
        is_id_query=is_id_query,
    )

    ids_flat = flatten_ids(ids_map)

    people_terms_match_mode = "or"
    people_terms_min_should = 1 if len(people_terms) >= 2 else None
    if len(people_terms) >= 2 and re.search(r"\b(and|모두|둘\s*다|동시)\b", tl):
        people_terms_match_mode = "and"
        people_terms_min_should = None

    requested_limit = _extract_requested_limit(q)
    join_key_mode, parsing_warnings, contract_violations = normalize_join_key_mode(
        "group" if ids_map.get("pjt_no") else "instance" if ids_map.get("pjt_id") else None,
        ids_map,
    )

    return QueryIntent(
        base_route=base_route,
        relation=relation,
        intent=intent,
        action=action,
        is_id_query=is_id_query,
        long_query=long_query,
        rare_ratio=float(rare_ratio),
        people_terms=people_terms,
        gender_terms=gender_terms,
        org_terms=org_terms,
        org_role=org_role,
        lead_org_terms=lead_org_terms,
        participant_org_terms=participant_org_terms,
        people_affiliation_org_terms=people_affiliation_org_terms,
        years=years,
        year_from=None,
        year_to=None,
        perf_types=[],
        title=[],
        ids_map=ids_map,
        ids_flat=ids_flat,
        project_tag_filters=project_tag_filters,
        perf_tag_filters=perf_tag_filters,
        tag_filters=list(dict.fromkeys([*project_tag_filters, *perf_tag_filters])),
        wants_count=wants_count,
        wants_list=wants_list,
        wants_detail=wants_detail,
        wants_rank=wants_rank,
        planner_limit=requested_limit,
        join_key_mode=join_key_mode,
        parsing_warnings=parsing_warnings,
        contract_violations=contract_violations,
        people_terms_match_mode=people_terms_match_mode,
        people_terms_min_should=people_terms_min_should,
        lookup_filter_policy="must_one_then_should" if people_terms_match_mode == "or" and len(people_terms) == 1 else None,
    )


def classify_query(
        q: str,
        kws: List[str],
        *,
        domain_hint: Optional[str] = None,
        hint: Optional[Any] = None,
) -> QueryIntent:
    q = (q or "").strip()
    tl = q.lower()

    extracted_ids_map = extract_id_candidates(q, kws)
    ids_map, selected_project_key = normalize_ids_map_for_strategy(q, extracted_ids_map)
    ids_flat = flatten_ids(ids_map)

    precheck = _cheap_precheck(q)
    if precheck:
        precheck_join_key_mode, precheck_parsing_warnings, precheck_contract_violations = normalize_join_key_mode(
            "group" if ids_map.get("pjt_no") else "instance" if ids_map.get("pjt_id") else None,
            ids_map,
        )
        return QueryIntent(
            base_route="support",
            relation=None,
            intent="support",
            action="support",
            is_id_query=bool(ids_flat),
            long_query=(len(q.split()) >= 12) or (len(q) >= 40),
            rare_ratio=0.0,
            ids_map=ids_map,
            ids_flat=ids_flat,
            categories=["qna"],
            planner_confidence=0.0,
            join_key_mode=precheck_join_key_mode,
            parsing_warnings=precheck_parsing_warnings,
            contract_violations=precheck_contract_violations,
        )

    plan = _plan_from_hint(hint)
    if selected_project_key is None and plan:
        prefer_project_key = str(plan.get("project_key_type") or "").strip().lower() or None
        ids_map, selected_project_key = normalize_ids_map_for_strategy(
            q,
            extracted_ids_map,
            prefer_project_key=prefer_project_key,
        )
        ids_flat = flatten_ids(ids_map)

    if not plan:
        return _classify_query_heuristic(q, kws, domain_hint=domain_hint, ids_map=ids_map)

    categories = normalize_categories(plan.get("category") or plan.get("categories"))
    valid_categories = {
        "project",
        "perf",
        "people",
        "qna",
        "etc",
        "org",
        "support",
    }
    categories = [c for c in categories if c in valid_categories]
    confidence = float(plan.get("confidence") or 0.0)

    base_route = str(plan.get("base_route") or plan.get("head") or "").strip().lower()
    if base_route not in ("support", "project", "perf", "people", "org"):
        base_route = pick_domain_hint_from_categories(categories) or (domain_hint or "")
    if base_route not in ("support", "project", "perf", "people", "org"):
        base_route = pick_base_route(q, kws, ids_map, domain_hint=domain_hint)

    if not categories:
        categories = _fallback_categories_for_route(base_route)

    has_project = _has_any_cue(tl, PROJECT_CUES) or bool(ids_map.get("pjt_id") or ids_map.get("pjt_no"))
    has_perf = _has_any_cue(tl, PERF_CUES) or bool(ids_map.get("doi") or ids_map.get("issn") or ids_map.get("rst_id") or ids_map.get("patent_reg_no"))

    relation = _parse_relation(plan.get("relation"))
    if relation not in RELATION_ROUTE_TABLES:
        relation = None
    relation = _strip_non_join_relation(relation)

    intent = str(plan.get("intent") or "").strip().lower()
    if intent not in ("support", "id", "filter", "topic", "content"):
        intent = pick_structured_intent(base_route, q, is_id_query=bool(ids_flat))

    wants_count = bool(plan.get("wants_count", False))
    wants_list = bool(plan.get("wants_list", False))
    wants_detail = bool(plan.get("wants_detail", False))
    wants_rank = bool(plan.get("wants_rank", False))
    if not (wants_count or wants_list or wants_detail):
        wants_count = any(c in tl for c in COUNT_CUES)
        wants_list = any(c in tl for c in LIST_CUES)
        wants_detail = any(c in tl for c in DETAIL_CUES)
    if not wants_rank:
        wants_rank = _has_superlative_cue(tl)
    output_type = str(plan.get("output_type") or "").strip().lower() or None
    if output_type == "rank":
        output_type = "stats"
    if output_type not in ("stats", "list", "detail", "relation", "summary"):
        output_type = None

    action = str(plan.get("action") or "").strip().lower()
    if action == "rank":
        action = "stats"
    if action not in ("support", "id_exact", "id_fuzzy", "list", "stats", "topic", "detail", "content", "relation"):
        action = ""

    people_terms = _normalize_str_list(plan.get("people_terms") or plan.get("researchers"))
    gender_terms = _normalize_str_list(plan.get("gender_terms"))
    plan_filters = plan.get("filters") if isinstance(plan.get("filters"), dict) else {}
    org_terms = normalize_org_terms(_normalize_str_list(plan.get("org_terms") or plan.get("organizations")))
    lead_org_terms = normalize_org_terms(_normalize_str_list(
        plan.get("lead_org_terms")
        or (plan_filters.get("lead_org_name") if isinstance(plan_filters, dict) else None)
    ))
    participant_org_terms = normalize_org_terms(_normalize_str_list(
        plan.get("participant_org_terms")
        or (plan_filters.get("participant_org_name") if isinstance(plan_filters, dict) else None)
    ))
    people_affiliation_org_terms = normalize_org_terms(_normalize_str_list(
        plan.get("people_affiliation_org_terms")
        or (plan_filters.get("people_affiliation_org_name") if isinstance(plan_filters, dict) else None)
    ))
    if not org_terms:
        org_terms = normalize_org_terms([*lead_org_terms, *participant_org_terms, *people_affiliation_org_terms])
    org_role = str(plan.get("org_role") or "").strip().lower() or None
    years = _normalize_str_list(plan.get("years"))
    year_from = str(plan.get("year_from") or "").strip() or None
    year_to = str(plan.get("year_to") or "").strip() or None
    perf_types = _normalize_str_list(plan.get("perf_types"))
    title_terms = _normalize_str_list(plan.get("title") or plan.get("title_terms"))

    if not gender_terms:
        gender_terms = extract_gender_terms(q, kws)
    if not org_role:
        org_role = extract_org_role(q)
    if not years:
        years = extract_years(q)
    if not year_from and years:
        year_from = years[0]
    if not year_to and years:
        year_to = years[-1]
    if org_role in ("lead", "performer", "performing") and not lead_org_terms:
        lead_org_terms = list(org_terms)
    if org_role == "participant" and not participant_org_terms:
        participant_org_terms = list(org_terms)
    if org_role == "affiliation" and not people_affiliation_org_terms:
        people_affiliation_org_terms = list(org_terms)

    has_people_cue = _has_any_cue(tl, PEOPLE_CUES)
    has_org_cue = _has_any_cue(tl, ORG_CUES)
    has_people = bool(people_terms) or bool(ids_map.get("person_no")) or has_people_cue
    has_org = bool(org_terms) or has_org_cue or bool(ids_map.get("biz_no") or ids_map.get("org_code"))
    wants_rank = wants_rank and (base_route in ("people", "org") or has_people or has_org or has_people_cue or has_org_cue)

    if people_terms:
        if any(keyword in tl for keyword in ("과제", "프로젝트", "project")):
            base_route = "project"
        elif "성과" in tl:
            base_route = "perf"

    base_route = _normalize_base_route_for_people_org_project_perf(
        base_route=base_route,
        has_people=has_people,
        has_org=has_org,
        has_project=has_project,
        has_perf=has_perf,
        wants_list=wants_list,
    )

    project_tag_filters = _normalize_tag_filters(plan.get("project_tag_filters"), PROJECT_TAGS)
    perf_tag_filters = _normalize_tag_filters(plan.get("perf_tag_filters"), PERF_TAGS)
    if not project_tag_filters and base_route in ("project", "people", "org"):
        project_tag_filters = pick_project_tag_filters(q)
    if not perf_tag_filters and base_route in ("perf", "project"):
        perf_tag_filters = pick_perf_tag_filters(q)

    plan_limit = plan.get("limit")
    limit: Optional[int] = None
    if plan_limit is not None:
        limit = _coerce_int(plan_limit, INTENT_MAX_LIMIT)
        limit = max(1, min(limit, INTENT_MAX_LIMIT))
    else:
        limit = _extract_requested_limit(q)
    retrieval_query = str(plan.get("retrieval_query") or "").strip()
    if retrieval_query:
        retrieval_query = retrieval_query[:INTENT_MAX_RETRIEVAL_QUERY]

    rare_kws = [kw for kw in (kws or []) if _is_rare_token(kw)]
    rare_ratio = len(rare_kws) / max(1, len(kws or []))

    people_terms, org_terms, relation = _apply_affiliation_intent(
        q,
        org_role=org_role,
        people_terms=people_terms,
        org_terms=org_terms,
        ids_map=ids_map,
        relation=relation,
    )
    relation = _strip_non_join_relation(relation)
    if (has_people or has_org) and has_project and has_perf:
        relation = ("project", "perf")

    if not action or (action == "relation" and relation is None):
        action = _resolve_action(
            base_route=base_route,
            relation=relation,
            wants_count=wants_count,
            wants_list=wants_list,
            wants_detail=wants_detail,
            wants_rank=wants_rank,
            intent=intent,
            ids_map=ids_map,
            is_id_query=bool(ids_flat),
        )

    people_terms_match_mode = "or"
    people_terms_min_should = 1 if len(people_terms) >= 2 else None
    if len(people_terms) >= 2 and re.search(r"\b(and|모두|둘\s*다|동시)\b", tl):
        people_terms_match_mode = "and"
        people_terms_min_should = None

    join_key_mode, parsing_warnings, contract_violations = normalize_join_key_mode(
        "group" if selected_project_key == "pjt_no" else "instance" if selected_project_key == "pjt_id" else None,
        ids_map,
    )

    return QueryIntent(
        base_route=base_route,
        relation=relation,
        intent=intent,
        action=action,
        is_id_query=bool(ids_flat),
        long_query=(len(q.split()) >= 12) or (len(q) >= 40),
        rare_ratio=float(rare_ratio),
        people_terms=people_terms,
        gender_terms=gender_terms,
        org_terms=org_terms,
        org_role=org_role,
        lead_org_terms=lead_org_terms,
        participant_org_terms=participant_org_terms,
        people_affiliation_org_terms=people_affiliation_org_terms,
        years=years,
        year_from=None,
        year_to=None,
        perf_types=[],
        title=[],
        ids_map=ids_map,
        ids_flat=ids_flat,
        project_tag_filters=project_tag_filters,
        perf_tag_filters=perf_tag_filters,
        tag_filters=list(dict.fromkeys([*project_tag_filters, *perf_tag_filters])),
        wants_count=wants_count,
        wants_list=wants_list,
        wants_detail=wants_detail,
        wants_rank=wants_rank,
        output_type=output_type,
        categories=categories,
        planner_limit=limit,
        retrieval_query=retrieval_query or None,
        planner_confidence=confidence if plan else None,
        people_terms_match_mode=people_terms_match_mode,
        people_terms_min_should=people_terms_min_should,
        lookup_filter_policy="must_one_then_should" if people_terms_match_mode == "or" and len(people_terms) == 1 else None,
        join_key_mode=join_key_mode,
        parsing_warnings=parsing_warnings,
        contract_violations=contract_violations,
    )
