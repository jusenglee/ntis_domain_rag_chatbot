from __future__ import annotations

import os
import re

COL_PROJECT = os.getenv("RAG_COL_PROJECT", "ntis_project_v1").strip() or "ntis_project_v1"
COL_PERF = os.getenv("RAG_COL_PERF", "ntis_perf_v1").strip() or "ntis_perf_v1"
COL_SUPPORT = os.getenv("RAG_COL_SUPPORT", "ntis_supports_v1").strip() or "ntis_supports_v1"

RARE_TOKEN_RE = re.compile(r"[0-9]|[-_:]")
HYPHEN_ID_RE = re.compile(r"\b[A-Za-z]{2,6}-\d{2,}(?:-\d{2,})+\b")

TAG_PJT_INFO = "IRD_NAI_PJT_INFO"
TAG_PJT_MP = "IRD_NAI_PJT_MP"
TAG_PJT_ORG = "IRD_NAI_PJT_ORG"
PROJECT_TAGS = {TAG_PJT_INFO}

TAG_RI_PAPER = "IRD_NAI_RI_PAPER"
TAG_RI_IPR = "IRD_NAI_RI_IPR"
TAG_RI_SW = "IRD_NAI_RI_SW"
TAG_RI_NVR = "IRD_NAI_RI_NVR"
TAG_RI_ORGSM_INFO = "IRD_NAI_RI_ORGSM_INFO"
TAG_RI_ORGSM_RES = "IRD_NAI_RI_ORGSM_RESOURCE"
TAG_RI_COMPOUND = "IRD_NAI_RI_COMPOUND"
TAG_RI_RSCH_RPT = "IRD_NAI_RI_RSCH_RPT"
TAG_RI_FCLT_EQUIP = "IRD_NAI_RI_FCLT_EQUIP"
TAG_RI_TECH_INFO = "IRD_NAI_RI_TECH_INFO"

PERF_TAGS = {
    TAG_RI_PAPER,
    TAG_RI_IPR,
    TAG_RI_SW,
    TAG_RI_NVR,
    TAG_RI_ORGSM_INFO,
    TAG_RI_ORGSM_RES,
    TAG_RI_COMPOUND,
    TAG_RI_RSCH_RPT,
    TAG_RI_FCLT_EQUIP,
    TAG_RI_TECH_INFO,
}

PERF_TYPE_TAG_TO_CATEGORY = {
    TAG_RI_PAPER: "논문",
    TAG_RI_IPR: "특허",
    TAG_RI_SW: "소프트웨어",
    TAG_RI_NVR: "생명정보",
    TAG_RI_ORGSM_INFO: "생물정보",
    TAG_RI_ORGSM_RES: "생물자원",
    TAG_RI_COMPOUND: "화합물",
    TAG_RI_RSCH_RPT: "연구보고서",
    TAG_RI_FCLT_EQUIP: "시설장비",
    TAG_RI_TECH_INFO: "기술요약",
}

PERF_TYPE_CATEGORY_ALIASES = {
    "논문": TAG_RI_PAPER,
    "학술지": TAG_RI_PAPER,
    "paper": TAG_RI_PAPER,
    "특허": TAG_RI_IPR,
    "patent": TAG_RI_IPR,
    "소프트웨어": TAG_RI_SW,
    "software": TAG_RI_SW,
    "sw": TAG_RI_SW,
    "연구보고서": TAG_RI_RSCH_RPT,
    "보고서": TAG_RI_RSCH_RPT,
    "report": TAG_RI_RSCH_RPT,
    "rpt": TAG_RI_RSCH_RPT,
    "시설장비": TAG_RI_FCLT_EQUIP,
    "시설": TAG_RI_FCLT_EQUIP,
    "장비": TAG_RI_FCLT_EQUIP,
    "equipment": TAG_RI_FCLT_EQUIP,
    "equip": TAG_RI_FCLT_EQUIP,
    "기술요약": TAG_RI_TECH_INFO,
    "기술정보": TAG_RI_TECH_INFO,
    "tech": TAG_RI_TECH_INFO,
    "생명정보": TAG_RI_NVR,
    "nvr": TAG_RI_NVR,
    "생물정보": TAG_RI_ORGSM_INFO,
    "orgsm": TAG_RI_ORGSM_INFO,
    "생물자원": TAG_RI_ORGSM_RES,
    "resource": TAG_RI_ORGSM_RES,
    "화합물": TAG_RI_COMPOUND,
    "compound": TAG_RI_COMPOUND,
}

_PERF_TYPE_TAG_ALIASES: dict[str, str] = {}
for _tag in PERF_TAGS:
    _tag_upper = str(_tag).strip().upper()
    if not _tag_upper:
        continue
    _PERF_TYPE_TAG_ALIASES[_tag_upper] = _tag
    _PERF_TYPE_TAG_ALIASES[_tag_upper.replace("IRD_NAI_", "")] = _tag
    _PERF_TYPE_TAG_ALIASES[_tag_upper.replace("IRD_", "")] = _tag
    _PERF_TYPE_TAG_ALIASES[_tag_upper.replace("NAI_", "")] = _tag


def _normalize_perf_type_key(value: str) -> str:
    """성과 유형 alias 비교를 위해 공백, 하이픈, 밑줄을 제거한 정규화 키를 만든다."""
    return re.sub(r"[\s_-]+", "", value.strip().lower())


def normalize_perf_types(values: list[object]) -> dict[str, list[str]]:
    """성과 유형 입력 목록을 tag, category, unknown 세 갈래로 정리한다.

    직접 tag, 한국어 alias, 영문 alias를 모두 받아들여 retrieval filter 단계가 canonical tag 집합만 다루게 만든다.
    """
    tags: list[str] = []
    categories: list[str] = []
    unknown: list[str] = []
    for raw in values or []:
        text = str(raw).strip()
        if not text:
            continue
        tag = _PERF_TYPE_TAG_ALIASES.get(text.strip().upper())
        if tag is None:
            key = _normalize_perf_type_key(text)
            tag = PERF_TYPE_CATEGORY_ALIASES.get(key)
        if tag is not None:
            if tag not in tags:
                tags.append(tag)
            category = PERF_TYPE_TAG_TO_CATEGORY.get(tag)
            if category and category not in categories:
                categories.append(category)
            continue
        unknown.append(text)
    return {"tags": tags, "categories": categories, "unknown": unknown}
