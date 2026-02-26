# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

SEARCH_POLICY_VERSION = "v1.2"


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

    score = getattr(p, "score", None)
    title = (
        pl.get("title_text")
        or pl.get("title1")
        or pl.get("title2")
        or ""
    )
    doc_id = pl.get("doc_id") or ""
    col = pl.get("_collection") or ""
    tag = pl.get("tag") or ""
    return {
        "score": float(score) if score is not None else None,
        "col": str(col),
        "doc_id": str(doc_id),
        "tag": str(tag),
        "source_table": "",
        "title": str(title)[:80],
    }


def build_strategy_key(action: Optional[str], mode: Optional[str]) -> str:
    action_norm = str(action or "").strip().lower() or "unknown"
    mode_norm = str(mode or "").strip().lower() or "unknown"
    return f"{action_norm}:{mode_norm}"


def build_rerank_spec(mode: Optional[str]) -> Dict[str, Any]:
    """실행 모드별 rerank preset 생성.

    NOTE: 최소 계약(top-level key)은 유지하고, 상세 가중치는 추후 정책 고도화 시 교체 가능.
    """
    mode_norm = str(mode or "").strip().lower()

    if mode_norm == "lookup":
        return {
            "final_keep": 80,
            "rerank_weights": {
                "lexical": 1.2,
                "dense": 1.0,
                "tag": 0.5,
            },
        }
    if mode_norm == "join":
        return {
            "final_keep": 120,
            "rerank_weights": {
                "lexical": 1.1,
                "dense": 1.0,
                "join_key": 0.8,
            },
        }
    return {
        "final_keep": 80,
        "rerank_weights": {
            "lexical": 1.0,
            "dense": 1.0,
        },
    }
