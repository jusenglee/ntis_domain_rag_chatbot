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
from typing import Dict, List, Optional, Tuple

from .constants import (
    RARE_TOKEN_RE,
    TAG_PJT_INFO,
    TAG_PJT_MP,
    TAG_PJT_ORG,
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

# 사람 이름 후보: 한글 2~4자 (단독으로는 오탐이 많아서 '연구자/연구원/참여인력' 등 주변 신호와 결합)
_NAME_NEAR_CUE_RE = re.compile(r"([가-힣]{2,4})\s*(?:연구자|연구원|교수|박사|PI|책임자|연구책임자|참여연구원|참여인력)")
_NAME_LABEL_RE = re.compile(r"(?:인물명|연구자명|성명|이름)\s*[:：]\s*([가-힣]{2,4})")

# 기관명 후보 (suffix 기반 + 라벨 기반)
_ORG_SUFFIXES = [
    "대학교", "대학", "산학협력단", "연구원", "연구소", "센터", "재단", "병원",
    "공사", "공단", "협회", "청", "부", "처", "원",
    "주식회사", "㈜", "회사", "Corp", "Inc", "Ltd", "LLC",
]
_ORG_NEAR_LABEL_RE = re.compile(r"(?:기관|소속|주관|수행|참여)\s*(?:기관명)?\s*[:：]\s*([가-힣A-Za-z0-9㈜().·\-\s]{2,40})")
# suffix로 끝나는 덩어리(공백 포함 허용)
_ORG_SUFFIX_RE = re.compile(
    r"([가-힣A-Za-z0-9㈜().·\-\s]{2,40}(?:"
    + "|".join(map(re.escape, _ORG_SUFFIXES))
    + r"))"
)

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
ORG_CUES = [
    "기관", "소속기관", "소속 기관", "주관기관", "주관 기관", "수행기관", "수행 기관",
    "참여기관", "참여 기관", "기관정보", "기관 정보", "산학협력단",
    "사업자등록번호", "기관코드",
]

REL_PEOPLE_CUES = PEOPLE_CUES[:]  # join relation용
REL_ORG_CUES = ORG_CUES[:]
PERF_TO_PROJECT_CUES = ["어느 과제", "어떤 과제", "관련 과제", "소속 과제", "과제 정보", "과제번호", "pjt_id", "pjt id", "project id"]

FILTER_CUES = ["목록", "리스트", "현황", "통계", "건수", "몇건", "기간", "시작", "종료", "연도", "년도", "기관", "주관", "참여", "상태", "단계", "추출", "다운로드", "엑셀"]
TOPIC_CUES = ["주제", "관련", "분야", "키워드", "동향", "트렌드", "이슈", "기술", "연구", "r&d", "rd", "사례", "핵심", "정리", "요약", "분석"]

COUNT_CUES = ["건수", "몇건", "통계", "count", "총 몇", "총몇", "몇 개", "몇개"]
DETAIL_CUES = ["상세", "세부", "자세히", "정보", "내용", "설명", "프로필"]
LIST_CUES = ["목록", "리스트", "현황", "조회", "보여", "찾아줘"]


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

    # 참여인력/연구자 계열
    if any(x in t for x in ["참여인력", "참여 인력", "참여연구원", "참여 연구원", "연구자", "연구원", "연구책임자", "책임자"]):
        _add(TAG_PJT_MP)

    # 참여기관/주관기관 계열
    if any(x in t for x in ["참여기관", "참여 기관", "주관기관", "주관 기관", "수행기관", "수행 기관", "기관정보", "기관 정보", "기관코드", "사업자등록번호"]):
        _add(TAG_PJT_ORG)

    # 과제 정보(기본)
    if any(x in t for x in ["과제정보", "연구목표", "연구내용", "연구기간", "연구비", "과제명", "영문과제명", "국문과제명"]):
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


def extract_people_terms(q: str, kws: List[str], *, max_terms: int = 2) -> List[str]:
    """질의에서 사람 이름(주로 한글 2~4자) 후보 추출."""
    q = (q or "").strip()
    if not q:
        return []
    cands: List[str] = []

    for m in _NAME_LABEL_RE.finditer(q):
        s = (m.group(1) or "").strip()
        if s and s not in cands:
            cands.append(s)
            if len(cands) >= max_terms:
                return cands

    for m in _NAME_NEAR_CUE_RE.finditer(q):
        s = (m.group(1) or "").strip()
        if s and s not in cands:
            cands.append(s)
            if len(cands) >= max_terms:
                return cands

    # 키워드에 2~4자 한글이 있고, 질의에 people cue가 있으면 약하게 채택
    tl = q.lower()
    if _hit_count(tl, PEOPLE_CUES) > 0:
        for kw in (kws or [])[:20]:
            t = (kw or "").strip()
            if re.fullmatch(r"[가-힣]{2,4}", t) and (t not in cands):
                cands.append(t)
                if len(cands) >= max_terms:
                    break

    return cands[:max_terms]


def extract_gender_terms(q: str, kws: List[str]) -> List[str]:
    text = " ".join([q or ""] + list(kws or []))
    t = text.lower()
    if not t.strip():
        return []

    male = any(x in t for x in ["남자", "남성", "male", "m "]) or "남" in t
    female = any(x in t for x in ["여자", "여성", "female", "f "]) or "여" in t

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
    """질의에서 기관명 후보 추출(라벨/접미사 기반)."""
    q = (q or "").strip()
    if not q:
        return []
    cands: List[str] = []

    for m in _ORG_NEAR_LABEL_RE.finditer(q):
        s = (m.group(1) or "").strip()
        if s:
            s = re.sub(r"\s+", " ", s).strip()
        if s and s not in cands:
            cands.append(s)
            if len(cands) >= max_terms:
                return cands

    for m in _ORG_SUFFIX_RE.finditer(q):
        s = (m.group(1) or "").strip()
        if s:
            s = re.sub(r"\s+", " ", s).strip()
        if s and s not in cands:
            cands.append(s)
            if len(cands) >= max_terms:
                return cands

    # 키워드에서 suffix/회사 표기 등 잡기
    for kw in (kws or [])[:30]:
        t = (kw or "").strip()
        if not t:
            continue
        if any(suf.lower() in t.lower() for suf in _ORG_SUFFIXES):
            if t not in cands:
                cands.append(t)
            if len(cands) >= max_terms:
                break

    return cands[:max_terms]


def extract_id_candidates(q: str, kws: List[str]) -> Dict[str, List[str]]:
    """타입별 ID 후보 추출."""
    qtext = (q or "")
    out: Dict[str, List[str]] = {
        "pjt_id": [],
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
      3) people/org (사람/기관이 주어+과제/성과 요청의 head일 때)
      4) perf / project
      5) default support
    """
    if domain_hint in ("support", "project", "perf", "people", "org"):
        return domain_hint

    t = (q or "").strip()
    tl = t.lower()

    people_terms = people_terms or []
    org_terms = org_terms or []

    has_pjt_id = bool((ids_map or {}).get("pjt_id"))
    has_perf_id = bool((ids_map or {}).get("doi") or (ids_map or {}).get("issn") or (ids_map or {}).get("rst_id") or (ids_map or {}).get("patent_reg_no"))
    has_people_id = bool((ids_map or {}).get("person_no"))

    has_people = bool(people_terms) or has_people_id or _has_any_cue(tl, PEOPLE_CUES)
    has_org = bool(org_terms) or _has_any_cue(tl, ORG_CUES)

    has_project = has_pjt_id or _has_any_cue(tl, PROJECT_CUES)
    has_perf = has_perf_id or _has_any_cue(tl, PERF_CUES)

    if is_support_query(tl, has_project=has_project, has_perf=has_perf, has_people=has_people, has_org=has_org):
        return "support"

    # 사람/기관 + 과제/성과 요청이면 head로 승격(조인 플로우를 타기 쉬움)
    if has_people and (has_project or "과제" in tl or "pjt" in tl or "참여" in tl):
        return "people"
    if has_org and (has_project or "과제" in tl or "pjt" in tl or "참여" in tl):
        return "org"

    if has_people and not (has_project or has_perf):
        return "people"
    if has_org and not (has_project or has_perf):
        return "org"

    # 사람/기관 + 성과(논문/특허...) 요청이면 people/org를 head로 두고 join으로 처리하기 쉬움
    # (예: "이한조 연구자의 논문", "KAIST 특허")
    if has_people and has_perf and not has_project:
        return "people"
    if has_org and has_perf and not has_project:
        return "org"

    if has_perf and not has_project:
        return "perf"
    if has_project:
        return "project"
    return "support"


def pick_relation(q: str, base_route: str, *, has_project: bool, has_perf: bool, has_people: bool, has_org: bool) -> Optional[Tuple[str, str]]:
    """
    relation 감지:
    - project -> perf : "~ 과제의 성과"
    - project -> people/org : 참여인력/참여기관
    - perf -> project : "이 성과가 어느 과제?"
    - people/org -> project : "이한조 연구자의 과제", "DMS 참여 과제"
    """
    t = (q or "").strip().lower()
    if not t:
        return None

    wants_perf_to_project = has_perf and (has_project or any(c in t for c in PERF_TO_PROJECT_CUES))
    wants_people = has_people or any(c in t for c in REL_PEOPLE_CUES)
    wants_org = has_org or any(c in t for c in REL_ORG_CUES)

    if base_route == "people":
        # 사람 → 과제/성과
        if has_project or ("과제" in t) or ("pjt" in t) or ("참여" in t):
            return ("people", "project")
        if has_perf:
            return ("people", "perf")
        return None

    if base_route == "org":
        # 기관 → 과제/성과
        if has_project or ("과제" in t) or ("pjt" in t) or ("참여" in t):
            return ("org", "project")
        if has_perf:
            return ("org", "perf")
        return None

    if base_route == "project":
        if has_perf:
            return ("project", "perf")
        if wants_people:
            return ("project", "people")
        if wants_org:
            return ("project", "org")
        return None

    if base_route == "perf":
        if wants_perf_to_project:
            return ("perf", "project")
        if wants_people:
            return ("perf", "people")
        if wants_org:
            return ("perf", "org")
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
    years: List[str] = field(default_factory=list)
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

    def debug_dict(self) -> Dict[str, object]:
        return {
            "base_route": self.base_route,
            "relation": self.relation,
            "intent": self.intent,
            "action": self.action,
            "is_id": int(self.is_id_query),
            "long": int(self.long_query),
            "rare_ratio": round(float(self.rare_ratio), 4),
            "people_terms": self.people_terms,
            "gender_terms": self.gender_terms,
            "org_terms": self.org_terms,
            "years": self.years,
            "ids_map": self.ids_map,
            "ids_flat": self.ids_flat,
            "project_tag_filters": self.project_tag_filters,
            "perf_tag_filters": self.perf_tag_filters,
            "tag_filters": self.tag_filters,
            "wants_count": self.wants_count,
            "wants_list": self.wants_list,
            "wants_detail": self.wants_detail,
        }

def normalize_categories(cat) -> list[str]:
    if cat is None:
        return []
    xs = cat if isinstance(cat, (list, tuple, set)) else [cat]
    out = []
    for x in xs:
        if hasattr(x, "value"):  # Enum
            s = str(x.value)
        else:
            s = str(x)
        s = s.strip().lower()
        if s:
            out.append(s)
    return out

def pick_domain_hint_from_categories(cats: list[str]) -> str | None:
    # 너 시스템 기준: researcher -> people
    if any(c in ("researcher", "people") for c in cats):
        return "people"
    if any(c in ("org", "organization") for c in cats):
        return "org"
    if "project" in cats:
        return "project"
    if any(c in ("performance", "perf") for c in cats):
        return "perf"
    if "support" in cats:
        return "support"
    return None


def classify_query(q: str, kws: List[str], *, domain_hint: Optional[str] = None) -> QueryIntent:
    q = (q or "").strip()
    tl = q.lower()

    ids_map = extract_id_candidates(q, kws)

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
            or any(bool(v) for v in (ids_map or {}).values())
    )
    long_query = (len(q.split()) >= 12) or (len(q) >= 40)

    # entities
    people_terms = extract_people_terms(q, kws)
    gender_terms = extract_gender_terms(q, kws)
    org_terms = extract_org_terms(q, kws)
    years = extract_years(q)

    has_project = _has_any_cue(tl, PROJECT_CUES) or bool(ids_map.get("pjt_id"))
    has_perf = _has_any_cue(tl, PERF_CUES) or bool(ids_map.get("doi") or ids_map.get("issn") or ids_map.get("rst_id") or ids_map.get("patent_reg_no"))
    has_people = bool(people_terms) or _has_any_cue(tl, PEOPLE_CUES) or bool(ids_map.get("person_no"))
    has_org = bool(org_terms) or _has_any_cue(tl, ORG_CUES) or bool(ids_map.get("biz_no") or ids_map.get("org_code"))

    base_route = pick_base_route(
        q, kws, ids_map,
        domain_hint=domain_hint,
        people_terms=people_terms,
        org_terms=org_terms,
    )

    relation = pick_relation(
        q, base_route,
        has_project=has_project,
        has_perf=has_perf,
        has_people=has_people,
        has_org=has_org,
    )

    intent = pick_structured_intent(base_route, q, is_id_query=is_id_query)

    # tag filters
    project_tag_filters = pick_project_tag_filters(q) if base_route in ("project", "people", "org") else []
    perf_tag_filters = pick_perf_tag_filters(q) if base_route in ("perf", "project") else []

    # user ask signals
    wants_count = any(c in tl for c in COUNT_CUES)
    wants_list = any(c in tl for c in LIST_CUES)
    wants_detail = any(c in tl for c in DETAIL_CUES)

    # action (finer)
    if base_route == "support":
        action = "support"
    elif relation is not None:
        if wants_count and not wants_list:
            action = "stats"
        elif wants_list:
            action = "list"
        elif wants_detail:
            action = "detail"
        else:
            action = "relation"
    else:
        exact_id = bool(ids_map.get("pjt_id") or ids_map.get("rst_id") or ids_map.get("doi") or ids_map.get("issn") or ids_map.get("patent_reg_no") or ids_map.get("biz_no"))
        if exact_id:
            action = "id_exact"
        elif is_id_query:
            action = "id_fuzzy"
        elif wants_count:
            action = "stats"
        elif wants_list or intent == "filter":
            action = "list"
        elif intent == "topic":
            action = "topic"
        elif wants_detail:
            action = "detail"
        else:
            action = "content"

    ids_flat = flatten_ids(ids_map)

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
        years=years,
        ids_map=ids_map,
        ids_flat=ids_flat,
        project_tag_filters=project_tag_filters,
        perf_tag_filters=perf_tag_filters,
        tag_filters=list(dict.fromkeys([*project_tag_filters, *perf_tag_filters])),
        wants_count=wants_count,
        wants_list=wants_list,
        wants_detail=wants_detail,
    )
