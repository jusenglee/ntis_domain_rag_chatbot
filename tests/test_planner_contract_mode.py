import unittest

from rag_parts.planner_contract import planner_contract_mode


class PlannerContractModeTests(unittest.TestCase):
    def test_detail_action_lookup_mode_is_valid(self):
        mode, errors = planner_contract_mode(
            strategy_mode="lookup",
            strategy_action="detail",
            strategy_relation=None,
            fallback_mode=None,
        )
        self.assertEqual(mode, "lookup")
        self.assertEqual(errors, [])

    def test_detail_action_search_mode_is_mismatch(self):
        mode, errors = planner_contract_mode(
            strategy_mode="search",
            strategy_action="detail",
            strategy_relation=None,
            fallback_mode=None,
        )
        self.assertEqual(mode, "search")
        self.assertIn("action_mode_mismatch:detail->search", errors)


if __name__ == "__main__":
    unittest.main()
