from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Any


@dataclass(frozen=True)
class PlannerContractViolation:
    error_code: str
    reason: str


class StrategyViolation(RuntimeError):
    """Planner/Strategy 계약 위반을 상위 레이어로 전파하기 위한 명시적 예외."""

    def __init__(self, error_code: str, reason: str, violations: Optional[list[PlannerContractViolation]] = None):
        super().__init__(f"{error_code}: {reason}")
        self.error_code = error_code
        self.reason = reason
        self.violations = list(violations or [])


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
    if mode == "join" and action_value == "list":
        expected_mode = "join"
    if expected_mode and mode != expected_mode:
        errors.append(f"action_mode_mismatch:{action_value}->{mode}")

    if mode == "join" and not strategy_relation:
        errors.append("join_without_relation")

    # planner 확정 mode를 보존: 오류가 있어도 mode를 바꾸지 않는다.
    return mode, errors


def validate_planner_contract(
    *,
    mode: Optional[str],
    head: Optional[str],
    relation: Optional[Tuple[str, str]],
    target_cols: list[str],
    ids_map: Optional[dict[str, Any]],
    relation_target_cols: Optional[tuple[str, str]],
    join_key_mode: Optional[str],
) -> list[PlannerContractViolation]:
    """실행 직전 planner 계약 위반을 에러 코드로 수집한다."""
    violations: list[PlannerContractViolation] = []
    mode_norm = str(mode or "").strip().lower()
    if mode_norm != "join":
        return violations

    head_norm = str(head or "").strip().lower()
    normalized_target_cols = [str(col).strip() for col in (target_cols or []) if str(col).strip()]
    normalized_ids_map = ids_map if isinstance(ids_map, dict) else {}
    pjt_ids = [str(x).strip() for x in (normalized_ids_map.get("pjt_id") or []) if str(x).strip()]
    pjt_nos = [str(x).strip() for x in (normalized_ids_map.get("pjt_no") or []) if str(x).strip()]

    if relation is None or relation_target_cols is None:
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_RELATION_UNRESOLVED",
                reason=f"JOIN relation 해석 실패(relation={relation})",
            )
        )
        return violations

    if head_norm and relation[0] != head_norm:
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_RELATION_HEAD_TARGET_MISMATCH",
                reason=f"relation/head 불일치(head={head_norm}, relation={relation})",
            )
        )

    relation_cols = [relation_target_cols[0], relation_target_cols[1]]
    if normalized_target_cols and any(col not in normalized_target_cols for col in relation_cols):
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_RELATION_HEAD_TARGET_MISMATCH",
                reason=(
                    "relation/target_cols 불일치"
                    f"(relation_cols={relation_cols}, target_cols={normalized_target_cols})"
                ),
            )
        )

    if pjt_ids and pjt_nos:
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_MIXED_PROJECT_KEYS",
                reason="pjt_id/pjt_no 혼합 입력은 허용되지 않음",
            )
        )

    join_key_mode_norm = str(join_key_mode or "").strip().lower()
    if join_key_mode_norm == "group" and pjt_ids and not pjt_nos:
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_KEY_MODE_IDS_MISMATCH",
                reason="join_key_mode=group 인데 ids_map에 pjt_no 없이 pjt_id만 존재",
            )
        )
    if join_key_mode_norm == "instance" and pjt_nos and not pjt_ids:
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_KEY_MODE_IDS_MISMATCH",
                reason="join_key_mode=instance 인데 ids_map에 pjt_id 없이 pjt_no만 존재",
            )
        )

    return violations


LOOKUP_FILTER_POLICIES = {"hard", "off", "must_one_then_should"}


def normalize_lookup_filter_policy(policy: Optional[str]) -> Optional[str]:
    value = str(policy or "").strip().lower()
    if not value:
        return None
    if value not in LOOKUP_FILTER_POLICIES:
        return None
    return value
