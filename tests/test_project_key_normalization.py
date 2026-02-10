import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rag_parts.filters import build_project_id_filter
from rag_parts.planner_contract import validate_planner_contract
from rag_parts.query_intent import classify_query


class ProjectKeyNormalizationTests(unittest.TestCase):
    def test_planner_contract_lookup_rejects_mixed_project_keys(self):
        violations = validate_planner_contract(
            mode="lookup",
            head="project",
            relation=None,
            target_cols=["ntis_project"],
            ids_map={"pjt_id": ["12345678"], "pjt_no": ["PJT-2024-0001"]},
            relation_target_cols=None,
            join_key_mode=None,
        )
        self.assertTrue(any(v.error_code == "PLANNER_MIXED_PROJECT_KEYS" for v in violations))

    def test_build_project_id_filter_raises_on_mixed_keys(self):
        fake_qmodels = SimpleNamespace()
        with patch("rag_parts.filters.qmodels", fake_qmodels):
            with self.assertRaisesRegex(ValueError, "단일 타입"):
                build_project_id_filter(["12345678"], ["PJT-2024-0001"])

    def test_query_intent_normalize_prefers_single_project_key_type(self):
        q = "pjt_id 12345678 과 과제번호 PJT-2024-0001 조회"
        intent = classify_query(q, q.split())
        has_pjt_id = bool(intent.ids_map.get("pjt_id"))
        has_pjt_no = bool(intent.ids_map.get("pjt_no"))
        self.assertNotEqual(has_pjt_id, has_pjt_no)
        self.assertIn(intent.join_key_mode, ("instance", "group"))


if __name__ == "__main__":
    unittest.main()
