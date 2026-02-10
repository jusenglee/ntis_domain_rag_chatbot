import unittest

from rag_parts.pipeline_steps import normalize_intent
from rag_parts.query_intent import QueryIntent, classify_query


class JoinKeyModeNormalizationTests(unittest.TestCase):
    def _base_intent(self, *, join_key_mode, ids_map):
        return QueryIntent(
            base_route="project",
            relation=("project", "perf"),
            intent="filter",
            action="relation",
            is_id_query=True,
            long_query=False,
            rare_ratio=0.0,
            ids_map=ids_map,
            ids_flat=[],
            join_key_mode=join_key_mode,
        )

    def test_normalize_intent_coerces_group_without_pjt_no_to_instance(self):
        raw_intent = self._base_intent(join_key_mode="group", ids_map={"pjt_id": ["1711015550"]})

        normalized = normalize_intent(raw_intent, query="1711015550 성과", keywords=["1711015550", "성과"])

        self.assertEqual(normalized.join_key_mode, "instance")
        self.assertIn("JOIN_KEY_MODE_GROUP_WITHOUT_PJT_NO_COERCED_TO_INSTANCE", normalized.parsing_warnings)

    def test_normalize_intent_marks_instance_with_pjt_no_only_as_contract_violation(self):
        raw_intent = self._base_intent(join_key_mode="instance", ids_map={"pjt_no": ["PJT-2020-XXXX"]})

        normalized = normalize_intent(raw_intent, query="PJT-2020-XXXX 성과", keywords=["PJT-2020-XXXX", "성과"])

        self.assertEqual(normalized.join_key_mode, "instance")
        self.assertIn("JOIN_KEY_MODE_INSTANCE_WITH_PJT_NO_ONLY", normalized.contract_violations)

    def test_classify_query_pjt_no_only_sets_group_mode(self):
        intent = classify_query("과제번호 PJT-2020-XXXX 성과 전체", ["과제번호", "PJT-2020-XXXX", "성과", "전체"])

        self.assertTrue(intent.ids_map.get("pjt_no"))
        self.assertEqual(intent.join_key_mode, "group")

    def test_classify_query_pjt_id_only_sets_instance_mode(self):
        intent = classify_query("1711015550 성과 목록", ["1711015550", "성과", "목록"])

        self.assertTrue(intent.ids_map.get("pjt_id"))
        self.assertEqual(intent.join_key_mode, "instance")


if __name__ == "__main__":
    unittest.main()
