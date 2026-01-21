# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional


_PJT_KEYS = (
    "pjt_id", "PJT_ID", "pjtId", "PJTID",
    "과제ID", "과제번호", "과제고유번호",
)

# PJT_ID가 보통 숫자열이면(예: 10자리 전후) 이걸로 추가 추출
_PJT_ID_RE = re.compile(r"\b(\d{7,14})\b")


def _as_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, (list, tuple, set)):
        return list(x)
    return [x]


def _get_meta(pl: dict) -> dict:
    m = pl.get("meta")
    return m if isinstance(m, dict) else {}


def _extract_ids_from_value(v: Any) -> List[str]:
    out: List[str] = []
    for one in _as_list(v):
        if one is None:
            continue
        s = str(one).strip()
        if not s:
            continue

        # 1) 값 자체가 깔끔한 ID면 그대로
        if s.isdigit():
            out.append(s)
            continue

        # 2) 혼합 문자열이면 숫자 토큰만 추가 추출
        #    (예: "PJT_ID: 1340018736; ..." / "1340018736, 1340018737")
        out.extend(_PJT_ID_RE.findall(s))
    return out


def extract_pjt_ids(points: List[Any], *, max_ids: int = 12) -> List[str]:
    ids: List[str] = []
    seen: set[str] = set()

    for p in points or []:
        pl = getattr(p, "payload", None) or {}
        if not isinstance(pl, dict):
            continue
        meta = _get_meta(pl)

        cands: List[Any] = []

        # payload / meta에 흔히 존재
