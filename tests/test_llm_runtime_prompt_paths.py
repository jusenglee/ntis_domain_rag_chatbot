import asyncio
from pathlib import Path

from apps.api.services.llm_runtime import load_prompt_file, resolve_system_prompt_path


def test_resolve_system_prompt_path_uses_gemma_override():
    assert resolve_system_prompt_path(
        model_name="gemma_triton_0",
        default_path=Path("prompts/ntis_chatbot.md"),
        gemma_path=Path("prompts/ntis_chatbot_gemma.md"),
        solar_path=Path("prompts/ntis_chatbot_solar.md"),
    ) == Path("prompts/ntis_chatbot_gemma.md")


def test_resolve_system_prompt_path_uses_solar_override():
    assert resolve_system_prompt_path(
        model_name="solar_vllm_0",
        default_path=Path("prompts/ntis_chatbot.md"),
        gemma_path=Path("prompts/ntis_chatbot_gemma.md"),
        solar_path=Path("prompts/ntis_chatbot_solar.md"),
    ) == Path("prompts/ntis_chatbot_solar.md")


def test_resolve_system_prompt_path_falls_back_to_default():
    assert resolve_system_prompt_path(
        model_name="gemma_triton_0",
        default_path=Path("prompts/ntis_chatbot.md"),
        gemma_path=None,
        solar_path=None,
    ) == Path("prompts/ntis_chatbot.md")


def test_load_prompt_file_strips_leading_bom(tmp_path):
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("\ufeffsystem prompt", encoding="utf-8")

    content = asyncio.run(load_prompt_file(prompt_path))

    assert content == "system prompt"
