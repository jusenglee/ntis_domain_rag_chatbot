# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from settings import logger

# 0: off
# 1: 핵심 결정/요약 (route/intent/filter/top 결과)
# 2: retrieve 요약 (vec/use_vecs/hit/top score)
# 3: 더 자세히 (kws/필드/가중치/로컬 타이밍 등)
RAG_DEBUG_LEVEL = int(os.getenv("RAG_DEBUG_LEVEL", os.getenv("RAG_DEBUG", "1")))
RAG_DEBUG_TOPN = int(os.getenv("RAG_DEBUG_TOPN", "5"))
RAG_DEBUG_MAX_KWS = int(os.getenv("RAG_DEBUG_MAX_KWS", "25"))

def dbg(level: int, msg: str) -> None:
    if RAG_DEBUG_LEVEL >= level:
        logger.info(msg)
