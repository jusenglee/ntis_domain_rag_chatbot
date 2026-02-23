from pathlib import Path


def test_planner_prompt_includes_year_perf_title_filter_fields() -> None:
    source = Path("server3.py").read_text(encoding="utf-8")
    assert "year_from / year_to" in source
    assert "perf_types" in source
    assert "title_terms" in source
