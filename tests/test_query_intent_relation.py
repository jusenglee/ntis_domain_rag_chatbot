import unittest

from rag_parts.constants import COL_PERF, COL_PROJECT, TAG_PJT_INFO
from rag_parts.query_intent import (
    classify_query,
    get_relation_route,
    relation_target_collections,
)


class QueryIntentRelationTests(unittest.TestCase):
    def test_people_project_relation_route(self) -> None:
        q = "김철수 연구자의 과제 참여 이력"
        kws = q.split()
        intent = classify_query(q, kws)

        self.assertEqual(intent.base_route, "people")
        self.assertEqual(intent.relation, ("people", "project"))
        self.assertEqual(intent.action, "relation")
        self.assertIn(TAG_PJT_INFO, intent.project_tag_filters)

        route = get_relation_route(intent.relation)
        self.assertIsNotNone(route)
        self.assertEqual(route.hop1_col, COL_PROJECT)
        self.assertEqual(route.hop2_col, COL_PROJECT)
        self.assertEqual(route.hop1_tag_filters, [TAG_PJT_INFO])
        self.assertEqual(route.hop2_tag_filters, [TAG_PJT_INFO])
        self.assertEqual(set(relation_target_collections(intent.relation)), {COL_PROJECT})

    def test_project_perf_relation_route(self) -> None:
        q = "과제의 논문 성과 목록"
        kws = q.split()
        intent = classify_query(q, kws)

        self.assertEqual(intent.base_route, "project")
        self.assertEqual(intent.relation, ("project", "perf"))
        self.assertEqual(intent.action, "list")

        route = get_relation_route(intent.relation)
        self.assertIsNotNone(route)
        self.assertEqual(route.hop1_col, COL_PROJECT)
        self.assertEqual(route.hop2_col, COL_PERF)
        self.assertEqual(route.hop1_tag_filters, [TAG_PJT_INFO])
        self.assertIsNone(route.hop2_tag_filters)
        self.assertEqual(relation_target_collections(intent.relation), [COL_PROJECT, COL_PERF])


if __name__ == "__main__":
    unittest.main()
