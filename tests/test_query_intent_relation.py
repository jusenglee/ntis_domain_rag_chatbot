import os
import unittest

os.environ.setdefault("QUERY_INTENT_USE_LLM", "0")

from rag_parts.constants import COL_PERF, COL_PROJECT, TAG_PJT_INFO
from rag_parts.planner_contract import planner_contract_mode
from rag_parts.filters import extract_org_terms as extract_org_terms_filters
from rag_parts.pipeline_steps import normalize_intent
from rag_parts.query_intent import (
    classify_query,
    get_relation_route,
    extract_org_terms as extract_org_terms_intent,
    pick_domain_hint_from_categories,
    relation_target_collections,
    QueryIntent,
)
from server3 import (
    _build_planner_override_request,
    QuestionAnalysis,
)


class QueryIntentRelationTests(unittest.TestCase):
    def test_people_project_relation_route(self) -> None:
        q = "김철수 연구자의 과제 참여 이력"
        kws = q.split()
        intent = classify_query(q, kws)

        self.assertEqual(intent.base_route, "project")
        self.assertIsNone(intent.relation)
        self.assertEqual(intent.action, "list")
        self.assertIn(TAG_PJT_INFO, intent.project_tag_filters)

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

    def test_pick_domain_hint_from_categories_org(self) -> None:
        hint = pick_domain_hint_from_categories(["organization"])

        self.assertEqual(hint, "org")

    def test_normalize_intent_uses_hint_org_terms(self) -> None:
        intent = QueryIntent(
            base_route="project",
            relation=None,
            intent="content",
            action="content",
            is_id_query=False,
            long_query=False,
            rare_ratio=0.0,
        )

        normalized = normalize_intent(
            intent,
            query="농업생명과학연구원 과제",
            keywords=["농업생명과학연구원", "과제"],
            hint_org_terms=["농업생명과학연구원"],
        )

        self.assertEqual(normalized.org_terms, ["농업생명과학연구원"])


    def test_planner_contract_lookup_mode_is_not_overridden(self) -> None:
        mode, errors = planner_contract_mode(
            strategy_mode="lookup",
            strategy_action="topic",
            strategy_relation=None,
            fallback_mode="search",
        )

        self.assertEqual(mode, "lookup")
        self.assertIn("action_mode_mismatch:topic->lookup", errors)

    def test_build_planner_override_request_when_mode_and_action_conflict(self) -> None:
        intent = QueryIntent(
            base_route="project",
            relation=None,
            intent="content",
            action="topic",
            is_id_query=False,
            long_query=False,
            rare_ratio=0.0,
        )
        analysis = QuestionAnalysis(
            strategy_version="v2",
            retrieval_query="",
            confidence=1.0,
            mode="LOOKUP",
            head="project",
            action="topic",
        )

        override_request = _build_planner_override_request(analysis, intent)

        self.assertIsNotNone(override_request)
        self.assertEqual(override_request["requested_mode"], "lookup")
        self.assertEqual(override_request["current_action"], "topic")

    def test_build_planner_override_request_when_mode_matches_action(self) -> None:
        intent = QueryIntent(
            base_route="project",
            relation=None,
            intent="content",
            action="list",
            is_id_query=False,
            long_query=False,
            rare_ratio=0.0,
        )
        analysis = QuestionAnalysis(
            strategy_version="v2",
            retrieval_query="",
            confidence=1.0,
            mode="LOOKUP",
            head="project",
            action="topic",
        )

        override_request = _build_planner_override_request(analysis, intent)

        self.assertIsNone(override_request)


if __name__ == "__main__":
    unittest.main()
