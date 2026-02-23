from pathlib import Path


def test_apply_planner_v2_maps_year_and_perf_fields() -> None:
    source = Path("server3.py").read_text(encoding="utf-8")
    assert "planner_year_from" in source
    assert "planner_year_to" in source
    assert "planner_perf_types" in source
    assert "year_from=planner_year_from" in source
    assert "perf_types=planner_perf_types" in source
