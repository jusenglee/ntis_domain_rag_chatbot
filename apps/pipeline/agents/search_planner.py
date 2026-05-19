"""Phase 5: SearchPlannerAgent — DialogueIntent + EntityResolution → SearchPlan.

설계 원칙:
    - 결정적 규칙으로 SearchTask 1개 이상을 만든다. LLM 호출 없음.
    - manifest 해소 결과가 있으면 그것이 진실원이고 추가 task가 필요 없다 (exact_lookup 1개).
    - 사람/기관 활동내역은 project + perf 두 컬렉션 병렬 task로 분리해 같은 subject anchor를 양쪽에서
      매칭한다 (NTIS 데이터는 prtcp_mp가 두 컬렉션에 모두 존재).
    - 비교(compare)는 대상별 task. 향후 stats는 별도 처리.

본 단계 핵심은 “target=people/org면 단일 task로 두 컬렉션을 한 번에 쿼리해도 되는데, 왜 task 두 개로
나누는가?”에 대한 답: 컬렉션마다 임베딩 모델·인덱스가 다르고, latency/diagnostics를 분리해서 보아야
운영 추적이 가능하기 때문이다. Retrieval 단계가 task별 결과를 그대로 다시 합치므로 cost는 동일하다.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from loguru import logger

from apps.pipeline.agents.contracts import (
    DialogueIntent,
    EntityResolution,
    SearchPlan,
)
from apps.pipeline.contracts import (
    Action,
    AggregateBy,
    Axis,
    Collection,
    FilterBundle,
    IdentifierBundle,
    SearchStrategy,
    SearchTask,
    SubjectAnchor,
    Target,
    build_search_task,
)


# ============================================================================
# Default limits
# ============================================================================

_DEFAULT_LIST_LIMIT = 10
_DEFAULT_DETAIL_LIMIT = 1
_DEFAULT_TOPIC_LIMIT = 5


# ============================================================================
# SearchPlannerAgent
# ============================================================================

class SearchPlannerAgent:
    """DialogueIntent + EntityResolution → SearchPlan."""

    def plan(
        self,
        *,
        intent: DialogueIntent,
        resolution: EntityResolution,
        request_id: str,
        turn_id: str,
    ) -> Optional[SearchPlan]:
        """SearchPlan을 만든다. 검색이 불필요한 의도(direct_answer/clarification)면 None.

        호출자 측 분기:
            - None → workflow가 emit_direct_answer / emit_clarification으로 분기
            - SearchPlan → RetrievalAgent에 전달
        """
        if intent.kind in {"direct_answer", "clarification", "ask_meta"}:
            # ask_meta는 manifest item의 tag/axis만 보고 결정적으로 즉답 가능 — 검색 불필요.
            # workflow가 plan=None + kind=ask_meta를 보고 emit_meta_answer로 분기한다.
            return None

        # ---- manifest 해소가 있으면 단일 exact_lookup ----
        if resolution.manifest_rank is not None and resolution.identifiers.has_any():
            task = self._plan_manifest_detail(intent, resolution, request_id, turn_id)
            return SearchPlan(
                tasks=[task],
                merge_strategy="single",
                max_results=1,
                plan_reason="manifest_rank_resolved",
            )

        # ---- 직접 식별자(literal) ----
        if resolution.identifiers.has_any():
            task = self._plan_identifier_lookup(intent, resolution, request_id, turn_id)
            return SearchPlan(
                tasks=[task],
                merge_strategy="single",
                max_results=_action_limit(intent.action_hint or "detail"),
                plan_reason="identifier_literal",
            )

        # ---- compare ----
        if intent.kind == "compare":
            tasks = self._plan_compare(intent, resolution, request_id, turn_id)
            if tasks:
                return SearchPlan(
                    tasks=tasks,
                    merge_strategy="by_axis_groupby",
                    max_results=_action_limit(intent.action_hint or "list"),
                    plan_reason="compare_targets",
                )
            return None

        # ---- stats — aggregate 도구 ----
        if intent.kind == "stats":
            tasks = self._plan_stats(intent, resolution, request_id, turn_id)
            if tasks:
                return SearchPlan(
                    tasks=tasks,
                    merge_strategy="by_axis_groupby",
                    max_results=20,
                    plan_reason="stats_aggregate",
                )
            # subject도 query도 없으면 plan 못 만듦 → workflow가 clarify로 분기
            return None

        # ---- people/org 활동내역 (subject 기반) ----
        if resolution.subject is not None:
            tasks = self._plan_subject_activity(intent, resolution, request_id, turn_id)
            if tasks:
                return SearchPlan(
                    tasks=tasks,
                    merge_strategy="by_score_dedup",
                    max_results=_action_limit(intent.action_hint or "list"),
                    plan_reason="subject_activity",
                )

        # ---- 일반 검색 (subject 없음, identifier 없음, manifest 없음) ----
        task = self._plan_generic_search(intent, resolution, request_id, turn_id)
        if task is None:
            logger.info("[SearchPlanner] no plan: empty intent without resolvable signal")
            return None
        return SearchPlan(
            tasks=[task],
            merge_strategy="single",
            max_results=_action_limit(intent.action_hint or "list"),
            plan_reason="generic_query",
        )

    # ------------------------------------------------------------------
    # Plan branches
    # ------------------------------------------------------------------

    def _plan_manifest_detail(
        self,
        intent: DialogueIntent,
        resolution: EntityResolution,
        request_id: str,
        turn_id: str,
    ) -> SearchTask:
        """manifest_rank로 해소된 단일 detail."""
        target: Target = resolution.manifest_resolved_target or resolution.forced_target or "project"  # type: ignore[assignment]
        axis: Optional[Axis] = resolution.manifest_resolved_axis
        return build_search_task(
            action="detail",
            target=target,
            identifiers=resolution.identifiers,
            filters=resolution.filters,
            axis_hint=axis,
            retrieval_query=intent.query,
            request_id=request_id,
            turn_id=turn_id,
            limit=1,
            display_limit=1,
            judgment_reason=f"manifest_rank={resolution.manifest_rank}",
        )

    def _plan_identifier_lookup(
        self,
        intent: DialogueIntent,
        resolution: EntityResolution,
        request_id: str,
        turn_id: str,
    ) -> SearchTask:
        """식별자 직접 명시 → exact_lookup."""
        target: Target = resolution.forced_target or _default_target_for_identifier(resolution.identifiers)
        action: Action = _normalize_action(intent.action_hint or ("detail" if intent.kind == "ask_detail" else "list"))
        return build_search_task(
            action=action,
            target=target,
            identifiers=resolution.identifiers,
            filters=resolution.filters,
            retrieval_query=intent.query,
            request_id=request_id,
            turn_id=turn_id,
            limit=_action_limit(action),
            display_limit=_action_limit(action),
            judgment_reason="identifier_literal",
        )

    def _plan_stats(
        self,
        intent: DialogueIntent,
        resolution: EntityResolution,
        request_id: str,
        turn_id: str,
    ) -> List[SearchTask]:
        """stats 도구 — aggregate task. subject가 있으면 그 anchor로 좁힘.

        aggregate_by 결정 우선순위:
            1. 쿼리에 "기관별" → "lead_org"
            2. 쿼리에 "분야별"/"유형별"/"종류별" → "tag" (project) 또는 "perf_type" (perf)
            3. 그 외(기본) → "year"
        """
        query = (intent.query or "").lower()
        if "기관별" in query or "기관" in query and "별" in query:
            aggregate_by: AggregateBy = "lead_org"
        elif any(k in query for k in ("분야별", "유형별", "종류별", "타입별")):
            # subject_kind/target에 따라 perf_type vs tag
            forced = resolution.forced_target or "project"
            aggregate_by = "perf_type" if forced == "perf" else "tag"
        else:
            aggregate_by = "year"

        # collections — subject_kind/target에 따라
        target: Target = resolution.forced_target or intent.target_hint or "project"  # type: ignore[assignment]
        if target == "perf":
            collections: List[str] = ["ntis_perf_v1"]
        elif target == "people" or target == "org":
            collections = ["ntis_project_v1", "ntis_perf_v1"]
        else:
            collections = ["ntis_project_v1"]

        tasks: List[SearchTask] = []
        for collection in collections:
            task = build_search_task(
                action="stats",
                target=target,
                subject=resolution.subject,
                identifiers=resolution.identifiers,
                filters=resolution.filters,
                retrieval_query=intent.query,
                request_id=request_id,
                turn_id=turn_id,
                limit=20,
                display_limit=20,
                collections=[collection],  # type: ignore[arg-type]
                aggregate_by=aggregate_by,
                judgment_reason=f"stats_aggregate:{aggregate_by}:{collection}",
            )
            tasks.append(task)
        return tasks

    def _plan_subject_activity(
        self,
        intent: DialogueIntent,
        resolution: EntityResolution,
        request_id: str,
        turn_id: str,
    ) -> List[SearchTask]:
        """사람/기관 활동내역 — project + perf 두 task로 분리."""
        subject = resolution.subject
        target: Target = resolution.forced_target or ("people" if subject.kind == "people" else "org")  # type: ignore[assignment]
        action: Action = _normalize_action(intent.action_hint or "list")
        limit = _action_limit(action)

        tasks: List[SearchTask] = []
        for collection in ("ntis_project_v1", "ntis_perf_v1"):
            task = build_search_task(
                action=action,
                target=target,
                subject=subject,
                filters=resolution.filters,
                retrieval_query=intent.query,
                request_id=request_id,
                turn_id=turn_id,
                limit=limit,
                display_limit=limit,
                collections=[collection],
                judgment_reason=f"subject_activity:{collection}",
            )
            tasks.append(task)
        return tasks

    def _plan_compare(
        self,
        intent: DialogueIntent,
        resolution: EntityResolution,
        request_id: str,
        turn_id: str,
    ) -> List[SearchTask]:
        """compare는 대상별 task."""
        tasks: List[SearchTask] = []
        for ct in intent.compare_targets:
            ct_subject: Optional[SubjectAnchor] = None
            ct_target: Target = "project"
            if ct.kind in {"people", "org"}:
                ct_subject = SubjectAnchor(
                    kind=ct.kind,
                    display_name=ct.name,
                    identity_status="ambiguous_name_only",
                )
                ct_target = ct.kind  # type: ignore[assignment]
            elif ct.kind == "perf":
                ct_target = "perf"
            tasks.append(
                build_search_task(
                    action="list",
                    target=ct_target,
                    subject=ct_subject,
                    filters=resolution.filters,
                    retrieval_query=ct.name,
                    request_id=request_id,
                    turn_id=turn_id,
                    limit=_DEFAULT_LIST_LIMIT,
                    display_limit=_DEFAULT_LIST_LIMIT,
                    judgment_reason=f"compare:{ct.name}",
                )
            )
        return tasks

    def _plan_generic_search(
        self,
        intent: DialogueIntent,
        resolution: EntityResolution,
        request_id: str,
        turn_id: str,
    ) -> Optional[SearchTask]:
        """일반 검색 — subject/identifier 없음. query만 사용해 hybrid."""
        query = (intent.query or "").strip()
        if not query:
            return None
        target: Target = resolution.forced_target or intent.target_hint or "project"  # type: ignore[assignment]
        action: Action = _normalize_action(intent.action_hint or "list")
        return build_search_task(
            action=action,
            target=target,
            filters=resolution.filters,
            retrieval_query=query,
            request_id=request_id,
            turn_id=turn_id,
            limit=_action_limit(action),
            display_limit=_action_limit(action),
            judgment_reason="generic_query",
        )


# ============================================================================
# Helpers
# ============================================================================

def _normalize_action(value: Optional[str]) -> Action:
    """안전 변환. invalid면 list로 폴백."""
    if value in {"list", "detail", "stats", "topic", "download"}:
        return value  # type: ignore[return-value]
    return "list"


def _action_limit(action: Action) -> int:
    if action == "detail":
        return _DEFAULT_DETAIL_LIMIT
    if action == "topic":
        return _DEFAULT_TOPIC_LIMIT
    return _DEFAULT_LIST_LIMIT


def _default_target_for_identifier(identifiers: IdentifierBundle) -> Target:
    """identifier만 있을 때 target 폴백 (forced_target이 없을 때 호출됨)."""
    if identifiers.rst_id:
        return "perf"
    if identifiers.person_no:
        return "people"
    if identifiers.org_id:
        return "org"
    return "project"
