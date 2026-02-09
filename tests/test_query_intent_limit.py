import unittest

from rag_parts.query_intent import classify_query


class QueryIntentLimitExtractionTests(unittest.TestCase):
    def test_limit_from_show_request(self):
        intent = classify_query("과제 1개 보여줘", [])
        self.assertEqual(intent.planner_limit, 1)

    def test_limit_from_max_perf_request(self):
        intent = classify_query("최대 3개 성과", [])
        self.assertEqual(intent.planner_limit, 3)

    def test_limit_not_set_for_year_span_expression(self):
        intent = classify_query("최근 3개년 과제", [])
        self.assertIsNone(intent.planner_limit)

    def test_planner_hint_limit_has_priority(self):
        intent = classify_query("과제 1개 보여줘", [], hint={"limit": 7})
        self.assertEqual(intent.planner_limit, 7)


if __name__ == "__main__":
    unittest.main()
