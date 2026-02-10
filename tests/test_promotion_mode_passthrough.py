import unittest

from rag_parts.promotion import promote_mode_from_search_hits


class PromotionModePassthroughTests(unittest.TestCase):
    def test_search_mode_is_not_promoted_even_with_project_hits(self):
        hits = [
            {"payload": {"pjt_id": "PJT-001", "pjt_no": "NO-001"}},
            {"payload": {"project": {"pjt_id": "PJT-002"}}},
        ]
        result = promote_mode_from_search_hits(
            current_mode="search",
            search_hits=hits,
            ids_map={"pjt_id": ["PJT-BASE"]},
            planner_strategy={"mode": "search", "action": "relation"},
        )

        self.assertEqual(result["mode"], "search")
        self.assertEqual(result["reason"], "promotion_disabled_passthrough")
        self.assertEqual(result["ids_map"], {"pjt_id": ["PJT-BASE"]})

    def test_non_search_mode_is_also_passthrough(self):
        result = promote_mode_from_search_hits(
            current_mode="lookup",
            search_hits=[{"payload": {"pjt_id": "PJT-999"}}],
            ids_map={"pjt_no": ["NO-BASE"]},
            planner_strategy={"mode": "lookup"},
        )

        self.assertEqual(result["mode"], "lookup")
        self.assertEqual(result["ids_map"], {"pjt_no": ["NO-BASE"]})


if __name__ == "__main__":
    unittest.main()
