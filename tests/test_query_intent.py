import unittest

from rag_parts.query_intent import classify_query


class QueryIntentRegressionTests(unittest.TestCase):
    def test_people_project_list_without_relation(self):
        q = "김재수 참여 과제 목록"
        intent = classify_query(q, q.split())
        self.assertEqual(intent.base_route, "project")
        self.assertEqual(intent.action, "list")
        self.assertIsNone(intent.relation)

    def test_people_perf_list_without_relation(self):
        q = "김재수 논문 목록"
        intent = classify_query(q, q.split())
        self.assertEqual(intent.base_route, "perf")
        self.assertEqual(intent.action, "list")
        self.assertIsNone(intent.relation)

    def test_people_project_perf_join(self):
        q = "김재수 연구원의 과제 1개와 해당 과제 성과목록"
        intent = classify_query(q, q.split())
        self.assertEqual(intent.relation, ("project", "perf"))
        self.assertIn(intent.base_route, ("project", "perf"))
        self.assertEqual(intent.action, "list")


if __name__ == "__main__":
    unittest.main()
