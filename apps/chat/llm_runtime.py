from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

from apps.platform.settings import SOLAR_VLLM_CONFIG

_LLM_CACHE: Dict[str, Any] = {}
_PROMPT_CACHE: dict[tuple[str, float], str] = {}


def build_llm(*, model_name: str) -> Any:
    """Build or reuse the chat model adapter for the requested model name."""

    from apps.platform.openai_compat_llm import OpenAICompatChatModel
    from apps.platform.triton_llm import TritonChatModel

    cached = _LLM_CACHE.get(model_name)
    if cached is not None:
        return cached

    if model_name == "solar_vllm_0":
        llm = OpenAICompatChatModel(
            model_name=SOLAR_VLLM_CONFIG.model_name,
            base_url=SOLAR_VLLM_CONFIG.base_url,
            api_key=SOLAR_VLLM_CONFIG.api_key,
            timeout=SOLAR_VLLM_CONFIG.timeout,
        )
    else:
        llm = TritonChatModel(model_name=model_name)

    _LLM_CACHE[model_name] = llm
    return llm


async def load_prompt_file(path: Path) -> str:
    """Read a UTF-8 prompt file and cache by modification time."""

    stat = path.stat()
    cache_key = (str(path), stat.st_mtime)
    cached = _PROMPT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    if content.startswith("\ufeff"):
        content = content.lstrip("\ufeff")
    _PROMPT_CACHE.clear()
    _PROMPT_CACHE[cache_key] = content
    return content


def resolve_system_prompt_path(
    *,
    model_name: str,
    default_path: Path,
    gemma_path: Optional[Path] = None,
    solar_path: Optional[Path] = None,
) -> Path:
    """Return the final system prompt path for the target model."""

    if model_name == "gemma_triton_0" and gemma_path is not None:
        return gemma_path
    if model_name == "solar_vllm_0" and solar_path is not None:
        return solar_path
    return default_path


async def load_system_prompt(path: Path) -> str:
    """Thin wrapper kept for semantic clarity at call sites."""

    return await load_prompt_file(path)


def get_llm_cache() -> Dict[str, Any]:
    """Return the process-local LLM cache."""

    return _LLM_CACHE
