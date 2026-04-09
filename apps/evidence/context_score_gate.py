from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Sequence

from apps.platform.settings import RAG_CONTEXT_COMPRESS_MIN_SCORE


@dataclass(frozen=True)
class ContextScoreHit:
    point: Any
    rank: int
    final_score: float
    gate_passed: bool


def _resolve_final_score(point: Any) -> float:
    payload = getattr(point, "payload", None) or {}
    candidates = []
    if isinstance(payload, dict):
        candidates.extend(
            [
                payload.get("_final_total"),
                payload.get("final_score"),
                payload.get("_rrf"),
                payload.get("score"),
            ]
        )
    candidates.extend([getattr(point, "_final_total", None), getattr(point, "score", None)])
    for candidate in candidates:
        try:
            return float(candidate)
        except Exception:
            continue
    return 0.0


def normalize_reranked_hits(
    reranked_hits: Sequence[Any],
    *,
    min_score: float | None = None,
) -> List[ContextScoreHit]:
    floor = float(RAG_CONTEXT_COMPRESS_MIN_SCORE if min_score is None else min_score)
    normalized: List[ContextScoreHit] = []
    for rank, point in enumerate(list(reranked_hits or []), start=1):
        final_score = _resolve_final_score(point)
        normalized.append(
            ContextScoreHit(
                point=point,
                rank=rank,
                final_score=final_score,
                gate_passed=final_score >= floor,
            )
        )
    return normalized
