from __future__ import annotations

from rag_parts.join import extract_join_keys


class _Point:
    def __init__(self, payload):
        self.payload = payload


def test_extract_join_keys_detects_pjt_id_pjt_no_mixup_instance_mode() -> None:
    points = [
        _Point({"pjt_id": "PNO-2024-001", "pjt_no": "202300001234", "tag": "NAI_PJT_INFO"}),
    ]

    result = extract_join_keys(points, mode="instance", max_ids=10)

    assert result.keys == []
    assert result.invalid_values == ["PNO-2024-001"]
    assert result.suspected_swap_count == 1
    assert result.suspected_swaps[0].swap_candidate == "202300001234"


def test_extract_join_keys_detects_swap_suspect_group_mode() -> None:
    points = [
        _Point({"pjt_no": "***", "pjt_id": "PNO-2024-001", "tag": "NAI_PJT_INFO"}),
    ]

    result = extract_join_keys(points, mode="group", max_ids=10)

    assert result.keys == []
    assert result.invalid_values == ["***"]
    assert result.suspected_swap_count == 1
    assert result.suspected_swaps[0].swap_candidate == "PNO-2024-001"


def test_extract_join_keys_ignores_blank_and_noise_values() -> None:
    points = [
        _Point({"pjt_id": "   ", "pjt_no": "\n\t", "tag": "NAI_PJT_INFO"}),
        _Point({"pjt_id": "***", "pjt_no": " ", "tag": "NAI_PJT_INFO"}),
        _Point({"pjt_id": "202300001234", "pjt_no": "PNO-OK-001", "tag": "NAI_PJT_INFO"}),
    ]

    result = extract_join_keys(points, mode="instance", max_ids=10)

    assert result.keys == ["202300001234"]
    assert result.invalid_values == ["***"]
    assert result.suspected_swap_count == 0
