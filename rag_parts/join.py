# -*- coding: utf-8 -*-
"""
rag_parts/join.py

JOIN(2-hop)에서 Hop1 결과(예: 과제/참여인력/기관 등)로부터 join-key(PJT_ID)를 추출하고,
질의에서 불필요한 용어를 제거(sanitize)하기 위한 유틸.

- extract_pjt_ids(points): Qdrant ScoredPoint(or dict) 목록에서 PJT_ID 후보를 최대 max_ids개 추출
- sanitize_query_by_terms(q, remove_terms): 제거 대상 용어를 질의에서 제거(공백 정리)
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 흔한 중첩 경로 후보 (payload 안의 meta_basic/meta_detail dict)
_META_KEYS = ("meta_basic", "meta_detail")
TOP_PJT_ID_KEYS = ("pjt_id")
FALLBACK_PJT_NO_KEYS = ("pjt_no", "meta_basic.pjt_no", "meta_detail.pjt_no")


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


def _get_path_value(payload: Dict[str, Any], key_path: str) -> Any:
    """dot 표기(`a.b.c`)를 따라 중첩 dict 값을 읽습니다."""
    if not isinstance(payload, dict) or not key_path:
        return None
    if "." not in key_path:
        return payload.get(key_path)

    cur: Any = payload
    for part in key_path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur

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


def extract_pjt_ids(
    points: Iterable[Any],
    *,
    max_ids: int = 80,
    include_pjt_no_fallback: bool = False,
) -> List[str] | Tuple[List[str], List[str]]:
    """
    Hop1 결과 포인트들에서 PJT_ID 후보를 추출합니다.
    payload 최상위 및 meta 점경로(pjt_id 계열)에서 우선 추출합니다.
    include_pjt_no_fallback=True면 pjt_id 미추출 항목에서 pjt_no 후보를 별도로 수집해 함께 반환합니다.
    """
    pjt_ids: List[str] = []
    pjt_nos: List[str] = []
    seen_pjt_id = set()
    seen_pjt_no = set()

    if not points:
        return (pjt_ids, pjt_nos) if include_pjt_no_fallback else pjt_ids

    for p in points:
        payload = _get_payload(p)
        if not payload:
            continue

        pid = None
        pid = payload.get("pjt_id")

        if pid:
            if pid in seen_pjt_id:
                if include_pjt_no_fallback:
                    pjt_no = None
                    pjt_no = payload.get("pjt_no")
                    if pjt_no and pjt_no not in seen_pjt_no:
                        pjt_nos.append(pjt_no)
                        seen_pjt_no.add(pjt_no)
                continue
            pjt_ids.append(pid)
            seen_pjt_id.add(pid)
            if include_pjt_no_fallback:
                pjt_no = None
                for key_path in FALLBACK_PJT_NO_KEYS:
                    pjt_no =  payload.get("pjt_no")
                    if pjt_no:
                        break
                if pjt_no and pjt_no not in seen_pjt_no:
                    pjt_nos.append(pjt_no)
                    seen_pjt_no.add(pjt_no)
            if len(pjt_ids) >= max_ids:
                return (pjt_ids, pjt_nos) if include_pjt_no_fallback else pjt_ids
            continue

        if include_pjt_no_fallback:
            pjt_no = None
            pjt_no =  payload.get("pjt_no")
            if pjt_no and pjt_no not in seen_pjt_no:
                pjt_nos.append(pjt_no)
                seen_pjt_no.add(pjt_no)

    return (pjt_ids, pjt_nos) if include_pjt_no_fallback else pjt_ids



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
