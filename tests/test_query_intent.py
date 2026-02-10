import unittest
from pathlib import Path

from rag_parts.query_intent import classify_query, extract_org_terms


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

    def test_extract_org_terms_with_etri(self):
        q = "ETRI 수행 과제"
        terms = extract_org_terms(q, q.split())
        self.assertGreaterEqual(len(terms), 1)

    def test_extract_org_terms_with_kaist(self):
        q = "KAIST 참여 성과"
        terms = extract_org_terms(q, q.split())
        self.assertGreaterEqual(len(terms), 1)


class PlannerActionDetailLookupRegressionTests(unittest.TestCase):
    def test_detail_action_maps_to_lookup_mode(self):
        src = Path("rag_pipeline.py").read_text(encoding="utf-8")
        lookup_line = 'if action_value in ("list", "stats", "download", "id_exact", "id_fuzzy", "detail"):'
        self.assertIn(lookup_line, src)


class KoreanCategoryCanonicalRouteTests(unittest.TestCase):
    def test_korean_category_org_maps_to_org_route(self):
        intent = classify_query("연관 도메인 질의 샘플", ["연관", "도메인", "질의", "샘플"], hint={"categories": ["기관"]})
        self.assertEqual(intent.base_route, "org")

    def test_korean_category_researcher_maps_to_people_route(self):
        intent = classify_query("연관 도메인 질의 샘플", ["연관", "도메인", "질의", "샘플"], hint={"categories": ["연구자"]})
        self.assertEqual(intent.base_route, "people")

    def test_korean_category_performance_maps_to_perf_route(self):
        intent = classify_query("연관 도메인 질의 샘플", ["연관", "도메인", "질의", "샘플"], hint={"categories": ["성과"]})
        self.assertEqual(intent.base_route, "perf")


if __name__ == "__main__":
    unittest.main()
