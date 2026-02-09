# -*- coding: utf-8 -*-
"""
rag_parts/join.py

JOIN(2-hop)에서 Hop1 결과(예: 과제/참여인력/기관 등)로부터 join-key(PJT_ID)를 추출하고,
질의에서 불필요한 용어를 제거(sanitize)하기 위한 유틸.

- extract_pjt_ids(points): Qdrant ScoredPoint(or dict) 목록에서 PJT_ID를 최대 max_ids개 추출
- sanitize_query_by_terms(q, remove_terms): 제거 대상 용어를 질의에서 제거(공백 정리)
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _as_dict(x: Any) -> Optional[Dict[str, Any]]:
    return x if isinstance(x, dict) else None


def _get_payload(point: Any) -> Optional[Dict[str, Any]]:
    # qdrant_client ScoredPoint: .payload
    pl = getattr(point, "payload", None)
    if isinstance(pl, dict):
        return pl
    # dict-like (테스트/모킹)
    d = _as_dict(point)
    if d is not None:
        pl2 = d.get("payload")
        if isinstance(pl2, dict):
            return pl2
        # 혹시 point 자체가 payload인 경우
        return d
    return None


def extract_pjt_ids(
    points: Iterable[Any],
    *,
    max_ids: int = 80,
    include_pjt_no_fallback: bool = False,
) -> List[str] | Tuple[List[str], List[str]]:
    """
    Hop1 결과 포인트들에서 PJT_ID 후보를 추출합니다.
    payload 최상위에서 추출로 변경
    """
    pjt_ids: List[str] = []
    seen_pjt_id = set()

    if not points:
        return pjt_ids

    for p in points:
        payload = _get_payload(p)
        if not payload:
            continue

        pid = None
        pid = payload.get("pjt_id")

        if pid:
            if pid in seen_pjt_id:
                continue
            pjt_ids.append(pid)
            seen_pjt_id.add(pid)
            if len(pjt_ids) >= max_ids:
                return pjt_ids
            continue

    return pjt_ids

def normalize_relation_hint(value: Any) -> Optional[Tuple[str, str]]:
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
    return None
