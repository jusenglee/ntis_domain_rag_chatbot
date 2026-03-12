from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag_parts.pipeline_steps import normalize_intent
from rag_parts.query_intent import QueryIntent


def test_normalize_intent_applies_extended_hint_slots() -> None:
    intent = QueryIntent(
        base_route="project",
        relation=None,
        intent="filter",
        action="list",
        is_id_query=False,
        long_query=False,
        rare_ratio=0.0,
        years=["2019"],
        perf_types=["논문"],
        title=["기존제목"],
    )

    normalized = normalize_intent(
        intent,
        query="2022 ETRI 논문 제목 테스트",
        keywords=[],
        allow_strategy_fallback=False,
        hint_people_terms=["홍길동"],
        hint_org_terms=["ETRI"],
        hint_org_role="participant",
        hint_lead_org_terms=[],
        hint_participant_org_terms=["ETRI"],
        hint_people_affiliation_org_terms=[],
        hint_years=["2022", "2023"],
        hint_perf_types=["논문"],
        hint_title_terms=["차세대 통신"],
    )

    assert normalized.people_terms == ["홍길동"]
    assert "ETRI" in normalized.org_terms
    assert normalized.org_role == "participant"
    assert "ETRI" in normalized.participant_org_terms
    assert normalized.years == ["2022", "2023"]
    assert normalized.perf_types
    assert any("PAPER" in perf_type.upper() for perf_type in normalized.perf_types)
    assert normalized.title == ["차세대 통신"]
