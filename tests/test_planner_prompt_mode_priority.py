import unittest
from pathlib import Path


class PlannerPromptModePriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server3_source = Path("server3.py").read_text(encoding="utf-8")

    def test_project_perf_relation_rule_precedes_ids_map_rule(self):
        join_rule = 'A) "이 과제의 성과/논문/특허" 또는 "이 성과가 나온 과제" 등 project↔perf relation이 명확하면 => mode="JOIN"'
        ids_rule = 'C) ids_map에 값이 하나라도 있고, A에 해당하지 않으면 => mode="LOOKUP"'

        join_pos = self.server3_source.find(join_rule)
        ids_pos = self.server3_source.find(ids_rule)

        self.assertNotEqual(join_pos, -1, "JOIN 우선 규칙 문구가 누락되었습니다.")
        self.assertNotEqual(ids_pos, -1, "ids_map LOOKUP 규칙 문구가 누락되었습니다.")
        self.assertLess(join_pos, ids_pos, "project↔perf relation 규칙이 ids_map 규칙보다 앞서야 합니다.")

    def test_snapshot_examples_for_join_and_lookup(self):
        join_example = '"1711015550 과제의 논문/특허" => mode="JOIN" (project↔perf relation 명확)'
        lookup_example = '"1711015550 과제 상세" => mode="LOOKUP" (단순 ID 상세 조회)'

        self.assertIn(join_example, self.server3_source)
        self.assertIn(lookup_example, self.server3_source)


if __name__ == "__main__":
    unittest.main()
