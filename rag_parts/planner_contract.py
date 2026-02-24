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
        "list": "lookup",
        "stats": "lookup",
        "download": "lookup",
        "id_exact": "lookup",
        "id_fuzzy": "lookup",
        "detail": "lookup",
        "topic": "search",
        "search": "search",
        "join": "join",
    }

    if mode not in ("search", "lookup", "join"):
        errors.append(f"invalid_mode:{mode or 'empty'}")

    expected_mode = action_mode_map.get(action_value)
    # JOIN relation 전략은 action(list/detail/stats/download)과 공존 가능하므로
    # planner가 처음부터 JOIN을 확정한 경우 action-mode mismatch로 실패시키지 않는다.
    if expected_mode and mode != expected_mode:
        if not (mode == "join" and strategy_relation):
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
    if mode_norm not in ("lookup", "join"):
        return violations

    head_norm = str(head or "").strip().lower()
    normalized_target_cols = [str(col).strip() for col in (target_cols or []) if str(col).strip()]
    normalized_ids_map = ids_map if isinstance(ids_map, dict) else {}

    def _as_str_list(value: Any) -> list[str]:
        """ids_map 값이 str/int/list 등으로 흔들리는 경우를 흡수."""
        if value is None:
            return []
        if isinstance(value, (list, tuple, set)):
            seq = list(value)
        else:
            seq = [value]
        out: list[str] = []
        for x in seq:
            s = str(x).strip()
            if not s or s.lower() == "none":
                continue
            out.append(s)
        return out

    pjt_ids = _as_str_list(normalized_ids_map.get("pjt_id"))
    pjt_nos = _as_str_list(normalized_ids_map.get("pjt_no"))

    # planner 입력(ids_map) 계약: lookup/join에서 pjt_id vs pjt_no 는 XOR만 허용한다.
    if pjt_ids and pjt_nos:
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_MIXED_PROJECT_KEYS",
                reason=f"ids_map.pjt_id/pjt_no 혼합 입력은 허용되지 않음(mode={mode_norm})",
            )
        )

    if mode_norm != "join":
        return violations

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
LOOKUP_TITLE_FILTER_POLICIES = {"soft", "hard"}
TITLE_MATCH_MODE_EXACT = "EXACT"
TITLE_MATCH_MODE_TEXT = "TEXT"
TITLE_MATCH_MODE_CONTAINS = "CONTAINS"

DEFAULT_STATS_METRIC = "project_participation_count"
DEFAULT_STATS_WINDOW_YEARS = 3
DEFAULT_STATS_CANDIDATE_N = 50
DEFAULT_STATS_TOP_K = 1
DEFAULT_STATS_TIE_BREAK = "performance_count_desc_name_asc"


def normalize_stats_policy_value(
        *,
        stats_metric: Optional[str],
        window_years: Optional[int],
        candidate_n: Optional[int],
        top_k: Optional[int],
        tie_break: Optional[str],
) -> dict[str, Any]:
    metric = str(stats_metric or "").strip() or DEFAULT_STATS_METRIC
    window = int(window_years or DEFAULT_STATS_WINDOW_YEARS)
    candidate = int(candidate_n or DEFAULT_STATS_CANDIDATE_N)
    top = int(top_k or DEFAULT_STATS_TOP_K)
    tie = str(tie_break or "").strip() or DEFAULT_STATS_TIE_BREAK
    return {
        "stats_metric": metric,
        "window_years": max(1, window),
        "candidate_n": max(1, candidate),
        "top_k": max(1, top),
        "tie_break": tie,
    }


def normalize_lookup_filter_policy(policy: Optional[str]) -> Optional[str]:
    value = str(policy or "").strip().lower()
    if not value:
        return None
    if value not in LOOKUP_FILTER_POLICIES:
        return None
    return value


def normalize_lookup_title_filter_policy(policy: Optional[str]) -> Optional[str]:
    """lookup title 필터 정책 정규화.

    정책 계약:
    - soft: server-side title filter를 적용하지 않고, title_terms는 soft ranking 신호로만 활용.
    - hard: title_terms를 server-side must로 적용(단, detail lookup에서만 허용).
    """
    value = str(policy or "").strip().lower()
    if not value:
        return None
    if value not in LOOKUP_TITLE_FILTER_POLICIES:
        return None
    return value


def resolve_lookup_title_match_mode(*, lookup_title_filter_policy: str, index_supports_text: bool) -> str:
    """lookup title 정책을 실행 가능한 title match mode로 해석한다.

    정책 매핑:
    - hard: EXACT 또는 TEXT(인덱스 지원 시)
    - soft: CONTAINS(post-filter + rerank signal)
    """
    policy = str(lookup_title_filter_policy or "").strip().lower()
    if policy == "hard":
        return TITLE_MATCH_MODE_TEXT if bool(index_supports_text) else TITLE_MATCH_MODE_EXACT
    return TITLE_MATCH_MODE_CONTAINS


@dataclass(frozen=True)
class StrategyCompileResult:
    target_cols: tuple[str, ...]
    topk_spec: dict[str, Any]
    rerank_spec: dict[str, Any]
    filter_spec: dict[str, Any]
    qdrant_filter: Optional[Any]
    hop1_spec: Optional[dict[str, Any]]
    hop2_spec: Optional[dict[str, Any]]
    search_filter_enabled: bool
    lookup_filter_enabled: bool
    lookup_filter_policy: str
    lookup_title_filter_policy: str
    title_match_mode: str
    relation_lookup_enforce: bool


class StrategyCompiler:
    """planner_contract(JSON)를 실행 스펙으로 변환하는 컴파일러."""

    @staticmethod
    def compile(
            *,
            mode: str,
            relation: Optional[Tuple[str, str]],
            target_cols: list[str],
            fallback_target_cols: list[str],
            planner_filter_spec: Optional[dict[str, Any]],
            topk_spec: Optional[dict[str, Any]],
            rerank_spec: Optional[dict[str, Any]],
            search_filter_signal: bool,
            search_filter_conf_ok: bool,
            lookup_filter_policy_hint: Optional[str],
            lookup_title_filter_policy_hint: Optional[str],
            detail_lookup_request: bool,
            title_text_match_supported: bool,
    ) -> StrategyCompileResult:
        from rag_parts.filters import compile_filter

        planner_filter_spec_norm = dict(planner_filter_spec or {})

        mode_norm = str(mode or "").strip().lower()
        target_cols_norm = [str(c).strip() for c in (target_cols or []) if str(c).strip()]
        if not target_cols_norm:
            target_cols_norm = [str(c).strip() for c in (fallback_target_cols or []) if str(c).strip()]

        lookup_filter_policy = normalize_lookup_filter_policy(lookup_filter_policy_hint) or "hard"
        lookup_title_filter_policy = normalize_lookup_title_filter_policy(lookup_title_filter_policy_hint) or "soft"
        if lookup_title_filter_policy == "hard" and not detail_lookup_request:
            lookup_title_filter_policy = "soft"
        title_match_mode = resolve_lookup_title_match_mode(
            lookup_title_filter_policy=lookup_title_filter_policy,
            index_supports_text=title_text_match_supported,
        )

        search_filter_enabled = bool(mode_norm == "search" and search_filter_signal and search_filter_conf_ok)
        lookup_filter_enabled = bool(
            mode_norm == "lookup"
            and lookup_filter_policy in ("hard", "must_one_then_should")
            and search_filter_signal
            and search_filter_conf_ok
        )

        relation_lookup_enforce_raw = planner_filter_spec_norm.get("relation_lookup_enforce")
        relation_lookup_enforce = str(relation_lookup_enforce_raw).strip().lower() in ("1", "true", "yes", "y")

        filter_spec = {
            **planner_filter_spec_norm,
            "search_filter_enabled": search_filter_enabled,
            "lookup_filter_enabled": lookup_filter_enabled,
            "relation_lookup_enforce": relation_lookup_enforce,
            "lookup_filter_policy": lookup_filter_policy,
            "lookup_title_filter_policy": lookup_title_filter_policy,
            "title_match_mode": title_match_mode,
            "filter_signal": bool(search_filter_signal),
            "filter_conf_ok": bool(search_filter_conf_ok),
        }

        qdrant_filter = compile_filter(planner_filter_spec_norm.get("qdrant_filter"))
        hop1_spec = None
        hop2_spec = None
        if relation and mode_norm == "join":
            hop1_spec = {
                "collection": relation[0],
                "qdrant_filter": compile_filter(planner_filter_spec_norm.get("join_hop1_filter")),
            }
            hop2_spec = {
                "collection": relation[1],
                "qdrant_filter": compile_filter(planner_filter_spec_norm.get("join_filter")),
            }

        return StrategyCompileResult(
            target_cols=tuple(target_cols_norm),
            topk_spec=dict(topk_spec or {}),
            rerank_spec=dict(rerank_spec or {}),
            filter_spec=filter_spec,
            qdrant_filter=qdrant_filter,
            hop1_spec=hop1_spec,
            hop2_spec=hop2_spec,
            search_filter_enabled=search_filter_enabled,
            lookup_filter_enabled=lookup_filter_enabled,
            lookup_filter_policy=lookup_filter_policy,
            lookup_title_filter_policy=lookup_title_filter_policy,
            title_match_mode=title_match_mode,
            relation_lookup_enforce=relation_lookup_enforce,
        )
