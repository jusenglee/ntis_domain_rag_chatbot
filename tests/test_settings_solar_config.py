from __future__ import annotations

from settings import SolarVLLMConfig


def test_solar_vllm_config_from_env_valid(monkeypatch):
    monkeypatch.setenv("SOLAR_VLLM_MODEL", "solar")
    monkeypatch.setenv("SOLAR_VLLM_BASE_URL", "http://localhost:8010/v1")
    monkeypatch.setenv("SOLAR_VLLM_API_KEY", "EMPTY")
    monkeypatch.setenv("SOLAR_VLLM_TIMEOUT", "30")

    cfg = SolarVLLMConfig.from_env()

    assert cfg.model_name == "solar"
    assert cfg.base_url == "http://localhost:8010/v1"
    assert cfg.timeout == 30.0


def test_solar_vllm_config_from_env_invalid_timeout(monkeypatch):
    monkeypatch.setenv("SOLAR_VLLM_TIMEOUT", "not-a-number")

    try:
        SolarVLLMConfig.from_env()
    except ValueError as exc:
        assert "SOLAR_VLLM_TIMEOUT" in str(exc)
    else:
        raise AssertionError("ValueError expected")
