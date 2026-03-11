from pathlib import Path


def test_planner_bind_has_deterministic_sampling_and_max_tokens() -> None:
    src = Path("server3.py").read_text(encoding="utf-8")
    assert "PLANNER_TEMPERATURE = 0.0" in src
    assert "PLANNER_TOP_P = 1.0" in src
    assert "PLANNER_MAX_TOKENS = 450" in src
    assert "temperature=PLANNER_TEMPERATURE" in src
    assert "top_p=PLANNER_TOP_P" in src
    assert "max_tokens=PLANNER_MAX_TOKENS" in src
