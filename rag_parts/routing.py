# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .constants import (
    SUPPORT_STRONG_CUES, SUPPORT_WEAK_CUES,
    PROJECT_CUES, PERF_CUES,
    REL_PEOPLE_CUES, REL_ORG_CUES, PERF_TO_PROJECT_CUES,
    FILTER_CUES, TOPIC_CUES, YEAR_RE,
)

# -------------------------
# Domain hint normalization
# -------------------------
_DOMAIN_KEYS = ("PROJECT", "PERFORMANCE", "RESEARCHER", "SUPPORT")

def normalize_domain_hint(domain_hint: Optional[str]) -> Optional[str]:
    """
    Accepts:
      - "PROJECT" / "PERFORMANCE" / "RESEARCHER" / "SUPPORT"
      - Korean like "과제" "성과" "연구자" "문의"
      - Noisy strings like "- PROJECT: ..." (extract first matching domain key)
    Returns: "project" | "perf" | "researcher" | "support" | None
    """
    if not domain_hint:
        return None
    s = str(domain_hint).strip()
    if not s:
        return None

    # Try to find first domain token by appearance
    m = re.search(r"\b(PROJECT|PERFORMANCE|RESEARCHER|SUPPORT)\b", s, flags=re.IGNORECASE)
    if m:
        key = m.group(1).upper()
    else:
        # Korean heuristics (only used when explicit domain token not found)
        sl = s.lower()
        if any(k in sl for k in ["연구자", "researcher", "인력", "people", "person"]):
            key = "RESEARCHER"
        elif any(k in sl for k in ["성과", "performance", "perf", "논문", "특허", "보고서", "소프트웨어", "장비"]):
            key = "PERFORMANCE"
        elif any(k in sl for k in ["과제", "project", "pjt", "연구개발", "r&d"]):
            key = "PROJECT"
        elif any(k in sl for k in ["문의", "qna", "manual", "매뉴얼", "메뉴얼", "오류", "에러"]):
            key = "SUPPORT"
        else:
            return None

    if key == "PROJECT":
        return "project"
    if key == "PERFORMANCE":
        return "perf"
    if key == "RESEARCHER":
        return "researcher"
    if key == "SUPPORT":
        return "support"
    return None


# -------------------------
# Existing helpers (kept for action/intent detection)
# -------------------------
def support_hit_count(t: str, cues: List[str]) -> int:
    return sum(1 for c in cues if c and c.lower() in t)

def is_support_query(q: str, has_project: bool | None = None, has_perf: bool | None = None) -> bool:
    t = (q or "").strip().lower()
    if not t:
        return False

    if ("qna" in t) or ("manual" in t) or ("매뉴얼" in t) or ("메뉴얼" in t):
        return True

    strong = support_hit_count(t, SUPPORT_STRONG_CUES)
    weak = support_hit_count(t, SUPPORT_WEAK_CUES)
    if strong == 0 and weak == 0:
        return False

    trouble = any(x in t for x in ["오류", "에러", "실패", "안돼", "안됩니다", "접속", "접속불가", "권한", "차단"])
    if strong > 0 and trouble:
        return True

    if not (has_project or has_perf):
        return True

    account_like = any(x in t for x in ["회원가입", "로그인", "비밀번호", "아이디", "인증", "권한", "승인"])
    return (strong > 0 and account_like)

def is_project_query(q: str) -> bool:
    t = (q or "").strip().lower()
    return bool(t) and any(c.lower() in t for c in PROJECT_CUES)

def is_perf_query(q: str) -> bool:
    t = (q or "").strip().lower()
    return bool(t) and any(c.lower() in t for c in PERF_CUES)


# -------------------------
# Base route (domain_hint wins)
# -------------------------
def pick_base_route(q: str, *, is_id_query: bool, domain_hint: Optional[str] = None) -> str:
    dh = normalize_domain_hint(domain_hint)
    if dh:
        return dh

    # fallback: legacy heuristic (only when domain_hint missing)
    t = (q or "").strip()
    if not t:
        return "support"
    tl = t.lower()

    has_project = is_project_query(tl) or is_id_query
    has_perf = is_perf_query(tl)

    if is_support_query(tl, has_project=has_project, has_perf=has_perf):
        return "support"
    if has_perf and not has_project:
        return "perf"
    if has_project:
        return "project"
    return "support"


# -------------------------
# Relation (researcher supported)
# -------------------------
def pick_relation(q: str, base_route: str) -> Optional[Tuple[str, str]]:
    t = (q or "").strip().lower()
    if not t:
        return None

    has_project = is_project_query(t)
    has_perf = is_perf_query(t)
    has_people = any(c in t for c in REL_PEOPLE_CUES) or any(k in t for k in ["연구자", "참여인력", "참여인원", "인력", "people", "person"])
    has_org = any(c in t for c in REL_ORG_CUES)

    wants_perf_to_project = has_perf and (has_project or any(c in t for c in PERF_TO_PROJECT_CUES))

    if base_route == "project":
        if has_perf:
            return ("project", "perf")
        if has_people:
            return ("project", "people")
        if has_org:
            return ("project", "org")
        return None

    if base_route == "perf":
        if wants_perf_to_project:
            return ("perf", "project")
        if has_people:
            return ("perf", "people")
        if has_org:
            return ("perf", "org")
        return None

    # ✅ researcher: 기본 hop1=people, hop2=project/perf
    if base_route == "researcher":
        # "참여 과제/프로젝트" 류면 people -> project
        if any(k in t for k in ["과제", "project", "pjt", "참여과제", "참여 과제", "참여한 과제", "참여 프로젝트"]):
            return ("people", "project")
        # "성과/논문/특허" 류면 people -> perf (선택)
        if any(k in t for k in ["성과", "논문", "특허", "보고서", "소프트웨어", "performance", "paper", "patent"]):
            return ("people", "perf")
        return None

    return None  # support


# -------------------------
# Structured intent (kept)
# -------------------------
def pick_structured_intent(base_route: str, q: str, *, is_id_query: bool) -> str:
    t = (q or "").strip().lower()
    if not t:
        return "content"
    if base_route == "support":
        return "support"
    if is_id_query:
        return "id"
    has_year = bool(YEAR_RE.search(t))
    if has_year or any(c in t for c in FILTER_CUES):
        return "filter"
    if any(c in t for c in TOPIC_CUES) or ("관련" in t and ("과제" in t or "project" in t or "pjt" in t)):
        return "topic"
    return "content"

