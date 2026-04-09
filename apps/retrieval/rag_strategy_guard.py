from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from apps.platform.pipeline_steps import build_changed_fields
from apps.planner.planner_contract import StrategyViolation
from apps.retrieval.filters import validate_planner_join_keys
from apps.platform.schemas import QueryPlan
from apps.platform.log_keys import CHANGED_BY_EXECUTOR


def validate_project_key_exclusive(ids_map: Any, mode: Optional[str]) -> Dict[str, List[str]]:
    """ids_map에 project instance key와 group key가 섞였는지 검사한다.

    내부 검증기가 ValueError를 던지면 planner 계층의 StrategyViolation으로 다시 감싸,
    runtime이 계약 위반을 동일한 오류 코드 체계로 처리하게 만든다.
    """
    mode_norm = str(mode or "").strip().lower()
    try:
        return validate_planner_join_keys(mode=mode_norm, ids_map=ids_map)
    except ValueError as exc:
        msg = str(exc)
        error_code, _, reason = msg.partition(": ")
        if not error_code.startswith("PLANNER_"):
            error_code = "PLANNER_MIXED_PROJECT_KEYS"
            reason = msg
        raise StrategyViolation(error_code=error_code, reason=reason or msg) from exc


def normalize_strategy_target_cols(cols: Any) -> List[str]:
    """Planner가 준 target collection 목록을 공백 없는 문자열 리스트로 정리한다.

    비어 있는 값만 제거하고 의미 변경은 하지 않아, 이후 strategy 비교가 표현 차이 때문에 흔들리지 않게 한다.
    """
    out: List[str] = []
    for c in list(cols or []):
        v = str(c).strip()
        if v:
            out.append(v)
    return out


def strategy_field_diff(
    planner: Any,
    executed: Any,
    keys: List[str],
    *,
    changed_by: str = CHANGED_BY_EXECUTOR,
) -> Dict[str, Dict[str, Any]]:
    """Planner snapshot과 executor snapshot의 전략 필드 차이를 로그용 형태로 만든다.

    실제 비교 기준은 build_changed_fields에 위임하고, 여기서는 어떤 필드가 executor 단계에서 달라졌는지만 압축해서 넘긴다.
    """
    planner_map = planner if isinstance(planner, dict) else {}
    executed_map = executed if isinstance(executed, dict) else {}
    return build_changed_fields(
        planner_map,
        executed_map,
        tuple(keys),
        changed_by=changed_by,
    )


def derive_planner_locks(plan: QueryPlan) -> tuple[str, Optional[tuple[str, str]], List[str]]:
    """QueryPlan에서 planner lock으로 취급할 핵심 전략 값을 추출한다.

    mode, relation, target collections를 정규화해 이후 executor가 planner truth를 재판단하지 않도록 묶는다.
    """
    planner_mode_locked = str(getattr(plan, "mode", "") or "").strip().lower()
    planner_relation_locked = getattr(plan, "relation", None)
    planner_target_cols_locked = normalize_strategy_target_cols(getattr(plan, "target_collections", None))
    return planner_mode_locked, planner_relation_locked, planner_target_cols_locked


def strategy_consistency_or_violation(
    *,
    strict: bool,
    mismatch_kind: str,
    planner_value: Any,
    executed_value: Any,
    log_kv: Callable[..., None],
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Planner 전략과 실제 실행 전략이 어긋나는지 검사하고 필요하면 예외를 던진다.

    strict 모드에서는 즉시 StrategyViolation을 올리고, 완화 모드에서는 debug tier 로그만 남겨 관측과 차단 정책을 분리한다.
    """
    if planner_value == executed_value:
        return
    payload = {
        "kind": mismatch_kind,
        "planner": planner_value,
        "executed": executed_value,
    }
    if context:
        payload.update(context)
    log_kv("RAG.STRATEGY.MISMATCH", level="error" if strict else "warning", **payload, tier="debug")
    log_kv(
        "RAG.ERROR.STRATEGY_MISMATCH",
        level="error" if strict else "warning",
        tier="debug",
        planner_snapshot=planner_value,
        executed_snapshot=executed_value,
        kind=mismatch_kind,
    )
    if strict:
        raise StrategyViolation(
            error_code="STRATEGY_MISMATCH",
            reason=(
                f"planner/executed mismatch({mismatch_kind}): "
                f"planner={planner_value}, executed={executed_value}"
            ),
        )


def strategy_must_match_or_violation(
    *,
    mismatch_kind: str,
    planner_value: Any,
    executed_value: Any,
    log_kv: Callable[..., None],
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """전략 불일치를 항상 fail-close로 처리해야 하는 지점에서 쓰는 얇은 래퍼다."""
    strategy_consistency_or_violation(
        strict=True,
        mismatch_kind=mismatch_kind,
        planner_value=planner_value,
        executed_value=executed_value,
        log_kv=log_kv,
        context=context,
    )
