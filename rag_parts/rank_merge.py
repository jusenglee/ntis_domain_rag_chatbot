from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RankSource:
    name: str
    weight: float
    points: List[Any]


def resolve_collection(point: Any, payload: Optional[dict] = None) -> str:
    if payload is not None:
        pl = payload
    elif isinstance(point, dict):
        pl = point.get("payload", None)
    else:
        pl = getattr(point, "payload", None)
    pl = pl or {}
    if not isinstance(pl, dict):
        pl = {}
    col = pl.get("_collection")
    if not col:
        if isinstance(point, dict):
            col = point.get("_collection")
        else:
            col = getattr(point, "_collection", None)
    return str(col) if col else ""


def hit_key(point: Any) -> Tuple[str, str]:
    payload = getattr(point, "payload", None) or {}
    if not isinstance(payload, dict):
        payload = {}
    col = resolve_collection(point, payload)
    pid = str(payload.get("doc_id") or getattr(point, "id", "") or "")
    return (col, pid)


def rrf_merge(sources: List[RankSource], *, rrf_k: int = 60, keep: int = 2000) -> List[Any]:
    """sources 내의 랭킹을 RRF로 합친다(가중치 지원)."""
    score: Dict[Tuple[str, str], float] = {}
    best_obj: Dict[Tuple[str, str], Any] = {}

    for src in sources:
        w = float(src.weight)
        for rank, point in enumerate(src.points or []):
            key = hit_key(point)
            if key not in best_obj:
                best_obj[key] = point
            score[key] = score.get(key, 0.0) + (w / float(rrf_k + rank + 1))

    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    out: List[Any] = []
    for key, merged_score in ranked[: max(1, int(keep))]:
        point = best_obj.get(key)
        if point is None:
            continue
        payload = getattr(point, "payload", None)
        if isinstance(payload, dict):
            payload["_rrf"] = float(merged_score)
        out.append(point)
    return out
