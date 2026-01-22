# -*- coding: utf-8 -*-
"""
rag_parts/join.py

JOIN(2-hop)에서 Hop1 결과(예: 과제/참여인력/기관 등)로부터 join-key(PJT_ID)를 추출하고,
질의에서 불필요한 용어를 제거(sanitize)하기 위한 유틸.

- extract_pjt_ids(points): Qdrant ScoredPoint(or dict) 목록에서 PJT_ID 후보를 최대 max_ids개 추출
- sanitize_query_by_terms(q, remove_terms): 제거 대상 용어를 질의에서 제거(공백 정리)

주의:
- PJT_ID는 저장 스키마에 따라 meta.PJT_ID / meta.pjt_id / payload.PJT_ID 등으로 달라질 수 있어
  여러 키 변형을 모두 확인합니다.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


_PJT_ID_KEYS = (
    "PJT_ID",
    "pjt_id",
    "과제번호",
    "pjtId",
    "PJTID",
)

# 흔한 중첩 경로 후보 (payload 안의 meta dict)
_META_KEYS = ("meta", "metadata")


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


def _get_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    for mk in _META_KEYS:
        v = payload.get(mk)
        if isinstance(v, dict):
            return v
    return {}


def _normalize_pjt_id(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    # 보통 숫자만(또는 'ntis:' prefix 등)이 섞일 수 있으니 숫자만 추출해도 충분하면 사용
    digits = re.sub(r"\D+", "", s)
    if len(digits) >= 8:  # 경험적으로 PJT_ID가 10자리 전후인 경우가 많음
        return digits
    return s


def extract_pjt_ids(points: Iterable[Any], *, max_ids: int = 80) -> List[str]:
    """Hop1 결과 포인트들에서 PJT_ID 후보를 추출합니다."""
    out: List[str] = []
    seen = set()

    if not points:
        return out

    for p in points:
        payload = _get_payload(p)
        if not payload:
            continue

        meta = _get_meta(payload)

        # 1) meta에서 먼저 찾기
        for k in _PJT_ID_KEYS:
            if k in meta:
                pid = _normalize_pjt_id(meta.get(k))
                if pid and pid not in seen:
                    out.append(pid)
                    seen.add(pid)
                    if len(out) >= max_ids:
                        return out

        # 2) payload 최상위에서도 찾기
        for k in _PJT_ID_KEYS:
            if k in payload:
                pid = _normalize_pjt_id(payload.get(k))
                if pid and pid not in seen:
                    out.append(pid)
                    seen.add(pid)
                    if len(out) >= max_ids:
                        return out

        # 3) meta 안에 또 다른 dict 구조가 있는 경우(희귀)
        if isinstance(meta, dict):
            for v in meta.values():
                if isinstance(v, dict):
                    for k in _PJT_ID_KEYS:
                        if k in v:
                            pid = _normalize_pjt_id(v.get(k))
                            if pid and pid not in seen:
                                out.append(pid)
                                seen.add(pid)
                                if len(out) >= max_ids:
                                    return out

    return out


def sanitize_query_by_terms(q: str, remove_terms: Optional[List[str]] = None) -> str:
    """질의에서 특정 용어를 제거합니다. (Hop1 질의 집중용)"""
    if not q:
        return ""
    out = q

    for term in (remove_terms or []):
        t = (term or "").strip()
        if not t:
            continue
        # 한국어/혼합 텍스트에서 \b 경계가 잘 안 먹는 경우가 있어 substring 제거를 기본으로 둠
        out = re.sub(re.escape(t), " ", out, flags=re.IGNORECASE)

    out = re.sub(r"\s+", " ", out).strip()
    return out or q.strip()
