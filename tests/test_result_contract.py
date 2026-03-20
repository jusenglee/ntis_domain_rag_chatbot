import pytest

from apps.core.result_contract import enforce_reranked_contract
from apps.core.planner_contract import StrategyViolation


def test_enforce_reranked_contract_returns_no_result_for_lookup_empty_hits():
    timings = {}

    reason = enforce_reranked_contract(
        reranked=[],
        min_reranked=4,
        min_final_avg=0.0,
        min_final_max=0.0,
        score_topn=5,
        timing_put=lambda key, value: timings.__setitem__(key, value),
        mode="lookup",
    )

    assert reason == "no_reranked"
    assert timings["info.empty_result_policy"] == "normal_no_result"
    assert timings["info.contract_fail_reason"] == "no_reranked"


def test_enforce_reranked_contract_keeps_strict_search_empty_hits():
    with pytest.raises(StrategyViolation) as exc_info:
        enforce_reranked_contract(
            reranked=[],
            min_reranked=4,
            min_final_avg=0.0,
            min_final_max=0.0,
            score_topn=5,
            timing_put=lambda key, value: None,
            mode="search",
        )

    assert exc_info.value.error_code == "RAG_EMPTY_RESULT_CONTRACT"
