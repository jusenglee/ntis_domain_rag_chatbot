from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.platform.runtime_strategy_policy import RECOVERY_POLICIES, RuntimeStrategyPolicy, resolve_runtime_strategy_policy
from apps.retrieval.tools.project_tools import fetch_project_detail, search_projects_by_text
from apps.retrieval.tools.route_search_tools import search_route_by_text
from apps.retrieval.tools.relation_tools import fetch_project_performance
from apps.retrieval.tools.types import ToolResult


@dataclass(frozen=True)
class ExecutionInput:
    question_analysis: Any
    retrieval_query: str
    target_cols: list[str]
    request_meta: dict[str, Any] = field(default_factory=dict)
    collaborator_bundle: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionOutcome:
    docs: list[dict[str, Any]] = field(default_factory=list)
    canonical_evidence: list[dict[str, Any]] = field(default_factory=list)
    render_profile: dict[str, Any] = field(default_factory=dict)
    execution_trace: list[dict[str, Any]] = field(default_factory=list)
    no_result_message: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


_TOOL_REGISTRY = {
    "fetch_project_detail": fetch_project_detail,
    "search_projects_by_text": search_projects_by_text,
    "search_route_by_text": search_route_by_text,
    "fetch_project_performance": fetch_project_performance,
}

_RELAXABLE_SEARCH_FILTER_KEYS = frozenset(
    {
        "participant_researcher_name",
        "org_name",
        "lead_org_name",
        "participant_org_name",
        "people_affiliation_org_name",
    }
)


class ExecutionManager:
    """Execute bounded tool flows without touching workflow state directly."""

    def execute(self, execution_input: ExecutionInput) -> ExecutionOutcome:
        policy = resolve_runtime_strategy_policy(execution_input.question_analysis)
        requested_policy_name = str((execution_input.request_meta or {}).get("runtime_policy_name") or "").strip()
        if requested_policy_name:
            policy = RECOVERY_POLICIES.get(requested_policy_name) or policy
        if policy is None:
            return ExecutionOutcome(
                execution_trace=[
                    {
                        "step": 0,
                        "policy": None,
                        "tool_name": None,
                        "status": "skipped",
                        "reason": "no_runtime_strategy_policy",
                    }
                ],
                diagnostics={"reason": "no_runtime_strategy_policy"},
            )

        if policy.name == "LOOKUP_MISSING_RECOVERY":
            return self._execute_lookup_missing_recovery(execution_input, policy)
        if policy.name == "SEARCH_RECOVERY":
            return self._execute_search_recovery(execution_input, policy)
        if policy.name in {"PEOPLE_SEARCH_OBSERVATION", "ORG_SEARCH_OBSERVATION", "PERF_SEARCH_OBSERVATION"}:
            return self._execute_route_search_observation(execution_input, policy)
        if policy.name == "JOIN_QUALITY_RECOVERY":
            return self._execute_join_quality_probe(execution_input, policy)
        return ExecutionOutcome(
            execution_trace=[
                {
                    "step": 0,
                    "policy": policy.name,
                    "tool_name": None,
                    "status": "skipped",
                    "reason": "policy_not_implemented",
                }
            ],
            diagnostics={"reason": "policy_not_implemented"},
        )

    def _execute_lookup_missing_recovery(
        self,
        execution_input: ExecutionInput,
        policy: RuntimeStrategyPolicy,
    ) -> ExecutionOutcome:
        qa = execution_input.question_analysis
        ids_map = dict(getattr(qa, "ids_map", {}) or {})
        project_ids = list(ids_map.get("pjt_id") or [])
        project_id = _first_text_value(
            project_ids[0] if project_ids else None,
            execution_input.request_meta.get("pjt_id"),
            execution_input.request_meta.get("anchor_pjt_id"),
        )
        filters = dict(execution_input.request_meta.get("filters") or {})
        limit = max(1, int(execution_input.request_meta.get("limit") or 1))
        exact_id_query = bool(execution_input.request_meta.get("is_exact_id_query"))
        trace: list[dict[str, Any]] = []

        if not project_id:
            return ExecutionOutcome(
                execution_trace=[
                    {
                        "step": 0,
                        "policy": policy.name,
                        "tool_name": policy.primary,
                        "status": "skipped",
                        "reason": "missing_pjt_id",
                    }
                ],
                no_result_message="요청하신 과제 식별자를 확인하지 못했습니다.",
                diagnostics={"reason": "missing_pjt_id"},
            )

        primary_result = _TOOL_REGISTRY[policy.primary](
            pjt_id=project_id,
            target_cols=list(execution_input.target_cols or []),
            collaborators=execution_input.collaborator_bundle,
        )
        primary_observation_codes: list[str] = []
        if primary_result.status != "success":
            primary_observation_codes.append("empty_primary_search")
        trace.append(
            _build_trace_entry(
                step=1,
                phase="primary",
                policy=policy,
                result=primary_result,
                observation_codes=primary_observation_codes if primary_observation_codes else None,
            )
        )
        if primary_result.status == "success":
            return _tool_result_to_outcome(primary_result, trace=trace)

        if exact_id_query and policy.exact_id_no_relax:
            message = _resolve_no_result_message(
                primary_result,
                default_message="요청하신 식별자로는 과제를 찾지 못했습니다.",
            )
            trace.append(
                {
                    "step": 2,
                    "phase": "policy_gate",
                    "policy": policy.name,
                    "tool_name": policy.secondary,
                    "status": "blocked",
                    "reason": "exact_id_no_relax",
                }
            )
            return ExecutionOutcome(
                execution_trace=trace,
                no_result_message=message,
                diagnostics={"recovery_blocked": True, "reason": "exact_id_no_relax"},
            )

        secondary_result = _TOOL_REGISTRY[policy.secondary](
            query=str(execution_input.retrieval_query or project_id),
            filters=filters if policy.inherit_filters else {},
            limit=limit,
            collaborators=execution_input.collaborator_bundle,
        )
        recovery_notice = str(policy.user_notice_template or "").strip() or None
        secondary_observation_codes = list(primary_observation_codes)
        if secondary_result.status == "success":
            secondary_observation_codes.append("search_recovery_succeeded")
        trace.append(
            _build_trace_entry(
                step=2,
                phase="secondary",
                policy=policy,
                result=secondary_result,
                reason="lookup_empty",
                user_notice=recovery_notice if secondary_result.status == "success" else None,
                observation_codes=secondary_observation_codes if secondary_observation_codes else None,
            )
        )

        if secondary_result.status == "success":
            diagnostics = dict(secondary_result.diagnostics or {})
            diagnostics.update(
                {
                    "recovery_applied": True,
                    "recovery_policy": policy.name,
                    "recovery_user_notice": recovery_notice,
                }
            )
            return ExecutionOutcome(
                docs=list(secondary_result.rows),
                canonical_evidence=list(secondary_result.canonical_evidence),
                render_profile=dict(secondary_result.render_profile or {}),
                execution_trace=trace,
                no_result_message=None,
                diagnostics=diagnostics,
            )

        no_result_message = _resolve_no_result_message(
            secondary_result,
            default_message=_resolve_no_result_message(
                primary_result,
                default_message="관련 과제를 찾지 못했습니다.",
            ),
        )
        return ExecutionOutcome(
            execution_trace=trace,
            no_result_message=no_result_message,
            diagnostics={"recovery_applied": False, "recovery_policy": policy.name},
        )

    def _execute_search_recovery(
        self,
        execution_input: ExecutionInput,
        policy: RuntimeStrategyPolicy,
    ) -> ExecutionOutcome:
        query_text = str(execution_input.retrieval_query or "").strip()
        filters = dict(execution_input.request_meta.get("filters") or {})
        limit = max(1, int(execution_input.request_meta.get("limit") or 1))
        exact_id_query = bool(execution_input.request_meta.get("is_exact_id_query"))
        project_axis_locked = bool(execution_input.request_meta.get("project_axis_locked"))
        has_anchor_seed = bool(execution_input.request_meta.get("has_anchor_seed"))
        trace: list[dict[str, Any]] = []

        if not query_text:
            return ExecutionOutcome(
                execution_trace=[
                    {
                        "step": 0,
                        "policy": policy.name,
                        "tool_name": policy.primary,
                        "status": "skipped",
                        "reason": "missing_retrieval_query",
                    }
                ],
                no_result_message="검색어를 확인하지 못했습니다.",
                diagnostics={"reason": "missing_retrieval_query"},
            )

        primary_result = _TOOL_REGISTRY[policy.primary](
            query=query_text,
            filters=filters if policy.inherit_filters else {},
            limit=limit,
            collaborators=execution_input.collaborator_bundle,
        )
        primary_observation_codes: list[str] = []
        if primary_result.status == "empty":
            primary_observation_codes.append("empty_primary_search")
            if _has_relaxable_search_filters(filters):
                primary_observation_codes.append("filter_gate_too_strict")
        elif primary_result.status == "success" and 0 < len(primary_result.rows) < limit:
            primary_observation_codes.append("candidate_underflow")

        trace.append(
            _build_trace_entry(
                step=1,
                phase="primary",
                policy=policy,
                result=primary_result,
                observation_codes=primary_observation_codes,
            )
        )

        if primary_result.status != "empty":
            return _tool_result_to_outcome(
                primary_result,
                trace=trace,
                diagnostics_overrides=_build_observation_diagnostics(primary_observation_codes),
            )

        if exact_id_query or project_axis_locked or has_anchor_seed:
            blocked_codes = _merge_observation_codes(primary_observation_codes, ["relax_budget_exhausted"])
            trace.append(
                {
                    "step": 2,
                    "phase": "policy_gate",
                    "policy": policy.name,
                    "tool_name": policy.secondary,
                    "status": "blocked",
                    "reason": "search_contract_locked",
                    "observation_codes": blocked_codes,
                }
            )
            return ExecutionOutcome(
                execution_trace=trace,
                no_result_message=_resolve_no_result_message(
                    primary_result,
                    default_message="관련 과제를 찾지 못했습니다.",
                ),
                diagnostics={
                    "recovery_applied": False,
                    "recovery_blocked": True,
                    "recovery_policy": policy.name,
                    "reason": "search_contract_locked",
                    "observation_codes": blocked_codes,
                },
            )

        relaxed_filters, dropped_filter_keys = _relax_search_filters(filters)
        if not policy.auto_recovery_enabled or int(policy.relax_budget or 0) < 1 or not dropped_filter_keys:
            blocked_codes = _merge_observation_codes(primary_observation_codes, ["relax_budget_exhausted"])
            trace.append(
                {
                    "step": 2,
                    "phase": "policy_gate",
                    "policy": policy.name,
                    "tool_name": policy.secondary,
                    "status": "blocked",
                    "reason": "no_relaxable_filters",
                    "observation_codes": blocked_codes,
                    "dropped_filter_keys": dropped_filter_keys,
                }
            )
            return ExecutionOutcome(
                execution_trace=trace,
                no_result_message=_resolve_no_result_message(
                    primary_result,
                    default_message="관련 과제를 찾지 못했습니다.",
                ),
                diagnostics={
                    "recovery_applied": False,
                    "recovery_blocked": True,
                    "recovery_policy": policy.name,
                    "reason": "no_relaxable_filters",
                    "observation_codes": blocked_codes,
                    "dropped_filter_keys": dropped_filter_keys,
                },
            )

        secondary_result = _TOOL_REGISTRY[policy.secondary](
            query=query_text,
            filters=relaxed_filters if policy.inherit_filters else {},
            limit=limit,
            collaborators=execution_input.collaborator_bundle,
        )
        recovery_notice = str(policy.user_notice_template or "").strip() or None
        secondary_observation_codes = list(primary_observation_codes)
        if secondary_result.status == "success":
            secondary_observation_codes.append("search_recovery_succeeded")
            if 0 < len(secondary_result.rows) < limit:
                secondary_observation_codes.append("candidate_underflow")
        else:
            secondary_observation_codes.append("relax_budget_exhausted")

        trace.append(
            _build_trace_entry(
                step=2,
                phase="secondary",
                policy=policy,
                result=secondary_result,
                reason="primary_search_empty",
                user_notice=recovery_notice if secondary_result.status == "success" else None,
                observation_codes=secondary_observation_codes,
                extra_diagnostics={"dropped_filter_keys": dropped_filter_keys},
            )
        )

        if secondary_result.status == "success":
            diagnostics = {
                "recovery_applied": True,
                "recovery_policy": policy.name,
                "recovery_user_notice": recovery_notice,
                "dropped_filter_keys": dropped_filter_keys,
            }
            diagnostics.update(_build_observation_diagnostics(secondary_observation_codes))
            return _tool_result_to_outcome(
                secondary_result,
                trace=trace,
                diagnostics_overrides=diagnostics,
            )

        no_result_message = _resolve_no_result_message(
            secondary_result,
            default_message=_resolve_no_result_message(
                primary_result,
                default_message="관련 과제를 찾지 못했습니다.",
            ),
        )
        return ExecutionOutcome(
            execution_trace=trace,
            no_result_message=no_result_message,
            diagnostics={
                "recovery_applied": False,
                "recovery_policy": policy.name,
                "dropped_filter_keys": dropped_filter_keys,
                "observation_codes": secondary_observation_codes,
            },
        )

    def _execute_join_quality_probe(
        self,
        execution_input: ExecutionInput,
        policy: RuntimeStrategyPolicy,
    ) -> ExecutionOutcome:
        qa = execution_input.question_analysis
        ids_map = dict(getattr(qa, "ids_map", {}) or {})
        join_key_mode = str(getattr(qa, "join_key_mode", "") or "").strip().lower()
        join_axis = "pjt_no" if join_key_mode == "group" else "pjt_id"
        project_keys = list(ids_map.get(join_axis) or [])
        request_meta = dict(execution_input.request_meta or {})
        relation_result = _TOOL_REGISTRY[policy.primary](
            pjt_ids=project_keys,
            join_key_mode=join_key_mode,
            collaborators=execution_input.collaborator_bundle,
        )
        base_diagnostics = {
            "join_key_mode": join_key_mode,
            "join_axis": join_axis,
            "group_recovery_attempted": False,
            "group_recovery_applied": False,
        }
        primary_reason = "observation_only"
        trace: list[dict[str, Any]] = []

        if join_key_mode == "instance" and relation_result.status == "empty":
            primary_reason = "primary_join_empty"
        trace.append(
            _build_trace_entry(
                step=1,
                phase="primary",
                policy=policy,
                result=relation_result,
                reason=primary_reason,
                extra_diagnostics=base_diagnostics,
            )
        )

        if join_key_mode != "instance" or relation_result.status != "empty":
            return _tool_result_to_outcome(
                relation_result,
                trace=trace,
                diagnostics_overrides=base_diagnostics,
            )

        anchor_pjt_no = str(request_meta.get("anchor_pjt_no") or "").strip()
        anchor_source = str(request_meta.get("anchor_source") or "").strip() or "active_anchor"
        exact_id_query = bool(request_meta.get("is_exact_id_query"))
        project_axis_locked = bool(request_meta.get("project_axis_locked"))
        fallback_block_reason = ""
        if exact_id_query or project_axis_locked:
            fallback_block_reason = "instance_axis_locked"
        elif not project_keys:
            fallback_block_reason = "missing_instance_join_keys"
        elif not anchor_pjt_no:
            fallback_block_reason = "missing_anchor_pjt_no"
        elif not policy.auto_recovery_enabled or int(policy.max_steps or 1) < 2:
            fallback_block_reason = "group_recovery_disabled"

        if fallback_block_reason:
            diagnostics = dict(base_diagnostics)
            diagnostics.update(
                {
                    "recovery_join_axis": "pjt_no",
                    "recovery_source": anchor_source,
                    "recovery_blocked": True,
                    "reason": fallback_block_reason,
                }
            )
            trace.append(
                {
                    "step": 2,
                    "phase": "policy_gate",
                    "policy": policy.name,
                    "tool_name": policy.secondary or policy.primary,
                    "status": "blocked",
                    "reason": fallback_block_reason,
                    "diagnostics": diagnostics,
                }
            )
            return ExecutionOutcome(
                execution_trace=trace,
                no_result_message=_resolve_no_result_message(
                    relation_result,
                    default_message="관련 성과를 찾지 못했습니다.",
                ),
                diagnostics=diagnostics,
            )

        secondary_result = _TOOL_REGISTRY[policy.secondary or policy.primary](
            pjt_ids=[anchor_pjt_no],
            join_key_mode="group",
            collaborators=execution_input.collaborator_bundle,
        )
        recovery_diagnostics = {
            "join_key_mode": join_key_mode,
            "join_axis": join_axis,
            "group_recovery_attempted": True,
            "group_recovery_applied": secondary_result.status == "success",
            "recovery_join_axis": "pjt_no",
            "recovery_source": anchor_source,
        }
        trace.append(
            _build_trace_entry(
                step=2,
                phase="secondary",
                policy=policy,
                result=secondary_result,
                reason="group_recovery_from_anchor",
                extra_diagnostics=recovery_diagnostics,
            )
        )
        if secondary_result.status == "success":
            return _tool_result_to_outcome(
                secondary_result,
                trace=trace,
                diagnostics_overrides=recovery_diagnostics,
            )

        return ExecutionOutcome(
            execution_trace=trace,
            no_result_message=_resolve_no_result_message(
                secondary_result,
                default_message=_resolve_no_result_message(
                    relation_result,
                    default_message="관련 성과를 찾지 못했습니다.",
                ),
            ),
            diagnostics=recovery_diagnostics,
        )

    def _execute_route_search_observation(
        self,
        execution_input: ExecutionInput,
        policy: RuntimeStrategyPolicy,
    ) -> ExecutionOutcome:
        base_route = str(
            execution_input.request_meta.get("base_route")
            or getattr(execution_input.question_analysis, "head", "")
            or ""
        ).strip().lower()
        query_text = str(execution_input.retrieval_query or "").strip()
        filters = dict(execution_input.request_meta.get("filters") or {})
        limit = max(1, int(execution_input.request_meta.get("limit") or 1))
        output_type = str(getattr(execution_input.question_analysis, "output_type", "") or "summary").strip().lower()

        if not query_text:
            return ExecutionOutcome(
                execution_trace=[
                    {
                        "step": 0,
                        "policy": policy.name,
                        "tool_name": policy.primary,
                        "status": "skipped",
                        "reason": "missing_retrieval_query",
                    }
                ],
                no_result_message="검색어를 확인하지 못했습니다.",
                diagnostics={"reason": "missing_retrieval_query", "base_route": base_route, "observation_only": True},
            )

        primary_result = _TOOL_REGISTRY[policy.primary](
            base_route=base_route,
            query=query_text,
            filters=filters if policy.inherit_filters else {},
            limit=limit,
            target_cols=list(execution_input.target_cols or []),
            collaborators=execution_input.collaborator_bundle,
        )
        docs_kind = _classify_docs_kind(primary_result.rows)
        display_source = _resolve_display_source(
            base_route=base_route,
            output_type=output_type,
            docs=primary_result.rows,
            canonical_evidence=primary_result.canonical_evidence,
            limit=limit,
        )
        diagnostics = {
            "base_route": base_route,
            "docs_count": len(primary_result.rows),
            "canonical_count": len(primary_result.canonical_evidence),
            "docs_kind": docs_kind,
            "display_source": display_source,
            "observation_only": True,
        }
        trace = [
            _build_trace_entry(
                step=1,
                phase="primary",
                policy=policy,
                result=primary_result,
                reason="observation_only",
                observation_codes=list(policy.observation_codes or ()),
                extra_diagnostics=diagnostics,
            )
        ]
        return _tool_result_to_outcome(
            primary_result,
            trace=trace,
            diagnostics_overrides=diagnostics,
        )


def _build_trace_entry(
    *,
    step: int,
    phase: str,
    policy: RuntimeStrategyPolicy,
    result: ToolResult,
    reason: str | None = None,
    user_notice: str | None = None,
    observation_codes: list[str] | None = None,
    extra_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = {
        "step": step,
        "phase": phase,
        "policy": policy.name,
        "tool_name": result.tool_name,
        "status": result.status,
        "row_count": len(result.rows),
        "canonical_count": len(result.canonical_evidence),
        "latency_ms": round(float(result.latency_ms or 0.0), 3),
    }
    if reason:
        entry["reason"] = reason
    if user_notice:
        entry["user_notice"] = user_notice
    diagnostics = dict(result.diagnostics or {})
    if extra_diagnostics:
        diagnostics.update(dict(extra_diagnostics or {}))
    if diagnostics:
        entry["diagnostics"] = diagnostics
    merged_codes = _merge_observation_codes((result.diagnostics or {}).get("observation_codes"), observation_codes)
    if merged_codes:
        entry["observation_codes"] = merged_codes
    return entry


def _resolve_no_result_message(result: ToolResult, *, default_message: str) -> str:
    message = str((result.diagnostics or {}).get("no_result_message") or "").strip()
    return message or default_message


def _tool_result_to_outcome(
    result: ToolResult,
    *,
    trace: list[dict[str, Any]],
    diagnostics_overrides: dict[str, Any] | None = None,
) -> ExecutionOutcome:
    message = None
    if result.status != "success":
        message = _resolve_no_result_message(result, default_message="관련 데이터를 찾지 못했습니다.")
    diagnostics = dict(result.diagnostics or {})
    if diagnostics_overrides:
        diagnostics.update(dict(diagnostics_overrides or {}))
    return ExecutionOutcome(
        docs=list(result.rows),
        canonical_evidence=list(result.canonical_evidence),
        render_profile=dict(result.render_profile or {}),
        execution_trace=list(trace),
        no_result_message=message,
        diagnostics=diagnostics,
    )


def _has_relaxable_search_filters(filters: dict[str, Any]) -> bool:
    return any(key in _RELAXABLE_SEARCH_FILTER_KEYS for key in dict(filters or {}))


def _relax_search_filters(filters: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    dropped_keys = sorted(key for key in dict(filters or {}) if key in _RELAXABLE_SEARCH_FILTER_KEYS)
    relaxed_filters = {
        key: value for key, value in dict(filters or {}).items() if key not in _RELAXABLE_SEARCH_FILTER_KEYS
    }
    return relaxed_filters, dropped_keys


def _merge_observation_codes(*sources: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for source in sources:
        if isinstance(source, str):
            iterable = [source]
        elif isinstance(source, (list, tuple, set)):
            iterable = list(source)
        else:
            iterable = []
        for value in iterable:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


def _first_text_value(*values: Any) -> str:
    for value in values:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                text = str(item or "").strip()
                if text:
                    return text
            continue
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _build_observation_diagnostics(observation_codes: list[str]) -> dict[str, Any]:
    codes = _merge_observation_codes(observation_codes)
    return {"observation_codes": codes} if codes else {}


def _classify_docs_kind(docs: list[dict[str, Any]]) -> str:
    if not docs:
        return "item_list"
    source_types = {str(item.get("source_type") or "").strip().lower() for item in docs if isinstance(item, dict)}
    source_types.discard("")
    item_like_types = {"hit", "canonical_item", "item", "document"}
    if source_types and source_types.issubset(item_like_types):
        return "item_list"
    if "aggregation" in source_types:
        return "collection_wrapper"
    return "item_list"


def _resolve_display_source(
    *,
    base_route: str,
    output_type: str,
    docs: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    limit: int,
) -> str:
    normalized_output_type = str(output_type or "").strip().lower()
    normalized_route = str(base_route or "").strip().lower()
    docs_kind = _classify_docs_kind(docs)
    docs_count = len(docs or [])
    canonical_count = len(canonical_evidence or [])
    threshold = max(1, int(limit or 1))

    if (
        normalized_output_type in {"list", "relation", "comparison", "series", "stats"}
        and canonical_count
        and docs_kind == "collection_wrapper"
        and normalized_route in {"people", "org"}
    ):
        return "canonical_axis"

    if (
        normalized_output_type == "list"
        and canonical_count > docs_count
        and normalized_route in {"people", "org"}
        and (docs_kind == "collection_wrapper" or docs_count < threshold)
    ):
        return "synthetic_from_canonical"

    return "docs"
