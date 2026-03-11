from pathlib import Path


def test_planner_stagewise_env_default_is_true() -> None:
    source = Path("server3.py").read_text(encoding="utf-8")
    expected = 'PLANNER_STAGEWISE_ENABLED = os.getenv("PLANNER_STAGEWISE_ENABLED", "true")'
    assert expected in source


def test_legacy_prompt_contains_full_contract_sections() -> None:
    prompt = Path("prompts/planner_legacy_v2.md").read_text(encoding="utf-8")
    for required in [
        "[고정 계약]",
        "필수 키",
        "[정책]",
        "JOIN 계약",
        "[예시]",
    ]:
        assert required in prompt
