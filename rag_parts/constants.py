# -*- coding: utf-8 -*-
"""
RAG 파이프라인에서 공통으로 쓰는 상수/정규식/큐 리스트를 모아둔 모듈.

주의:
- env 기반 상수들은 import 시점에 평가됩니다.
- 프로젝트마다 key 이름이 다를 수 있어 env 로 override 가능하도록 유지합니다.
"""
from __future__ import annotations

import os
import re

# -------------------------
# Org parsing
# -------------------------
ORG_RE = re.compile(r"([가-힣A-Za-z0-9]+(?:대학교|대학|연구원|연구소|병원|재단|센터))")
KEY_ORG_NORM = (os.getenv("RAG_KEY_ORG_NORM", "org_name_norm").strip() or "org_name_norm")

# -------------------------
# Collections (3-way)
# -------------------------
COL_SUPPORT = (os.getenv("RAG_COL_SUPPORT", "ntis_supports").strip() or "ntis_supports")  # 고객지원
COL_PROJECT = (os.getenv("RAG_COL_PROJECT", "ntis_project").strip() or "ntis_project")    # 과제정보
COL_PERF    = (os.getenv("RAG_COL_PERF", "ntis_perf").strip() or "ntis_perf")             # 성과정보

# -------------------------
# Tags
# -------------------------
TAG_PJT_INFO = "IRD_NAI_PJT_INFO"  # 과제
TAG_PJT_MP   = "IRD_NAI_PJT_MP"    # 과제-참여인력
TAG_PJT_ORG  = "IRD_NAI_PJT_ORG"   # 과제-참여기관
PROJECT_TAGS = {TAG_PJT_INFO, TAG_PJT_MP, TAG_PJT_ORG}

TAG_RI_PAPER        = "IRD_NAI_RI_PAPER"
TAG_RI_IPR          = "IRD_NAI_RI_IPR"
TAG_RI_SW           = "IRD_NAI_RI_SW"
TAG_RI_NVR          = "IRD_NAI_RI_NVR"
TAG_RI_ORGSM_INFO   = "IRD_NAI_RI_ORGSM_INFO"
TAG_RI_ORGSM_RES    = "IRD_NAI_RI_ORGSM_RESOURCE"
TAG_RI_COMPOUND     = "IRD_NAI_RI_COMPOUND"
TAG_RI_RSCH_RPT     = "IRD_NAI_RI_RSCH_RPT"
TAG_RI_FCLT_EQUIP   = "IRD_NAI_RI_FCLT_EQUIP"
TAG_RI_TECH_INFO    = "IRD_NAI_RI_TECH_INFO"

PERF_TAGS = {
    TAG_RI_PAPER, TAG_RI_IPR, TAG_RI_SW, TAG_RI_NVR, TAG_RI_ORGSM_INFO,
    TAG_RI_ORGSM_RES, TAG_RI_COMPOUND, TAG_RI_RSCH_RPT, TAG_RI_FCLT_EQUIP,
    TAG_RI_TECH_INFO,
}

# -------------------------
# Query heuristics regex
# -------------------------
RARE_TOKEN_RE = re.compile(r"[0-9]|[-_:]")
YEAR_RE = re.compile(r"(19\d{2}|20\d{2})")
HYPHEN_ID_RE = re.compile(r"\d{2,}[-_]\d{2,}")  # 11543-43 같은 형태

# -------------------------
# Routing cues
# -------------------------
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
    "표준", "standard",
    "기술이전", "사업화",
]

REL_PEOPLE_CUES = ["연구자", "연구원", "참여인력", "참여 인력", "참여연구원", "참여 연구원", "연구책임자", "연구 책임자", "책임자", "참여자", "인력", "연구진"]
REL_ORG_CUES = ["참여기관", "참여 기관", "주관기관", "주관 기관", "수행기관", "수행 기관", "기관정보", "기관 정보", "소속기관", "소속 기관"]
PERF_TO_PROJECT_CUES = ["어느 과제", "어떤 과제", "관련 과제", "소속 과제", "과제 정보", "과제번호", "pjt_id", "pjt id", "project id"]

# S3.5 intent cues
FILTER_CUES = ["목록", "리스트", "현황", "통계", "건수", "몇건", "기간", "시작", "종료", "연도", "년도", "기관", "주관", "참여", "상태", "단계", "추출", "다운로드", "엑셀"]
TOPIC_CUES = ["주제", "관련", "분야", "키워드", "동향", "트렌드", "이슈", "기술", "연구", "r&d", "rd", "사례", "핵심", "정리", "요약", "분석"]
