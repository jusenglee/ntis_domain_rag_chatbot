from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeStrategyPolicy:
    """런타임이 planner 전략 불일치를 어떻게 다룰지 정리한 정책 객체다."""
    strict_strategy_consistency: bool
    runtime_env: str
    force_fallback_chat: bool


@dataclass(frozen=True)
class PromotionRuntimePolicy:
    """현재 런타임에서 promotion 기능을 어떤 수준으로 열지 나타내는 정책 객체다."""
    promotion_mode: str
    promotion_feature_mode: str
    promotion_enabled: bool
    promotion_max_depth: int


def env_flag(name: str, default: str = "0") -> bool:
    """환경변수를 불리언 플래그 규칙으로 읽는다."""
    return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "y", "on")


def build_runtime_strategy_policy() -> RuntimeStrategyPolicy:
    """환경변수를 읽어 전략 일관성 정책을 조립한다."""
    return RuntimeStrategyPolicy(
        strict_strategy_consistency=env_flag("RAG_STRICT_STRATEGY_CONSISTENCY", "1"),
        runtime_env=str(os.getenv("APP_ENV", os.getenv("ENV", "")) or "").strip().lower(),
        # RAG_FORCE_FALLBACK_CHAT is a response fallback, not a strategy fallback.
        force_fallback_chat=env_flag("RAG_FORCE_FALLBACK_CHAT", "0"),
    )


def build_promotion_runtime_policy(*, mode: str) -> PromotionRuntimePolicy:
    """현재는 promotion을 비활성화한 기본 정책만 만든다."""
    return PromotionRuntimePolicy(
        promotion_mode=mode,
        promotion_feature_mode="disable",
        promotion_enabled=False,
        promotion_max_depth=0,
    )


