import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rag_parts.planner_contract import StrategyViolation
from rag_parts.result_contract import enforce_reranked_contract


class RagEmptyResultContractTests(unittest.TestCase):
    def test_empty_reranked_raises_contract_violation(self):
        timings = {"info.contract_fail_reason": ""}

        with self.assertRaises(StrategyViolation) as cm:
            enforce_reranked_contract(
                reranked=[],
                min_reranked=0,
                min_final_avg=0.0,
                min_final_max=0.0,
                score_topn=5,
                timing_put=lambda k, v: timings.__setitem__(k, v),
            )

        self.assertEqual(cm.exception.error_code, "RAG_EMPTY_RESULT_CONTRACT")
        self.assertEqual(timings.get("info.contract_fail_reason"), "no_reranked")

    def test_low_score_raises_contract_violation_without_fallback_chat(self):
        timings = {"info.contract_fail_reason": ""}
        low_score_hit = SimpleNamespace(payload={"_final_total": 0.02})

        with self.assertRaises(StrategyViolation) as cm:
            enforce_reranked_contract(
                reranked=[low_score_hit],
                min_reranked=0,
                min_final_avg=0.1,
                min_final_max=0.0,
                score_topn=3,
                timing_put=lambda k, v: timings.__setitem__(k, v),
            )

        self.assertEqual(cm.exception.error_code, "RAG_EMPTY_RESULT_CONTRACT")
        self.assertEqual(timings.get("info.contract_fail_reason"), "low_score")


    def test_no_reranked_default_does_not_switch_to_chat_fallback(self):
        timings = {"info.contract_fail_reason": ""}

        with patch.dict(os.environ, {"RAG_FORCE_FALLBACK_CHAT": "0"}, clear=False):
            with self.assertRaises(StrategyViolation) as cm:
                enforce_reranked_contract(
                    reranked=[],
                    min_reranked=0,
                    min_final_avg=0.0,
                    min_final_max=0.0,
                    score_topn=5,
                    timing_put=lambda k, v: timings.__setitem__(k, v),
                )

        self.assertEqual(cm.exception.error_code, "RAG_EMPTY_RESULT_CONTRACT")
        self.assertEqual(timings.get("info.contract_fail_reason"), "no_reranked")

    def test_force_fallback_flag_disables_raise_by_default_off(self):
        timings = {"info.contract_fail_reason": ""}

        with patch.dict(os.environ, {"RAG_FORCE_FALLBACK_CHAT": "1"}, clear=False):
            reason = enforce_reranked_contract(
                reranked=[],
                min_reranked=1,
                min_final_avg=0.0,
                min_final_max=0.0,
                score_topn=5,
                timing_put=lambda k, v: timings.__setitem__(k, v),
            )

        self.assertEqual(reason, "no_reranked")
        self.assertEqual(timings.get("info.contract_fail_reason"), "no_reranked")


if __name__ == "__main__":
    unittest.main()
