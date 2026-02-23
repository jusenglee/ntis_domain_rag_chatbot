from __future__ import annotations

import os
from typing import Any, Callable, Optional

from rag_parts.planner_contract import StrategyViolation


def evaluate_reranked_contract(
        *,
        reranked: list[Any],
        min_reranked: int,
        min_final_avg: float,
        min_final_max: float,
        score_topn: int,
        timing_put: Callable[[str, Any], None],
) -> Optional[str]:
    if not reranked:
        return "no_reranked"
    if min_reranked and len(reranked) < min_reranked:
        return "insufficient_hits"
    if min_final_avg <= 0 and min_final_max <= 0:
        return None

    score_vals: list[float] = []
    for point in reranked[: max(1, score_topn)]:
        payload = getattr(point, "payload", None) or {}
        try:
            score_vals.append(float(payload.get("_final_total")))
        except Exception:
            continue

    if not score_vals:
        return None

    score_avg = sum(score_vals) / max(1, len(score_vals))
    score_max = max(score_vals)
    timing_put("metric.final_score_avg", float(score_avg))
    timing_put("metric.final_score_max", float(score_max))

    if (min_final_avg > 0 and score_avg < min_final_avg) or (min_final_max > 0 and score_max < min_final_max):
        return "low_score"
    return None


def enforce_reranked_contract(
        *,
        reranked: list[Any],
        min_reranked: int,
        min_final_avg: float,
        min_final_max: float,
        score_topn: int,
        timing_put: Callable[[str, Any], None],
) -> Optional[str]:
    contract_fail_reason = evaluate_reranked_contract(
        reranked=reranked,
        min_reranked=min_reranked,
        min_final_avg=min_final_avg,
        min_final_max=min_final_max,
        score_topn=score_topn,
        timing_put=timing_put,
    )
    if not contract_fail_reason:
        return None

    timing_put("info.contract_fail_reason", contract_fail_reason)
    force_fallback_chat = str(os.getenv("RAG_FORCE_FALLBACK_CHAT", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
    )
    if force_fallback_chat:
        return contract_fail_reason

    raise StrategyViolation(
        error_code="RAG_EMPTY_RESULT_CONTRACT",
        reason=(
            f"reranked result violated contract(reason={contract_fail_reason}, "
            f"reranked={len(reranked)}, min_reranked={min_reranked})"
        ),
    )
