from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

from apps.platform.settings import SOLAR_TOKENIZER_NAME_OR_PATH
from loguru import logger


def serialize_json(obj: Any) -> str:
    """Serialize prompt JSON with the single canonical format used for packing."""

    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


@lru_cache(maxsize=1)
def _load_solar_tokenizer() -> Any:
    if not SOLAR_TOKENIZER_NAME_OR_PATH:
        return None
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(SOLAR_TOKENIZER_NAME_OR_PATH, trust_remote_code=True)
    except Exception as exc:  # pragma: no cover - environment-dependent
        logger.warning("solar tokenizer unavailable: {exc}", exc=exc)
        return None


@lru_cache(maxsize=1)
def _load_tiktoken_encoder() -> Any:
    try:
        import tiktoken

        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return tiktoken.encoding_for_model("gpt-4o-mini")
    except Exception:  # pragma: no cover - optional dependency
        return None


def count_text(text: str) -> int:
    """Count tokens for evidence packing with Solar tokenizer as the primary source."""

    normalized = str(text or "")
    tokenizer = _load_solar_tokenizer()
    if tokenizer is not None:
        try:
            return len(tokenizer.encode(normalized, add_special_tokens=False))
        except TypeError:  # pragma: no cover - tokenizer API variance
            return len(tokenizer.encode(normalized))
        except Exception as exc:  # pragma: no cover - tokenizer runtime variance
            logger.warning("solar token count failed: {exc}", exc=exc)

    encoder = _load_tiktoken_encoder()
    if encoder is not None:
        return len(encoder.encode(normalized))

    # Last-resort development fallback. Production packing should use Solar.
    words = len(normalized.split())
    return max(words, int(math.ceil(len(normalized) / 4)))


def count_json(obj: Any) -> int:
    return count_text(serialize_json(obj))


def count_serialized_units(units: list[dict[str, Any]]) -> int:
    return count_text(serialize_json(list(units or [])))
