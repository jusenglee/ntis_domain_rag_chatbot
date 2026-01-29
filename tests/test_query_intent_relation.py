import unittest

from rag_parts.constants import COL_PERF, COL_PROJECT, TAG_PJT_INFO
from rag_parts.filters import extract_org_terms as extract_org_terms_filters
from rag_parts.query_intent import (
    classify_query,
    get_relation_route,
    extract_org_terms as extract_org_terms_intent,
    relation_target_collections,
)


class QueryIntentRelationTests(unittest.TestCase):
    def test_people_project_relation_route(self) -> None:
        q = "김철수 연구자의 과제 참여 이력"
        kws = q.split()
        intent = classify_query(q, kws)

        self.assertEqual(intent.base_route, "project")
        self.assertEqual(intent.relation, ("project", "people"))
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
        self.assertEqual(
            relation_target_collections(intent.relation),
            list(dict.fromkeys([COL_PROJECT, COL_PERF])),
        )

    def test_extract_org_terms_excludes_task_only_query(self) -> None:
        q = "과제"
        kws = q.split()

        self.assertEqual(extract_org_terms_intent(q, kws), [])
        self.assertEqual(extract_org_terms_filters(q, kws), [])

    def test_org_cues_include_business_registration_lookup(self) -> None:
        q = "사업자등록번호로 기관 조회"
        kws = q.split()
        intent = classify_query(q, kws)

        self.assertEqual(intent.base_route, "org")
        self.assertEqual(intent.action, "list")

    def test_org_name_with_researcher_suffix_does_not_trigger_people(self) -> None:
        q = "농업생명과학연구원 과제 상세 정보"
        kws = q.split()
        intent = classify_query(q, kws)

        self.assertNotEqual(intent.base_route, "people")
        self.assertEqual(intent.people_terms, [])


if __name__ == "__main__":
    unittest.main()
