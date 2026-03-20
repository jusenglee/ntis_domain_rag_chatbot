from __future__ import annotations

"""Planner와 executor 사이의 계약을 검증하는 모듈.\n\n문서 계약을 코드에서 마지막으로 강제하는 계층이며,\n규칙 변경 시 `docs/CONTRACT.md`와 함께 갱신해야 한다.\n"""

from dataclasses import dataclass
from typing import Optional, Tuple, Any


@dataclass(frozen=True)
class PlannerContractViolation:
    """planner 계약 위반 한 건을 설명하는 정규 구조체다.
    error_code와 reason을 같이 들고 다니며 fail-close 판단이 왜 발생했는지 로그와 응답에서 바로 보여준다.
    """
    error_code: str
    reason: str


class StrategyViolation(RuntimeError):
    """여러 planner contract violation을 하나의 fail-close 예외로 묶어 전달한다.
    상위 레이어는 이 예외를 받으면 strategy 계약이 깨졌다고 보고 실행을 멈춘다.
    """

    def __init__(self, error_code: str, reason: str, violations: Optional[list[PlannerContractViolation]] = None):
        """error code, reason, 세부 violation 목록으로 StrategyViolation을 구성한다.
        표준 RuntimeError message에도 같은 정보를 실어 상위 예외 핸들러가 별도 파싱 없이 표준 문구를 사용하게 한다.
        """
        super().__init__(f"{error_code}: {reason}")
        self.error_code = error_code
        self.reason = reason
        self.violations = list(violations or [])


def planner_contract_mode(
        strategy_mode: Optional[str],
        strategy_action: Optional[str],
        strategy_relation: Optional[Tuple[str, str]],
        default_mode: Optional[str],
) -> tuple[str, list[str]]:
    """planner가 낸 mode가 action·relation과 맞는지 검사하고 정규 mode를 돌려준다.
    action-mode mismatch와 join_without_relation 같은 기본 계약 이탈을 초기에 걸러 낼 때 쓴다.
    """
    errors: list[str] = []

    mode = str(strategy_mode or default_mode or "").strip().lower()
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
    has_join_relation = False
    if isinstance(strategy_relation, (tuple, list)):
        has_join_relation = len(strategy_relation) >= 2 and bool(str(strategy_relation[0]).strip()) and bool(str(strategy_relation[1]).strip())
    else:
        has_join_relation = bool(str(strategy_relation or "").strip())

    # relation JOIN은 list/detail/stats action과 공존할 수 있으므로,
    # relation이 있는 JOIN은 action-mode mismatch 예외로 본다.
    if expected_mode and mode != expected_mode:
        if not (mode == "join" and has_join_relation):
            errors.append(f"action_mode_mismatch:{action_value}->{mode}")

    if mode == "join" and not has_join_relation:
        errors.append("join_without_relation")

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
        candidate_keys: Optional[dict[str, Any]] = None,
        project_key_policy: Optional[str] = None,
) -> list[PlannerContractViolation]:
    """mode, relation, ids_map, join_key_mode, target_cols 조합이 planner 계약을 지키는지 검사한다.
    `pjt_id`/`pjt_no` 혼합, non-join의 join_key_mode, group/instance mismatch, relation-target_cols 충돌을 fail-close 대상으로 고정한다.
    """
    violations: list[PlannerContractViolation] = []
    mode_norm = str(mode or "").strip().lower()
    join_key_mode_norm = str(join_key_mode or "").strip().lower()
    if mode_norm not in ("search", "lookup", "join"):
        return violations

    head_norm = str(head or "").strip().lower()
    normalized_target_cols = [str(col).strip() for col in (target_cols or []) if str(col).strip()]
    normalized_ids_map = ids_map if isinstance(ids_map, dict) else {}

    def _as_str_list(value: Any) -> list[str]:
        """contract 검사에 쓸 id/filter 값을 문자열 리스트로 정규화한다.
        list가 아닌 scalar·set·tuple까지 다 흡수해 validator가 입력 형태 차이에 흔들리지 않게 한다.
        """
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
    project_key_candidates = list((candidate_keys or {}).get("project_key") or []) if isinstance(candidate_keys, dict) else []
    project_key_policy_norm = str(project_key_policy or "").strip().lower()

    # 구조적 계약 위반은 상위 레이어에서 StrategyViolation으로 fail-close 한다.
    # 표현상의 경고는 parsing_warnings로 남길 수 있지만, 여기서 다루는 것은 fail-close 대상이다.
    if pjt_ids and pjt_nos:
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_MIXED_PROJECT_KEYS",
                reason=f"ids_map.pjt_id/pjt_no 혼합 입력은 허용되지 않음(mode={mode_norm})",
            )
        )

    if mode_norm != "join":
        if join_key_mode_norm:
            violations.append(
                PlannerContractViolation(
                    error_code="PLANNER_JOIN_KEY_MODE_INVALID",
                    reason="join_key_mode must be null when mode is not JOIN",
                )
            )
        return violations

    if join_key_mode_norm not in ("instance", "group", "deferred"):
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_KEY_MODE_INVALID",
                reason="join mode requires join_key_mode",
            )
        )

    if relation is None or relation_target_cols is None:
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_RELATION_UNRESOLVED",
                reason=f"JOIN relation 해석 실패(relation={relation})",
            )
        )
        return violations

    expected_target_head = relation[1]
    if head_norm and expected_target_head != head_norm:
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_RELATION_HEAD_TARGET_MISMATCH",
                reason=(
                    "JOIN head must equal relation target(relation[1])"
                    f"(expected_target={expected_target_head}, head={head_norm}, relation={relation})"
                ),
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

    if join_key_mode_norm == "group" and not pjt_nos:
        reason = (
            "join_key_mode=group 인데 ids_map에 pjt_no가 비어 있음"
            if not pjt_ids
            else "join_key_mode=group 인데 ids_map에 pjt_no 없이 pjt_id만 존재"
        )
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_KEY_MODE_IDS_MISMATCH",
                reason=reason,
            )
        )
    if join_key_mode_norm == "instance" and pjt_nos and not pjt_ids:
        violations.append(
            PlannerContractViolation(
                error_code="PLANNER_JOIN_KEY_MODE_IDS_MISMATCH",
                reason="join_key_mode=instance 인데 ids_map에 pjt_id 없이 pjt_no만 존재",
            )
        )

    if join_key_mode_norm == "deferred":
        if project_key_policy_norm != "ambiguous_or":
            violations.append(
                PlannerContractViolation(
                    error_code="PLANNER_DEFERRED_PROJECT_KEY_POLICY_INVALID",
                    reason="join_key_mode=deferred requires project_key_policy=ambiguous_or",
                )
            )
        if not project_key_candidates:
            violations.append(
                PlannerContractViolation(
                    error_code="PLANNER_DEFERRED_PROJECT_KEY_MISSING",
                    reason="join_key_mode=deferred requires candidate_keys.project_key",
                )
            )
        if pjt_ids or pjt_nos:
            violations.append(
                PlannerContractViolation(
                    error_code="PLANNER_DEFERRED_PROJECT_KEY_MIXED",
                    reason="join_key_mode=deferred forbids resolved pjt_id/pjt_no in ids_map",
                )
            )

    return violations


LOOKUP_FILTER_POLICIES = {"hard", "off", "must_one_then_should"}
LOOKUP_TITLE_FILTER_POLICIES = {"soft"}
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
    """stats 관련 정책 값을 기본값과 하한선으로 정규화한다.
    window_years, candidate_n, top_k가 비정상값이어도 retrieval 정책이 일관된 양수 상한으로 넘어가게 한다.
    """
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
    """lookup filter policy 문자열을 허용된 값으로 정규화한다.
    빈 값은 `None`으로 돌려 상위 정책 결정이 default를 쓸 수 있게 한다.
    """
    value = str(policy or "").strip().lower()
    if not value:
        return None
    if value not in LOOKUP_FILTER_POLICIES:
        return None
    return value


def normalize_lookup_title_filter_policy(policy: Optional[str]) -> Optional[str]:
    """title filter policy를 허용된 키워드로 정규화한다.
    title 매칭 로직은 시리즈형 정책이 아니라 제한된 선택지를 갖도록 의도된 관문이다.
    """
    value = str(policy or "").strip().lower()
    if not value:
        return None
    if value not in LOOKUP_TITLE_FILTER_POLICIES:
        return None
    return value


def resolve_lookup_title_match_mode(*, lookup_title_filter_policy: str, index_supports_text: bool) -> str:
    """title filter policy에 맞는 실제 title 비교 모드를 고른다.
    soft/exact/contains 같은 runtime match mode를 policy 키워드에서 파생시키는 역할을 한다.
    """
    policy = str(lookup_title_filter_policy or "").strip().lower()
    return TITLE_MATCH_MODE_CONTAINS


@dataclass(frozen=True)
class StrategyCompileResult:
    """planner 입력과 contract 검사 결과를 함께 묶어 돌려주는 산출물이다.
    compiled strategy meta와 violations를 한 곳에 실어 runtime prelude가 후속 fail-close 판단을 하게 한다.
    """
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
    """planner payload를 실행 가능한 strategy 형태로 컴파일하는 관문 객체다.
    mode normalization, relation target resolution, lookup/title policy 확정, contract validation을 이 관문에서 처리한다.
    """

    @staticmethod
    def compile(
            *,
            mode: str,
            relation: Optional[Tuple[str, str]],
            target_cols: list[str],
            default_target_cols: list[str],
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
        """planner raw fields를 정규 strategy로 컴파일하고 contract violation을 수집한다.
        relation route, target cols, title policy, lookup filter policy를 해석한 뒤 fail-close 사유까지 함께 돌려준다.
        """
        from apps.core.filters import compile_filter

        planner_filter_spec_norm = dict(planner_filter_spec or {})

        mode_norm = str(mode or "").strip().lower()
        target_cols_norm = [str(c).strip() for c in (target_cols or []) if str(c).strip()]
        if not target_cols_norm:
            target_cols_norm = [str(c).strip() for c in (default_target_cols or []) if str(c).strip()]

        lookup_filter_policy = normalize_lookup_filter_policy(lookup_filter_policy_hint) or "hard"
        lookup_title_filter_policy = normalize_lookup_title_filter_policy(lookup_title_filter_policy_hint) or "soft"
        if lookup_title_filter_policy != "soft":
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
