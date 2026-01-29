# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re

# -----------------------------
# Common keys / collections
# -----------------------------
KEY_ORG_NORM = (os.getenv("RAG_KEY_ORG_NORM", "org_name").strip() or "org_name")

COL_PROJECT = (os.getenv("RAG_COL_PROJECT", "ntis_project_v2").strip() or "ntis_project_v2")
COL_PERF = (os.getenv("RAG_COL_PERF", "ntis_project_v2").strip() or "ntis_project_v2")
COL_SUPPORT = (os.getenv("RAG_COL_SUPPORT", "ntis_project_v2").strip() or "ntis_project_v2")

# -----------------------------
# Regex
# -----------------------------
RARE_TOKEN_RE = re.compile(r"[0-9]|[-_:]")
HYPHEN_ID_RE = re.compile(r"\b[A-Za-z]{2,6}-\d{2,}(?:-\d{2,})+\b")
ORG_RE = re.compile(
    r"(?:기관|소속|주관|수행|참여)\s*(?:기관명)?\s*[:：]?\s*([가-힣A-Za-z0-9㈜().·\-\s]{2,40})"
)

# -----------------------------
# Tag constants (payload.tag)
# -----------------------------
# project tags
TAG_PJT_INFO = "IRD_NAI_PJT_INFO"
TAG_PJT_MP = "IRD_NAI_PJT_MP"
TAG_PJT_ORG = "IRD_NAI_PJT_ORG"
# NOTE: 프로젝트 계열 tag는 IRD_NAI_PJT_INFO로 통일 (MP/ORG는 레거시).
PROJECT_TAGS = {TAG_PJT_INFO}

# perf tags
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
