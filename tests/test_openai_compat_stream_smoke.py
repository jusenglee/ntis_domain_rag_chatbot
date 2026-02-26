from __future__ import annotations

import asyncio
import os

import pytest
from openai import AsyncOpenAI


def test_vllm_openai_compat_stream_smoke() -> None:
    """OpenAI 2.16.0 + vLLM OpenAI-compatible 스트리밍 최소 스모크.

    실행 조건:
    - OPENAI_COMPAT_SMOKE=1
    - OPENAI_COMPAT_BASE_URL (예: http://localhost:8010/v1)
    - OPENAI_COMPAT_MODEL (기본 /model)
    """

    if os.getenv("OPENAI_COMPAT_SMOKE", "0") != "1":
        pytest.skip("OPENAI_COMPAT_SMOKE=1 일 때만 실행")

    base_url = os.getenv("OPENAI_COMPAT_BASE_URL")
    if not base_url:
        pytest.skip("OPENAI_COMPAT_BASE_URL 미설정")

    model_name = os.getenv("OPENAI_COMPAT_MODEL", "/model")
    api_key = os.getenv("OPENAI_COMPAT_API_KEY", "EMPTY")

    async def _run() -> None:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=float(os.getenv("OPENAI_COMPAT_TIMEOUT", "30")))
        try:
            stream = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": "간단히 인사해줘."}],
                max_tokens=32,
                stream=True,
            )

            chunk_n = 0
            text_parts = []
            async for chunk in stream:
                chunk_n += 1
                if chunk.choices and getattr(chunk.choices[0].delta, "content", None):
                    text_parts.append(chunk.choices[0].delta.content)

            final_text = "".join(text_parts).strip()
            assert chunk_n >= 1
            assert final_text != ""
        finally:
            await client.close()

    asyncio.run(_run())
