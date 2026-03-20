from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeStrategyPolicy:
    """Runtime policy for strategy consistency and response fallback behavior.

    `force_fallback_chat` is a response-level fallback knob. It must not be
    interpreted as permission for lower layers to invent a new SEARCH/LOOKUP/JOIN
    strategy.
    """

    strict_strategy_consistency: bool
    runtime_env: str
    force_fallback_chat: bool


@dataclass(frozen=True)
class PromotionRuntimePolicy:
    """Policy object describing whether promotion is enabled in this runtime."""

    promotion_mode: str
    promotion_feature_mode: str
    promotion_enabled: bool
    promotion_max_depth: int


def env_flag(name: str, default: str = "0") -> bool:
    """Read an environment flag with common truthy forms."""

    return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "y", "on")


def build_runtime_strategy_policy() -> RuntimeStrategyPolicy:
    """Build the runtime strategy policy from environment defaults."""

    return RuntimeStrategyPolicy(
        strict_strategy_consistency=env_flag("RAG_STRICT_STRATEGY_CONSISTENCY", "1"),
        runtime_env=str(os.getenv("APP_ENV", os.getenv("ENV", "")) or "").strip().lower(),
        force_fallback_chat=env_flag("RAG_FORCE_FALLBACK_CHAT", "0"),
    )


def build_promotion_runtime_policy(*, mode: str) -> PromotionRuntimePolicy:
    """Build the current promotion policy. Promotion remains disabled by default."""

    return PromotionRuntimePolicy(
        promotion_mode=mode,
        promotion_feature_mode="disable",
        promotion_enabled=False,
        promotion_max_depth=0,
    )
