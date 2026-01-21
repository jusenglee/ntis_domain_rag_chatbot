# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from typing import Dict, Optional, Set

def parse_vecsets_env(s: str) -> Dict[str, set]:
    out: Dict[str, set] = {}
    s = (s or "").strip()
    if not s:
        return out
    for part in s.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        col, vecs = part.split(":", 1)
        col = col.strip()
        vset = {v.strip() for v in vecs.split(",") if v.strip()}
        if col and vset:
            out[col] = vset
    return out

_VECSETS_MAP = parse_vecsets_env(os.getenv("RAG_VECSETS", ""))
_VECSETS_CACHE: Dict[str, Optional[set]] = {}

def named_vectors_in_collection(client, collection_name: str) -> Optional[Set[str]]:
    # env가 있으면 env 우선
    if _VECSETS_MAP:
        return _VECSETS_MAP.get(collection_name)

    if collection_name in _VECSETS_CACHE:
        return _VECSETS_CACHE[collection_name]

    vecset: Optional[set] = None
    try:
        info = client.get_collection(collection_name)
        params = info.config.params
        vecs = params.vectors
        if isinstance(vecs, dict):
            vecset = set(vecs.keys())
        else:
            vecset = None
    except Exception:
        vecset = None

    _VECSETS_CACHE[collection_name] = vecset
    return vecset
