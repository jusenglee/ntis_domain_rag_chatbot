from pathlib import Path


def test_planner_prompt_mentions_role_separated_org_slots() -> None:
    source = Path("server3.py").read_text(encoding="utf-8")

    assert "lead_org_name" in source
    assert "participant_org_name" in source
    assert "people_affiliation_org_name" in source
    assert "역할이 명확하면 해당 슬롯 외 나머지 두 슬롯은 반드시 []" in source
