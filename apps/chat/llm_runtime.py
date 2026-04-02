from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

_LLM_CACHE: Dict[str, Any] = {}
_PROMPT_CACHE: dict[tuple[str, float], str] = {}


def build_llm(*, model_name: str, solar_vllm_config: Any) -> Any:
    """모델 이름에 맞는 LLM 어댑터를 만들고 캐시에 재사용한다.

    Solar는 OpenAI 호환 클라이언트를, 나머지는 TritonChatModel을 사용해 호출 경로 차이를 이 함수 안에 가둔다.
    """
    from apps.platform.openai_compat_llm import OpenAICompatChatModel
    from apps.platform.triton_llm import TritonChatModel

    cached = _LLM_CACHE.get(model_name)
    if cached is not None:
        return cached

    if model_name == "solar_vllm_0":
        llm = OpenAICompatChatModel(
            model_name=solar_vllm_config.model_name,
            base_url=solar_vllm_config.base_url,
            api_key=solar_vllm_config.api_key,
            timeout=solar_vllm_config.timeout,
        )
    else:
        llm = TritonChatModel(model_name=model_name)

    _LLM_CACHE[model_name] = llm
    return llm


async def load_prompt_file(path: Path) -> str:
    """프롬프트 파일을 UTF-8로 읽고 수정 시각 기준 캐시에 보관한다."""
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
    """대상 모델에 맞는 최종 answer prompt 경로를 반환한다."""
    if model_name == "gemma_triton_0" and gemma_path is not None:
        return gemma_path
    if model_name == "solar_vllm_0" and solar_path is not None:
        return solar_path
    return default_path


async def load_system_prompt(path: Path) -> str:
    """시스템 프롬프트 파일 로딩을 의미상 분리한 얇은 래퍼다."""
    return await load_prompt_file(path)


def get_llm_cache() -> Dict[str, Any]:
    """현재 프로세스에 살아 있는 LLM 인스턴스 캐시를 돌려준다."""
    return _LLM_CACHE
