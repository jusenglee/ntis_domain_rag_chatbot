import unittest
from types import SimpleNamespace
from rag_parts.promotion import promote_mode_from_search_hits


class SearchPromotionTests(unittest.TestCase):
    def _hit(self, *, pjt_id=None, pjt_no=None):
        payload = {"meta_basic": {}}
        if pjt_id is not None:
            payload["meta_basic"]["pjt_id"] = pjt_id
        if pjt_no is not None:
            payload["meta_basic"]["pjt_no"] = pjt_no
        return {"payload": payload}

    def test_promote_lookup_group_when_history_with_pjt_no(self) -> None:
        strategy = SimpleNamespace(mode="search", action="list", relation=None, query_text="과제 이력 전체 보여줘")
        out = promote_mode_from_search_hits(
            current_mode="search",
            search_hits=[self._hit(pjt_no="PJTNO-2024-001")],
            ids_map={},
            planner_strategy=strategy,
        )

        self.assertEqual(out["mode"], "lookup")
        self.assertEqual(out["kind"], "group")
        self.assertEqual(out["reason"], "pjt_no_history")
        self.assertTrue(out["ids_map"].get("pjt_no"))

    def test_promote_lookup_instance_when_detail_with_pjt_id(self) -> None:
        strategy = SimpleNamespace(mode="search", action="detail", relation=None, query_text="특정 시행 상세 정보")
        out = promote_mode_from_search_hits(
            current_mode="search",
            search_hits=[self._hit(pjt_id="1711195604")],
            ids_map={},
            planner_strategy=strategy,
        )

        self.assertEqual(out["mode"], "lookup")
        self.assertEqual(out["kind"], "instance")
        self.assertEqual(out["reason"], "pjt_id_detail")
        self.assertIn("1711195604", out["ids_map"].get("pjt_id", []))

    def test_promote_join_when_relation_request_with_join_key(self) -> None:
        strategy = SimpleNamespace(mode="search", action="relation", relation="project_perf", query_text="이 과제 성과 보여줘")
        out = promote_mode_from_search_hits(
            current_mode="search",
            search_hits=[self._hit(pjt_id="1711195604")],
            ids_map={},
            planner_strategy=strategy,
        )

        self.assertEqual(out["mode"], "join")
        self.assertEqual(out["kind"], "join")
        self.assertEqual(out["reason"], "relation_join_key")

    def test_do_not_promote_when_planner_mode_fixed_lookup(self) -> None:
        strategy = SimpleNamespace(mode="lookup", action="detail", relation=None, query_text="상세")
        out = promote_mode_from_search_hits(
            current_mode="search",
            search_hits=[self._hit(pjt_id="1711195604")],
            ids_map={},
            planner_strategy=strategy,
        )

        self.assertEqual(out["mode"], "search")
        self.assertFalse(out["allowed"].get("lookup"))


if __name__ == "__main__":
    unittest.main()
