# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Iterable, List, Optional, Tuple

def norm_tag_from_payload(pl: dict) -> str:
    meta = pl.get("meta") if isinstance(pl.get("meta"), dict) else {}
    t = (pl.get("tag") or meta.get("doc_type") or meta.get("source_table") or meta.get("sourceTable") or "")
    t = str(t).strip().upper()
    return t[4:] if t.startswith("IRD_") else t

def _normalize_tag_value(tag: object) -> str:
    if tag is None:
        return ""
    t = str(tag).strip().upper()
    return t[4:] if t.startswith("IRD_") else t

def tag_match_bonus(
    p: Any,
    *,
    tag_filters: Optional[Iterable[str]],
    boost: float,
    mismatch_penalty: float = 0.0,
) -> float:
    if not tag_filters:
        return 0.0
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        return 0.0
    payload_tag = norm_tag_from_payload(pl)
    if not payload_tag:
        return 0.0
    filt_norm = {_normalize_tag_value(t) for t in tag_filters if str(t).strip()}
    if not filt_norm:
        return 0.0
    if payload_tag in filt_norm:
        return float(boost)
    if mismatch_penalty:
        return -float(mismatch_penalty)
    return 0.0

def dedup_by_doc_id(points: List[Any], max_keep: Optional[int] = None) -> List[Any]:
    out: List[Any] = []
    seen_doc: set[str] = set()
    seen_fallback: set[Tuple[str, str]] = set()

    for p in points or []:
        pl = getattr(p, "payload", None) or {}
        if not isinstance(pl, dict):
            pl = {}
        doc_id = str(pl.get("doc_id") or "")
        col = str(pl.get("_collection") or "")
        pid = str(getattr(p, "id", ""))

        if doc_id:
            if doc_id in seen_doc:
                continue
            seen_doc.add(doc_id)
            out.append(p)
        else:
            k = (col, pid)
            if k in seen_fallback:
                continue
            seen_fallback.add(k)
            out.append(p)

        if (max_keep is not None) and (len(out) >= max_keep):
            break

    return out
