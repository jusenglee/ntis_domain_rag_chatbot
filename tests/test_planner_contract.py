import unittest

from rag_parts.planner_contract import planner_contract_mode, normalize_lookup_filter_policy


class PlannerContractTests(unittest.TestCase):
    def test_join_mode_is_kept_even_when_action_mismatch(self) -> None:
        mode, errors = planner_contract_mode(
            strategy_mode="join",
            strategy_action="list",
            strategy_relation=("project", "perf"),
            fallback_mode="search",
        )

        self.assertEqual(mode, "join")
        self.assertNotIn("action_mode_mismatch:list->join", errors)

    def test_lookup_mode_is_kept_even_when_action_mismatch(self) -> None:
        mode, errors = planner_contract_mode(
            strategy_mode="lookup",
            strategy_action="topic",
            strategy_relation=None,
            fallback_mode="search",
        )

        self.assertEqual(mode, "lookup")
        self.assertIn("action_mode_mismatch:topic->lookup", errors)



    def test_lookup_filter_policy_must_one_then_should(self) -> None:
        self.assertEqual(normalize_lookup_filter_policy("must_one_then_should"), "must_one_then_should")
        self.assertIsNone(normalize_lookup_filter_policy("unknown"))

if __name__ == "__main__":
    unittest.main()
