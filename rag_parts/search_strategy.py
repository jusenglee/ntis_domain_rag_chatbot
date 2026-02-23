# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

def safe_json(obj: Any, max_len: int = 1200) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    if len(s) > max_len:
        return s[:max_len] + "…"
    return s

def first_match(text_lower: str, cues: List[str]) -> Optional[str]:
    for c in cues:
        if c and c.lower() in text_lower:
            return c
    return None

def count_hits(sr: Dict[str, Any]) -> Tuple[int, int, float]:
    dense_hit = 0
    best_dense = -1.0
    for _, lst in (sr.get("dense") or {}).items():
        dense_hit += len(lst)
        if lst:
            best_dense = max(best_dense, float(lst[0].score))
    lex_hit = len(sr.get("lexical") or [])
    return dense_hit, lex_hit, best_dense

def point_brief(p: Any) -> Dict[str, Any]:
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        pl = {}
    meta = {}
    for key in ("meta_basic", "meta_detail"):
        v = pl.get(key)
        if isinstance(v, dict):
            meta.update(v)

    score = getattr(p, "score", None)
    title = (
            pl.get("title_text")
            or pl.get("title1")
            or pl.get("title2")
            or ""
    )
    doc_id = pl.get("doc_id") or ""
    col = pl.get("_collection") or ""
    st = ""
    tag = pl.get("tag") or ""
    return {
        "score": float(score) if score is not None else None,
        "col": str(col),
        "doc_id": str(doc_id),
        "tag": str(tag),
        "source_table": str(st),
        "title": str(title)[:80],
    }
