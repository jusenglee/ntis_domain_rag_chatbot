from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeStrategyPolicy:
    strict_strategy_consistency: bool = True
    runtime_env: str = ""
    force_fallback_chat: bool = False
    name: str = ""
    mode: str = ""
    primary: str = ""
    secondary: str | None = None
    head: str | None = None
    max_steps: int = 1
    retry_mode: str | None = None
    relax_budget: int = 0
    exact_id_no_relax: bool = False
    inherit_filters: bool = False
    user_notice_template: str | None = None
    observation_codes: tuple[str, ...] = ()
    auto_recovery_enabled: bool = True


@dataclass(frozen=True)
class PromotionRuntimePolicy:
    promotion_mode: str
    promotion_feature_mode: str
    promotion_enabled: bool
    promotion_max_depth: int


RECOVERY_POLICIES: dict[str, RuntimeStrategyPolicy] = {
    "LOOKUP_MISSING_RECOVERY": RuntimeStrategyPolicy(
        name="LOOKUP_MISSING_RECOVERY",
        mode="LOOKUP",
        head="project",
        primary="fetch_project_detail",
        secondary="search_projects_by_text",
        max_steps=2,
        retry_mode="fallback_search_once",
        relax_budget=0,
        exact_id_no_relax=True,
        inherit_filters=True,
        user_notice_template="요청한 식별자로 결과가 없어 동일 조건의 유사 과제 검색으로 보정했습니다.",
        observation_codes=("empty_primary_search", "search_recovery_succeeded"),
        auto_recovery_enabled=True,
    ),
    "SEARCH_RECOVERY": RuntimeStrategyPolicy(
        name="SEARCH_RECOVERY",
        mode="SEARCH",
        head="project",
        primary="search_projects_by_text",
        secondary="search_projects_by_text",
        max_steps=2,
        retry_mode="relax_filters_once",
        relax_budget=1,
        exact_id_no_relax=True,
        inherit_filters=True,
        user_notice_template="1차 검색 결과가 없어 동일한 SEARCH 모드 안에서 필터를 1회 완화해 다시 확인했습니다.",
        observation_codes=(
            "empty_primary_search",
            "filter_gate_too_strict",
            "candidate_underflow",
            "relax_budget_exhausted",
            "search_recovery_succeeded",
        ),
        auto_recovery_enabled=True,
    ),
    "PEOPLE_SEARCH_OBSERVATION": RuntimeStrategyPolicy(
        name="PEOPLE_SEARCH_OBSERVATION",
        mode="SEARCH",
        head="people",
        primary="search_route_by_text",
        secondary=None,
        max_steps=1,
        retry_mode="observation_only",
        relax_budget=0,
        exact_id_no_relax=False,
        inherit_filters=True,
        user_notice_template=None,
        observation_codes=("observation_only",),
        auto_recovery_enabled=False,
    ),
    "ORG_SEARCH_OBSERVATION": RuntimeStrategyPolicy(
        name="ORG_SEARCH_OBSERVATION",
        mode="SEARCH",
        head="org",
        primary="search_route_by_text",
        secondary=None,
        max_steps=1,
        retry_mode="observation_only",
        relax_budget=0,
        exact_id_no_relax=False,
        inherit_filters=True,
        user_notice_template=None,
        observation_codes=("observation_only",),
        auto_recovery_enabled=False,
    ),
    "PERF_SEARCH_OBSERVATION": RuntimeStrategyPolicy(
        name="PERF_SEARCH_OBSERVATION",
        mode="SEARCH",
        head="perf",
        primary="search_route_by_text",
        secondary=None,
        max_steps=1,
        retry_mode="observation_only",
        relax_budget=0,
        exact_id_no_relax=False,
        inherit_filters=True,
        user_notice_template=None,
        observation_codes=("observation_only",),
        auto_recovery_enabled=False,
    ),
    "JOIN_QUALITY_RECOVERY": RuntimeStrategyPolicy(
        name="JOIN_QUALITY_RECOVERY",
        mode="JOIN",
        primary="fetch_project_performance",
        secondary="fetch_project_performance",
        max_steps=2,
        retry_mode="group_anchor_once",
        relax_budget=0,
        exact_id_no_relax=False,
        inherit_filters=True,
        user_notice_template=None,
        observation_codes=(),
        auto_recovery_enabled=True,
    ),
}


def env_flag(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "y", "on")


def build_runtime_strategy_policy() -> RuntimeStrategyPolicy:
    return RuntimeStrategyPolicy(
        strict_strategy_consistency=env_flag("RAG_STRICT_STRATEGY_CONSISTENCY", "1"),
        runtime_env=str(os.getenv("APP_ENV", os.getenv("ENV", "")) or "").strip().lower(),
        force_fallback_chat=env_flag("RAG_FORCE_FALLBACK_CHAT", "0"),
    )


def build_promotion_runtime_policy(*, mode: str) -> PromotionRuntimePolicy:
    return PromotionRuntimePolicy(
        promotion_mode=mode,
        promotion_feature_mode="disable",
        promotion_enabled=False,
        promotion_max_depth=0,
    )


def resolve_runtime_strategy_policy(question_analysis: Any) -> RuntimeStrategyPolicy | None:
    mode = str(getattr(question_analysis, "mode", "") or "").strip().upper()
    head = str(getattr(question_analysis, "head", "") or "").strip().lower() or None

    if mode == "LOOKUP" and head == "project":
        return RECOVERY_POLICIES["LOOKUP_MISSING_RECOVERY"]
    if mode == "SEARCH" and head == "project":
        return RECOVERY_POLICIES["SEARCH_RECOVERY"]
    if mode == "SEARCH" and head == "people":
        return RECOVERY_POLICIES["PEOPLE_SEARCH_OBSERVATION"]
    if mode == "SEARCH" and head == "org":
        return RECOVERY_POLICIES["ORG_SEARCH_OBSERVATION"]
    if mode == "SEARCH" and head == "perf":
        return RECOVERY_POLICIES["PERF_SEARCH_OBSERVATION"]
    if mode == "JOIN":
        return RECOVERY_POLICIES["JOIN_QUALITY_RECOVERY"]
    return None
