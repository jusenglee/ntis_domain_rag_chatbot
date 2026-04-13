from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Optional, Tuple

from apps.platform.pipeline_steps import NormalizedIntent

from apps.platform.schemas import QueryPlan, build_query_plan

BuildPlan = Callable[..., tuple[QueryPlan, str]]
InvalidModeLogger = Callable[..., None]


def normalize_strategy_target_cols(cols: Any) -> list[str]:
    """planner가 준 target collection 힌트를 중복 없는 문자열 목록으로 정리한다."""
    out: list[str] = []
    for col in list(cols or []):
        text = str(col or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def planner_action_to_mode(action_value: Optional[str]) -> Optional[str]:
    """planner action 값을 runtime mode 의미로 변환한다."""
    if not action_value:
        return None
    action_value = str(action_value).strip().lower()
    if action_value in ("list", "stats", "download", "id_exact", "id_fuzzy", "detail"):
        return "lookup"
    if action_value in ("topic", "search"):
        return "search"
    if action_value == "join":
        return "join"
    return None


def resolve_planner_locked_plan(
    intent: NormalizedIntent,
    *,
    planner_mode: Optional[str],
    planner_action: Optional[str],
    hinted_cols: Any,
    build_plan: BuildPlan = build_query_plan,
    log_invalid_mode: Optional[InvalidModeLogger] = None,
) -> Tuple[QueryPlan, str, Optional[str], Optional[str]]:
    """planner가 잠근 mode와 target collection을 기본 QueryPlan 위에 반영한다.

    planner가 준 mode가 유효하지 않으면 버리고, target columns만 별도로 잠가 planner truth와 실행 plan을 같은 함수에서 맞춘다.
    """
    planner_mode_source = "payload.mode" if planner_mode else None
    if not planner_mode:
        planner_mode = planner_action_to_mode(planner_action)
        planner_mode_source = "payload.action" if planner_mode else None
    if planner_mode not in ("search", "lookup", "join"):
        if planner_mode and log_invalid_mode is not None:
            log_invalid_mode(
                planner_mode=planner_mode,
                planner_mode_source=planner_mode_source,
                planner_action=planner_action,
            )
        planner_mode = None
        planner_mode_source = None

    planner_target_cols_from_hint = normalize_strategy_target_cols(hinted_cols)
    plan, policy_reason = build_plan(
        intent,
        preferred_mode=planner_mode,
        preferred_mode_source=planner_mode_source,
    )
    if planner_target_cols_from_hint:
        plan = replace(plan, target_collections=tuple(planner_target_cols_from_hint))
        policy_reason = f"planner:target_cols_locked:{planner_mode_source or 'hint'}"
    return plan, policy_reason, planner_mode, planner_mode_source
