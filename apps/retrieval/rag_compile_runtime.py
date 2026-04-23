from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from loguru import logger

from apps.planner.planner_contract import (
    StrategyCompileResult,
    StrategyCompiler,
    normalize_lookup_filter_policy,
    resolve_lookup_title_match_mode,
)


@dataclass(frozen=True)
class RuntimeCompilePolicyResult:
    """planner compile 결과와 runtime filter/search 정책을 함께 담는 객체다."""
    compiled_strategy: StrategyCompileResult
    lookup_filter_policy: str
    lookup_title_filter_policy: str
    title_match_mode: str
    relation_lookup_enforce: bool
    search_filter_enabled: bool
    lookup_filter_enabled: bool
    join_hop1_lookup_filter_enabled: bool
    people_promote_one_must: bool
    search_filter_server_policy: str


def assemble_runtime_compile_policy(
    *,
    plan_mode: str,
    action: Optional[str],
    output_type: Optional[str],
    relation: Optional[Tuple[str, str]],
    base_route: str,
    planner_mode: Optional[str],
    planner_filter_spec: Optional[dict[str, Any]],
    target_cols: list[str],
    default_target_cols: list[str],
    topk_spec: Optional[dict[str, Any]],
    rerank_spec: Optional[dict[str, Any]],
    search_filter_signal: bool,
    search_filter_conf_ok: bool,
    lookup_filter_policy_hint: Optional[str],
    title_terms: list[str],
    people_terms: list[str],
    people_ids: list[str],
    has_relation_join_ids: bool,
    title_text_match_supported: bool,
    log_kv: Callable[..., None],
) -> RuntimeCompilePolicyResult:
    """planner 전략, 환경설정, 검색 신호를 합쳐 runtime compile 정책을 만든다.

    title filter 정책, relation lookup enforcement, search/lookup filter enablement를 여기서 고정해 이후 실행 단계가 같은 판단을 반복하지 않게 한다.
    """
    lookup_filter_policy_raw = lookup_filter_policy_hint or os.getenv("RAG_LOOKUP_FILTER_POLICY", "hard")
    lookup_filter_policy = normalize_lookup_filter_policy(lookup_filter_policy_raw)
    if lookup_filter_policy is None:
        logger.warning(
            "invalid RAG_LOOKUP_FILTER_POLICY=%s, falling back to 'hard'",
            lookup_filter_policy_raw,
        )
        lookup_filter_policy = "hard"

    lookup_title_filter_policy_raw = str(os.getenv("RAG_LOOKUP_TITLE_FILTER_POLICY", "soft")).strip().lower()
    lookup_title_filter_policy = "soft"
    if lookup_title_filter_policy_raw not in ("", "soft"):
        logger.warning(
            "title server filter policy is fixed to soft; ignore RAG_LOOKUP_TITLE_FILTER_POLICY=%s",
            lookup_title_filter_policy_raw,
        )

    detail_lookup_request = bool(
        str(plan_mode or "").strip().lower() == "lookup"
        and (
            str(action or "").strip().lower() == "detail"
            or str(output_type or "").strip().lower() == "detail"
        )
    )
    title_match_mode = resolve_lookup_title_match_mode(
        lookup_title_filter_policy=lookup_title_filter_policy,
        index_supports_text=title_text_match_supported,
    )
    log_kv(
        "RAG.LOOKUP.TITLE_FILTER_POLICY",
        policy=lookup_title_filter_policy,
        title_match_mode=title_match_mode,
        title_text_match_supported=int(title_text_match_supported),
        mode=plan_mode,
        action=action,
        output_type=output_type,
        detail_lookup_request=detail_lookup_request,
        title_terms=title_terms[:4],
        tier="debug",
    )

    relation_mode_conflict = bool(relation and plan_mode in ("search", "lookup"))
    if relation_mode_conflict:
        logger.warning(
            "relation-mode conflict detected (mode=%s, relation=%s, payload_mode=%s)",
            plan_mode,
            relation,
            planner_mode,
        )
        log_kv(
            "RAG.PLAN.MODE_CONFLICT",
            level="warning",
            mode=plan_mode,
            relation=relation,
            payload_mode=planner_mode,
            base_route=base_route,
            action=action,
            tier="debug",
        )
    else:
        log_kv(
            "RAG.PLAN.MODE_CONFLICT",
            level="info",
            mode=plan_mode,
            relation=relation,
            payload_mode=planner_mode,
            base_route=base_route,
            action=action,
            conflict=0,
            tier="debug",
        )

    relation_lookup_policy = str(os.getenv("RAG_RELATION_LOOKUP_POLICY", "filter")).strip().lower()
    if relation_lookup_policy not in ("filter", "join"):
        logger.warning(
            "invalid RAG_RELATION_LOOKUP_POLICY=%s, falling back to 'filter'",
            relation_lookup_policy,
        )
        relation_lookup_policy = "filter"

    relation_action = bool(relation and action == "relation")
    relation_lookup_enforce_raw = (planner_filter_spec or {}).get("relation_lookup_enforce")
    relation_lookup_enforce = str(relation_lookup_enforce_raw).strip().lower() in ("1", "true", "yes", "y")
    if relation and plan_mode == "lookup":
        if relation_action:
            logger.warning(
                "lookup+relation(action) requested to join (relation=%s)",
                relation,
            )
        elif relation_lookup_policy == "join" and has_relation_join_ids:
            logger.warning(
                "lookup+relation requested to join (policy=%s, relation=%s)",
                relation_lookup_policy,
                relation,
            )
        if relation_lookup_enforce:
            logger.warning(
                "relation_lookup_enforce=1 enforcing relation filters in lookup (policy=%s, relation=%s)",
                relation_lookup_policy,
                relation,
            )

        log_kv(
            "RAG.PLAN.RELATION_LOOKUP_POLICY",
            level="warning",
            policy=relation_lookup_policy,
            mode=plan_mode,
            relation=relation,
            enforced=int(relation_lookup_enforce),
            source="planner_filter_contract",
            tier="debug",
        )

    compiled_strategy = StrategyCompiler.compile(
        mode=plan_mode,
        relation=relation,
        target_cols=target_cols,
        default_target_cols=default_target_cols,
        planner_filter_spec=planner_filter_spec,
        topk_spec=topk_spec,
        rerank_spec=rerank_spec,
        search_filter_signal=search_filter_signal,
        search_filter_conf_ok=search_filter_conf_ok,
        lookup_filter_policy_hint=lookup_filter_policy,
        lookup_title_filter_policy_hint=lookup_title_filter_policy,
        detail_lookup_request=detail_lookup_request,
        title_text_match_supported=title_text_match_supported,
    )
    if compiled_strategy.hop1_spec or compiled_strategy.hop2_spec:
        log_kv(
            "RAG.JOIN.HOP.COMPILED",
            hop1_spec=compiled_strategy.hop1_spec,
            hop2_spec=compiled_strategy.hop2_spec,
            tier="debug",
        )

    search_filter_enabled = bool(compiled_strategy.search_filter_enabled)
    lookup_filter_enabled = bool(compiled_strategy.lookup_filter_enabled)
    lookup_filter_policy = compiled_strategy.lookup_filter_policy
    lookup_title_filter_policy = compiled_strategy.lookup_title_filter_policy
    title_match_mode = compiled_strategy.title_match_mode
    relation_lookup_enforce = bool(compiled_strategy.relation_lookup_enforce)

    people_promote_one_must = bool(
        lookup_filter_enabled
        and lookup_filter_policy == "must_one_then_should"
        and not people_ids
        and len(people_terms) == 1
    )
    join_hop1_lookup_filter_enabled = bool(
        plan_mode == "join"
        and (
            relation_lookup_enforce
            or (base_route == "project" and relation == ("project", "perf") and bool(people_terms))
        )
    )
    if join_hop1_lookup_filter_enabled:
        log_kv(
            "RAG.JOIN.HOP1.LOOKUP_FILTER.ENFORCE",
            mode=plan_mode,
            base_route=base_route,
            relation=relation,
            people_terms=people_terms[:4],
            relation_lookup_enforce=int(relation_lookup_enforce),
            tier="debug",
        )

    search_filter_server_policy = "disabled" if plan_mode == "search" else "lookup_only"

    return RuntimeCompilePolicyResult(
        compiled_strategy=compiled_strategy,
        lookup_filter_policy=lookup_filter_policy,
        lookup_title_filter_policy=lookup_title_filter_policy,
        title_match_mode=title_match_mode,
        relation_lookup_enforce=relation_lookup_enforce,
        search_filter_enabled=search_filter_enabled,
        lookup_filter_enabled=lookup_filter_enabled,
        join_hop1_lookup_filter_enabled=join_hop1_lookup_filter_enabled,
        people_promote_one_must=people_promote_one_must,
        search_filter_server_policy=search_filter_server_policy,
    )
