from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag_parts.join import extract_join_keys, is_valid_join_key


def test_instance_join_key_accepts_non_numeric_value() -> None:
    assert is_valid_join_key("PJT-ABC-001", mode="instance") is True


def test_extract_join_keys_instance_does_not_mark_non_numeric_as_invalid() -> None:
    points = [
        {"payload": {"pjt_id": "PJT-ABC-001", "pjt_no": "12345678"}},
        {"payload": {"pjt_id": "1711000001", "pjt_no": "A-2024-0001"}},
    ]

    result = extract_join_keys(points, mode="instance", max_ids=10)

    assert result.keys == ["PJT-ABC-001", "1711000001"]
    assert result.invalid_values == []
    assert result.suspected_swap_count == 0
