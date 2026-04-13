import os
from typing import Any, Callable, Optional

from apps.planner.planner_contract import StrategyViolation
from apps.retrieval.result_policy import ResultContractOutcome


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


def resolve_reranked_outcome(
        *,
        reranked: list[Any],
        min_reranked: int,
        min_final_avg: float,
        min_final_max: float,
        score_topn: int,
        timing_put: Callable[[str, Any], None],
        mode: str | None = None,
) -> ResultContractOutcome:
    normalized_mode = str(mode or "").strip().lower() or None
    empty_result_policy = "strict_search"
    timing_put("info.reranked_count", int(len(reranked or [])))

    contract_fail_reason = evaluate_reranked_contract(
        reranked=reranked,
        min_reranked=min_reranked,
        min_final_avg=min_final_avg,
        min_final_max=min_final_max,
        score_topn=score_topn,
        timing_put=timing_put,
    )
    if not contract_fail_reason:
        timing_put("info.empty_result_policy", empty_result_policy)
        return ResultContractOutcome(status="ok", empty_result_policy=empty_result_policy)

    if normalized_mode in {"lookup", "join"} and contract_fail_reason in {"no_reranked", "insufficient_hits", "low_score"}:
        empty_result_policy = "normal_no_result"
        timing_put("info.empty_result_policy", empty_result_policy)
        timing_put("info.contract_fail_reason", contract_fail_reason)
        status = "normal_no_result" if contract_fail_reason == "no_reranked" else contract_fail_reason
        return ResultContractOutcome(status=status, empty_result_policy=empty_result_policy, reason=contract_fail_reason)

    timing_put("info.empty_result_policy", empty_result_policy)
    timing_put("info.contract_fail_reason", contract_fail_reason)
    force_fallback_chat = str(os.getenv("RAG_FORCE_FALLBACK_CHAT", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
    )
    if force_fallback_chat:
        return ResultContractOutcome(status="normal_no_result", empty_result_policy=empty_result_policy, reason=contract_fail_reason)

    return ResultContractOutcome(status="contract_violation", empty_result_policy=empty_result_policy, reason=contract_fail_reason)


def enforce_reranked_contract(
        *,
        reranked: list[Any],
        min_reranked: int,
        min_final_avg: float,
        min_final_max: float,
        score_topn: int,
        timing_put: Callable[[str, Any], None],
        mode: str | None = None,
) -> Optional[str]:
    outcome = resolve_reranked_outcome(
        reranked=reranked,
        min_reranked=min_reranked,
        min_final_avg=min_final_avg,
        min_final_max=min_final_max,
        score_topn=score_topn,
        timing_put=timing_put,
        mode=mode,
    )
    if outcome.status == "ok":
        return None
    if outcome.status in {"normal_no_result", "insufficient_hits", "low_score"}:
        return outcome.reason or outcome.status

    raise StrategyViolation(
        error_code="RAG_EMPTY_RESULT_CONTRACT",
        reason=(
            f"reranked result violated contract(reason={outcome.reason}, "
            f"reranked={len(reranked)}, min_reranked={min_reranked}, empty_result_policy={outcome.empty_result_policy})"
        ),
    )
