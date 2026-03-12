from pathlib import Path


def test_planner_pipeline_default_is_staged() -> None:
    source = Path("server3.py").read_text(encoding="utf-8")
    assert 'RAG_PLANNER_PIPELINE = str(os.getenv("RAG_PLANNER_PIPELINE", "staged") or "staged").strip().lower()' in source
    assert 'PLANNER_SCHEMA_VERSION = "v3-staged" if RAG_PLANNER_PIPELINE != "legacy" else "v2"' in source
    assert 'RAG_PLANNER_PIPELINE != "legacy"' in source


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
