from __future__ import annotations

from typing import Any


FRONTEND_STREAM_MODEL_KEYS: tuple[str, str] = ("solar", "gemma")
TERMINAL_DONE_MODEL_KEYS: tuple[str, str] = ("solar", "gemma")
DETERMINISTIC_TERMINAL_ANSWER_KINDS = frozenset(
    {
        "direct_answer",
        "error",
        "no_result",
        "clarification",
    }
)


def normalize_stream_model_key_value(model_key: Any) -> str:
    """Normalize internal provider aliases to frontend stream model keys."""
    normalized = str(model_key or "").strip().lower()
    if normalized in {"solar", "upstage"}:
        return "solar"
    if normalized == "gemma":
        return "gemma"
    return normalized


def answer_chunk_model_keys_for_frontend(*, answer_kind: Any, model_key: Any) -> tuple[str, ...]:
    """Return frontend-supported model keys for a user-visible answer chunk."""
    normalized_kind = str(answer_kind or "").strip().lower()
    if normalized_kind in DETERMINISTIC_TERMINAL_ANSWER_KINDS:
        return FRONTEND_STREAM_MODEL_KEYS

    normalized_key = normalize_stream_model_key_value(model_key)
    if normalized_key in FRONTEND_STREAM_MODEL_KEYS:
        return (normalized_key,)
    return ("solar",)


def sanitize_terminal_done_model_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Prevent internal-only model keys from leaking through terminal done meta."""
    sanitized = dict(meta or {})
    normalized_key = normalize_stream_model_key_value(sanitized.get("model_key"))
    if normalized_key in FRONTEND_STREAM_MODEL_KEYS:
        sanitized["model_key"] = normalized_key
        return sanitized
    if normalized_key in {"", "none", "null"}:
        sanitized.pop("model_key", None)
        return sanitized
    sanitized.pop("model_key", None)
    return sanitized


def terminal_done_model_keys_for_frontend(meta: dict[str, Any]) -> tuple[str | None, ...]:
    """Return top-level model_key fan-out targets for terminal done events."""
    return TERMINAL_DONE_MODEL_KEYS
