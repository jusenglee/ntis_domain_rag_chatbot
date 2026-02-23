from pathlib import Path


def test_question_analysis_validator_contains_lookup_guard() -> None:
    source = Path("server3.py").read_text(encoding="utf-8")

    assert "if self.mode == \"SEARCH\":" in source
    assert "participant_researcher_name" in source
    assert "lead_org_name" in source
    assert "people_affiliation_org_name" in source
    assert "object.__setattr__(self, \"mode\", \"LOOKUP\")" in source
