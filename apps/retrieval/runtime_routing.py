from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.platform.rag_constants import COL_PERF, COL_PROJECT, COL_SUPPORT


DEFAULT_PROJECT_TARGET_COL = "ntis_project_v1"
ANSWER_FANOUT = ["generate_answer_gemma", "generate_answer_solar"]
MAX_SEARCH_RETRIES = 2


@dataclass(frozen=True)
class RuntimeDispatchPlan:
    runtime_owner: str
    policy_name: str | None
    orchestrator_owned: bool
    legacy_retry_allowed: bool
    use_execution_manager: bool
    execution_kind: str
    target_cols: list[str] = field(default_factory=list)
    request_meta: dict[str, Any] = field(default_factory=dict)

    def build_runtime_meta(self, *, diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
        merged_diagnostics = dict(diagnostics or {})
        meta = {
            "runtime_owner": self.runtime_owner,
            "policy_name": self.policy_name,
            "orchestrator_owned": self.orchestrator_owned,
            "legacy_retry_allowed": self.legacy_retry_allowed,
            "use_execution_manager": self.use_execution_manager,
            "execution_kind": self.execution_kind,
            "recovery_applied": bool(merged_diagnostics.get("recovery_applied")),
        }
        for key in (
            "join_axis",
            "join_key_mode",
            "join_observation_only",
            "base_route",
            "observation_only",
            "group_recovery_attempted",
            "group_recovery_applied",
            "recovery_join_axis",
            "recovery_source",
        ):
            value = merged_diagnostics.get(key, self.request_meta.get(key))
            if value is not None:
                meta[key] = value
        return meta


def build_runtime_dispatch_plan(
    *,
    qa: Any,
    query_intent: Any,
    focus_seed_map: Any,
    prefer_fresh_retrieval: bool,
    search_num: int,
    resolved_retrieval_query: str,
) -> RuntimeDispatchPlan:
    mode = str(getattr(qa, "mode", "") or "").strip().upper()
    base_route = str(
        _pick_attr(query_intent, qa, key="base_route", default=getattr(qa, "head", None))
        or getattr(qa, "head", None)
        or ""
    ).strip().lower()
    hard_contract = getattr(qa, "hard_contract", None)
    ids_map = dict(getattr(qa, "ids_map", {}) or {})
    tool_project_id = str(
        _first_text_value(
            (focus_seed_map.get("pjt_id") or [None])[0] if isinstance(focus_seed_map, dict) else None,
            (ids_map.get("pjt_id") or [None])[0],
        )
        or ""
    ).strip()
    tool_project_no = str(
        _first_text_value(
            (focus_seed_map.get("pjt_no") or [None])[0] if isinstance(focus_seed_map, dict) else None,
            (ids_map.get("pjt_no") or [None])[0],
        )
        or ""
    ).strip()
    exact_id_query = bool(
        getattr(query_intent, "is_exact_key_query", False)
        or getattr(query_intent, "is_id_query", False)
        or bool(getattr(hard_contract, "explicit_project_id_label", False))
        or bool(getattr(hard_contract, "explicit_project_no_label", False))
    )
    project_axis_locked = bool(
        tool_project_id
        or tool_project_no
        or bool(getattr(hard_contract, "explicit_project_id_label", False))
        or bool(getattr(hard_contract, "explicit_project_no_label", False))
        or bool(getattr(hard_contract, "project_key_axis_locked", False))
        or str(getattr(hard_contract, "resolved_project_key_axis", "") or "").strip()
    )
    has_anchor_seed = bool(isinstance(focus_seed_map, dict) and focus_seed_map)
    project_target_cols = _normalize_project_target_cols(
        getattr(qa, "target_cols", None),
        getattr(query_intent, "target_cols", None),
    )
    route_search_target_cols = _normalize_route_search_target_cols(
        base_route,
        getattr(qa, "target_cols", None),
        getattr(query_intent, "target_cols", None),
    )
    merged_target_cols = _merge_target_cols(
        getattr(qa, "target_cols", None),
        getattr(query_intent, "target_cols", None),
    )
    join_key_mode = str(getattr(qa, "join_key_mode", "") or "").strip().lower()
    join_axis = "pjt_no" if join_key_mode == "group" else "pjt_id"
    join_keys = list(ids_map.get(join_axis) or [])
    common_request_meta = {
        "filters": dict(getattr(qa, "filters", {}) or {}),
        "limit": max(1, int(search_num or 1)),
        "is_exact_id_query": exact_id_query,
        "project_axis_locked": project_axis_locked,
        "has_anchor_seed": has_anchor_seed,
    }

    if not prefer_fresh_retrieval and mode == "LOOKUP" and base_route == "project" and tool_project_id:
        return RuntimeDispatchPlan(
            runtime_owner="execution_manager",
            policy_name="LOOKUP_MISSING_RECOVERY",
            orchestrator_owned=True,
            legacy_retry_allowed=False,
            use_execution_manager=True,
            execution_kind="project_lookup",
            target_cols=project_target_cols,
            request_meta=common_request_meta,
        )

    if not prefer_fresh_retrieval and mode == "SEARCH" and base_route == "project":
        return RuntimeDispatchPlan(
            runtime_owner="execution_manager",
            policy_name="SEARCH_RECOVERY",
            orchestrator_owned=True,
            legacy_retry_allowed=False,
            use_execution_manager=True,
            execution_kind="project_search",
            target_cols=project_target_cols,
            request_meta=common_request_meta,
        )

    if not prefer_fresh_retrieval and mode == "SEARCH" and base_route in {"people", "org", "perf"}:
        route_request_meta = dict(common_request_meta)
        route_request_meta.update({"base_route": base_route, "observation_only": True})
        return RuntimeDispatchPlan(
            runtime_owner="execution_manager",
            policy_name=(
                "PEOPLE_SEARCH_OBSERVATION"
                if base_route == "people"
                else ("ORG_SEARCH_OBSERVATION" if base_route == "org" else "PERF_SEARCH_OBSERVATION")
            ),
            orchestrator_owned=True,
            legacy_retry_allowed=False,
            use_execution_manager=True,
            execution_kind="route_search",
            target_cols=route_search_target_cols,
            request_meta=route_request_meta,
        )

    if (
        not prefer_fresh_retrieval
        and mode == "JOIN"
        and base_route == "project"
        and join_key_mode in {"instance", "group"}
        and join_keys
    ):
        join_request_meta = dict(common_request_meta)
        join_request_meta.update(
            {
                "join_axis": join_axis,
                "join_key_mode": join_key_mode,
                "join_observation_only": True,
            }
        )
        return RuntimeDispatchPlan(
            runtime_owner="execution_manager",
            policy_name="JOIN_QUALITY_RECOVERY",
            orchestrator_owned=True,
            legacy_retry_allowed=False,
            use_execution_manager=True,
            execution_kind="project_join",
            target_cols=merged_target_cols,
            request_meta=join_request_meta,
        )

    return RuntimeDispatchPlan(
        runtime_owner="legacy_retriever",
        policy_name=None,
        orchestrator_owned=False,
        legacy_retry_allowed=True,
        use_execution_manager=False,
        execution_kind="legacy_retriever",
        target_cols=merged_target_cols or project_target_cols,
        request_meta=common_request_meta,
    )


def decide_post_retrieval_route(
    *,
    context: Any,
    retry_count: int,
    qa_mode: str,
    join_key_mode: str | None,
    retrieval_runtime_meta: Any,
) -> str | list[str]:
    if context:
        return list(ANSWER_FANOUT)
    if int(retry_count or 0) >= MAX_SEARCH_RETRIES:
        return list(ANSWER_FANOUT)

    mode = str(qa_mode or "").strip().upper()
    if mode == "LOOKUP":
        return list(ANSWER_FANOUT)
    if mode == "JOIN" and str(join_key_mode or "").strip():
        return list(ANSWER_FANOUT)

    if not can_use_legacy_retry(
        qa_mode=mode,
        retrieval_runtime_meta=retrieval_runtime_meta,
    ):
        return list(ANSWER_FANOUT)
    return "relax_and_retry"


def can_use_legacy_retry(*, qa_mode: str, retrieval_runtime_meta: Any) -> bool:
    mode = str(qa_mode or "").strip().upper()
    if mode != "SEARCH":
        return False

    meta = dict(retrieval_runtime_meta or {})
    return bool(meta.get("legacy_retry_allowed", True))


def _normalize_project_target_cols(*target_cols: Any) -> list[str]:
    project_cols: list[str] = []
    seen: set[str] = set()

    for values in target_cols:
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            if not text.lower().startswith("ntis_project"):
                continue
            seen.add(text)
            project_cols.append(text)

    return project_cols or [DEFAULT_PROJECT_TARGET_COL]


def _normalize_route_search_target_cols(base_route: str, *target_cols: Any) -> list[str]:
    merged_cols = _merge_target_cols(*target_cols)
    if merged_cols:
        return merged_cols
    route = str(base_route or "").strip().lower()
    if route == "perf":
        return [COL_PERF]
    if route == "support":
        return [COL_SUPPORT]
    if route in {"people", "org"}:
        return [COL_PROJECT, COL_PERF]
    return [COL_PROJECT]


def _merge_target_cols(*target_cols: Any) -> list[str]:
    merged_cols: list[str] = []
    seen: set[str] = set()

    for values in target_cols:
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged_cols.append(text)

    return merged_cols


def _pick_attr(*sources: Any, key: str, default: Any = None) -> Any:
    for source in sources:
        if source is None:
            continue
        if isinstance(source, dict):
            value = source.get(key, None)
        else:
            value = getattr(source, key, None)
        if value is not None:
            return value
    return default


def _first_text_value(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text:
                    return text
            continue
        text = str(value or "").strip()
        if text:
            return text
    return None
