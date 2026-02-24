from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from server3 import refine_documents_rule_based


def test_refine_documents_rule_based_formats_aggregation_docs() -> None:
    docs = [
        {
            "source_type": "aggregation",
            "source_index": 1,
            "metric": "project_participation_count",
            "candidate_docs": 15,
            "window_years": {"from": "2020", "to": "2024"},
            "rank_item": {
                "hm_nm": "홍길동",
                "project_participation_count": 7,
                "performance_count": 3,
            },
        }
    ]

    text = refine_documents_rule_based(docs, relax_limits=True)
    assert "홍길동" in text
    assert "project_participation_count: 7" in text
    assert "candidate_docs: 15" in text
