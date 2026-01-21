# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .constants import RARE_TOKEN_RE, HYPHEN_ID_RE

@dataclass
class QueryProfile:
    rare_kws: List[str]
    rare_ratio: float
    is_id_query: bool
    long_query: bool

def is_rare_token(tok: str) -> bool:
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

def profile_query(q: str, kws: List[str]) -> QueryProfile:
    rare_kws = [kw for kw in (kws or []) if is_rare_token(kw)]
    rare_ratio = len(rare_kws) / max(1, len(kws or []))

    is_id_query = (
            len(rare_kws) >= 2
            or any((kw.isdigit() and len(kw) >= 6) for kw in (kws or []))
            or ("::" in (q or ""))
            or bool(HYPHEN_ID_RE.search(q or ""))
    )
    qq = (q or "")
    long_query = (len(qq.split()) >= 12) or (len(qq) >= 40)

    return QueryProfile(
        rare_kws=rare_kws,
        rare_ratio=float(rare_ratio),
        is_id_query=bool(is_id_query),
        long_query=bool(long_query),
    )
