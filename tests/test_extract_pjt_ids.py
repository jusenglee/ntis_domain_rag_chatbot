import unittest

from rag_parts.join import extract_pjt_ids


class ExtractPjtIdsTests(unittest.TestCase):
    def test_extract_from_top_level_and_meta_paths(self) -> None:
        points = [
            {"payload": {"pjt_id": " 1234567890 "}},
            {"payload": {"meta_basic": {"pjt_id": "ntis:2345678901"}}},
            {"payload": {"meta_detail": {"pjt_id": "3456789012"}}},
            {"payload": {"pjt_id": "1234567890"}},  # duplicate
        ]

        got = extract_pjt_ids(points)

        self.assertEqual(got, ["1234567890", "2345678901", "3456789012"])


    def test_extracts_pjt_id_and_pjt_no_together_when_both_exist(self) -> None:
        points = [
            {"payload": {"pjt_id": "4567890123", "pjt_no": "PJT-2025-123"}},
            {"payload": {"meta_basic": {"pjt_id": "5678901234", "pjt_no": "BASIC-2025-01"}}},
            {"payload": {"meta_detail": {"pjt_id": "6789012345", "pjt_no": "DETAIL-2025-99"}}},
        ]

        pjt_ids, pjt_nos = extract_pjt_ids(points, include_pjt_no_fallback=True)

        self.assertEqual(pjt_ids, ["4567890123", "5678901234", "6789012345"])
        self.assertEqual(pjt_nos, ["PJT-2025-123", "BASIC-2025-01", "DETAIL-2025-99"])

    def test_extract_returns_pjt_no_fallback_separately(self) -> None:
        points = [
            {"payload": {"meta_basic": {"pjt_id": "1111222233"}}},
            {"payload": {"pjt_no": "PJT-2024-001"}},
            {"payload": {"meta_detail": {"pjt_no": "2024-XYZ-77"}}},
            {"payload": {"meta_basic": {"pjt_no": "PJT-2024-001"}}},  # duplicate
        ]

        pjt_ids, pjt_nos = extract_pjt_ids(points, include_pjt_no_fallback=True)

        self.assertEqual(pjt_ids, ["1111222233"])
        self.assertEqual(pjt_nos, ["PJT-2024-001", "2024-XYZ-77"])


if __name__ == "__main__":
    unittest.main()
