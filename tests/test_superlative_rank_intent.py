from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from rag_parts.query_intent import classify_query
from rag_parts.pipeline_steps import normalize_intent


def test_people_superlative_sets_wants_rank_and_stats_action() -> None:
    intent = classify_query(
        "가장 많은 과제를 수행한 연구자",
        ["가장", "많은", "과제", "수행", "연구자"],
        hint={"head": "people"},
    )
    assert intent.wants_rank is True
    assert intent.base_route == "people"
    assert intent.action == "stats"


def test_org_superlative_hint_rank_is_normalized_to_stats() -> None:
    intent = classify_query(
        "상위 기관 알려줘",
        ["상위", "기관"],
        hint={"head": "org", "action": "rank", "output_type": "rank", "wants_rank": True},
    )
    normalized = normalize_intent(intent, query="상위 기관 알려줘", keywords=["상위", "기관"])
    assert intent.wants_rank is True
    assert intent.action == "stats"
    assert normalized.action == "stats"
    assert normalized.output_type == "stats"


def test_normalize_intent_sets_default_stats_policy() -> None:
    intent = classify_query(
        "가장 많은 과제를 수행한 연구자",
        ["가장", "많은", "과제", "수행", "연구자"],
        hint={"head": "people"},
    )
    normalized = normalize_intent(intent, query="가장 많은 과제를 수행한 연구자", keywords=["연구자"])
    assert normalized.stats_metric == "project_participation_count"
    assert normalized.window_years == 3
    assert normalized.candidate_n == 50
    assert normalized.top_k == 1


def test_people_superlative_queries_keep_people_head_and_stats_contract() -> None:
    queries = [
        "가장 활발한 연구자",
        "Top 3 연구자",
        "최근 5년 최다 참여 연구자",
    ]

    for query in queries:
        intent = classify_query(
            query,
            query.split(),
            hint={"head": "people"},
        )
        normalized = normalize_intent(intent, query=query, keywords=query.split())

        assert intent.base_route == "people"
        assert normalized.base_route == "people"
        assert normalized.wants_rank is True
        assert normalized.action in {"stats", "rank"}
        assert normalized.output_type in {"stats", "rank"}
        assert normalized.action == normalized.output_type
