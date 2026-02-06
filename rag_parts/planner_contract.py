from __future__ import annotations

from typing import Optional, Tuple


def planner_contract_mode(
    strategy_mode: Optional[str],
    strategy_action: Optional[str],
    strategy_relation: Optional[Tuple[str, str]],
    fallback_mode: Optional[str],
) -> tuple[str, list[str]]:
    """planner 계약: planner가 고른 mode를 실행 mode로 유지하고 오류만 보고한다."""
    errors: list[str] = []

    mode = str(strategy_mode or fallback_mode or "").strip().lower()
    action_value = str(strategy_action or "").strip().lower()

    action_mode_map = {
        "list": "lookup",
        "stats": "lookup",
        "download": "lookup",
        "id_exact": "lookup",
        "id_fuzzy": "lookup",
        "topic": "search",
        "search": "search",
        "join": "join",
    }

    if mode not in ("search", "lookup", "join"):
        errors.append(f"invalid_mode:{mode or 'empty'}")

    expected_mode = action_mode_map.get(action_value)
    if expected_mode and mode != expected_mode:
        errors.append(f"action_mode_mismatch:{action_value}->{mode}")

    if mode == "join" and not strategy_relation:
        errors.append("join_without_relation")

    # planner 확정 mode를 보존: 오류가 있어도 mode를 바꾸지 않는다.
    return mode, errors


LOOKUP_FILTER_POLICIES = {"hard", "off", "must_one_then_should"}


def normalize_lookup_filter_policy(policy: Optional[str]) -> Optional[str]:
    value = str(policy or "").strip().lower()
    if not value:
        return None
    if value not in LOOKUP_FILTER_POLICIES:
        return None
    return value
