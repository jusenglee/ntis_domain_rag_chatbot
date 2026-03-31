import json
from pathlib import Path


ALLOWED_OUTPUT_TYPES = {"list", "detail", "relation", "comparison", "series", "stats"}
REQUIRED_AXIS_TAGS = {"project", "perf", "people", "org", "followup", "id", "broad_history", "quoted_title", "source_ref", "ordinal"}
ALLOWED_GROUNDEDNESS_STATUSES = {
    "empty_answer",
    "skipped_bypass_kind",
    "no_structured_claims",
    "insufficient_snapshot",
    "supported",
    "unsupported",
    "skipped_fallback",
}


def test_sample_queries_fixture_has_required_fields():
    path = Path("eval/sample_queries.jsonl")
    assert path.exists()

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    assert rows, "fixture must not be empty"

    required = {"id", "question", "axis", "expected_output_type"}
    axis_tags = set()
    for row in rows:
        assert required.issubset(row.keys())
        assert isinstance(row["axis"], list) and row["axis"]
        assert row["expected_output_type"] in ALLOWED_OUTPUT_TYPES
        axis_tags.update(str(value) for value in row["axis"])

    assert REQUIRED_AXIS_TAGS.issubset(axis_tags)


def test_answer_groundedness_fixture_has_required_fields():
    path = Path("eval/answer_groundedness_cases.jsonl")
    assert path.exists()

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    assert rows, "groundedness fixture must not be empty"

    required = {"id", "answer_kind", "answer_text", "snapshot", "expected_status", "expected_reason_codes"}
    seen_ids = set()
    for row in rows:
        assert required.issubset(row.keys())
        assert row["id"] not in seen_ids
        seen_ids.add(row["id"])
        assert isinstance(row["snapshot"], dict)
        assert row["expected_status"] in ALLOWED_GROUNDEDNESS_STATUSES
        assert isinstance(row["expected_reason_codes"], list)
