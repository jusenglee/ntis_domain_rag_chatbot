"""Phase 6: RetrievalAgent — SearchPlan(N tasks)을 실행하고 결과를 합친다.

설계 원칙:
    - 기존 SearchAgent.execute(task)를 task 단위로 재활용. 본 모듈은 SearchPlan 차원의
      orchestration(병렬 실행 + merge_strategy 적용 + diagnostics 집계)만 담당.
    - 출력은 SearchResult (CanonicalEvidence 리스트 + status + 진단). 모델 추가 없이 재사용.
    - raw payload는 절대 상위로 흘리지 않는다 — RetrievalAgent를 통과한 시점에서 모든 결과는
      CanonicalEvidence로 정규화되어 있다.

merge_strategy 적용:
    - "single"          : tasks=1 가정. 결과 그대로
    - "by_score_dedup"  : 모든 task의 evidences를 합치고 identity 기준 dedup, score 내림차순
    - "by_axis_groupby" : task별 결과를 보존하되 단일 리스트로 평탄화. 그룹 정보는
                          diagnostics.per_task에 남겨 EvidenceCurator가 활용
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional


from apps.pipeline.agents.contracts import SearchPlan
from apps.pipeline.contracts import (
    CanonicalEvidence,
    SearchResult,
    SearchTask,
)
from apps.pipeline.search_agent import SearchAgent


# ============================================================================
# RetrievalAgent
# ============================================================================

class RetrievalAgent:
    """SearchPlan 실행기.

    기존 SearchAgent를 task 단위 executor로 재사용한다. SearchPlan의 merge_strategy에 따라
    여러 task의 결과를 합쳐 단일 SearchResult로 반환한다.
    """

    def __init__(
        self,
        *,
        qdrant_client: Any,
        embed_e5i: Any,
        embed_e5: Any,
        task_executor: Optional[SearchAgent] = None,
    ) -> None:
        self._executor = task_executor or SearchAgent(
            qdrant_client=qdrant_client,
            embed_e5i=embed_e5i,
            embed_e5=embed_e5,
        )

    async def execute(self, plan: SearchPlan) -> SearchResult:
        """SearchPlan 실행 → 합쳐진 SearchResult.

        - tasks 한 개: 그대로 실행한 결과를 반환 (merge 불필요)
        - tasks 여러 개: 병렬 실행 후 merge_strategy 적용
        """
        t0 = time.perf_counter()

        if len(plan.tasks) == 1:
            return await self._execute_single(plan, plan.tasks[0], t0)

        # 다중 task — 병렬 실행
        results = await asyncio.gather(
            *[self._executor.execute(task) for task in plan.tasks],
            return_exceptions=True,
        )

        per_task_diag: List[Dict[str, Any]] = []
        all_evidences: List[CanonicalEvidence] = []
        error_codes: List[str] = []

        for task, result in zip(plan.tasks, results):
            entry = {
                "strategy": task.strategy,
                "target": task.target,
                "collections": list(task.collections),
                "task_action": task.action,
            }
            if isinstance(result, Exception):
                entry["error"] = repr(result)
                error_codes.append("task_exception")
                per_task_diag.append(entry)
                continue
            entry.update(
                {
                    "status": result.status,
                    "total_hits": result.total_hits,
                    "evidence_n": len(result.evidences),
                    "task_diagnostics": dict(result.diagnostics or {}),
                }
            )
            if result.error_code:
                entry["error_code"] = result.error_code
                error_codes.append(result.error_code)
            per_task_diag.append(entry)
            if result.status not in ("error",):
                all_evidences.extend(result.evidences)

        latency_ms = (time.perf_counter() - t0) * 1000
        merged = self._merge_evidences(all_evidences, strategy=plan.merge_strategy)
        # snapshot_rank 재부여 (1..N) — EvidenceCurator가 다시 재정렬할 수 있지만 일관성 보장.
        renumbered = [
            ev.model_copy(update={"snapshot_rank": i + 1})
            for i, ev in enumerate(merged)
        ]
        renumbered = renumbered[: plan.max_results]
        total_hits_sum = sum(
            int(entry.get("total_hits") or 0) for entry in per_task_diag
        )

        diagnostics: Dict[str, Any] = {
            "merge_strategy": plan.merge_strategy,
            "plan_reason": plan.plan_reason,
            "task_count": len(plan.tasks),
            "latency_ms": latency_ms,
            "per_task": per_task_diag,
            "merged_total": len(merged),
            "returned": len(renumbered),
            "error_codes": error_codes,
        }

        if not renumbered:
            if error_codes and not all_evidences:
                # 모든 task가 error로 종료
                return SearchResult(
                    status="error",
                    error_code="all_tasks_failed",
                    error_detail=";".join(error_codes)[:200],
                    diagnostics=diagnostics,
                )
            return SearchResult(status="empty", evidences=[], total_hits=0, diagnostics=diagnostics)

        # plan.max_results <= action limit; status 결정은 task 의도 따라 다르지만 RetrievalAgent는
        # 결과 1건 이상이면 status="single"로 반환 (refine은 상위 단계에서 결정).
        return SearchResult(
            status="single",
            evidences=renumbered,
            total_hits=total_hits_sum or len(merged),
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------------
    # Single-task fast path
    # ------------------------------------------------------------------

    async def _execute_single(
        self,
        plan: SearchPlan,
        task: SearchTask,
        t0: float,
    ) -> SearchResult:
        """single-task 빠른 경로. multi-task와 동일한 diagnostics 키셋을 보장한다.

        키셋: merge_strategy, plan_reason, task_count, latency_ms, per_task[], merged_total,
        returned, error_codes, retrieval_latency_ms (single-task 호환).
        """
        result = await self._executor.execute(task)
        latency_ms = (time.perf_counter() - t0) * 1000
        per_task_entry: Dict[str, Any] = {
            "strategy": task.strategy,
            "target": task.target,
            "collections": list(task.collections),
            "task_action": task.action,
            "status": result.status,
            "total_hits": result.total_hits,
            "evidence_n": len(result.evidences),
            "task_diagnostics": dict(result.diagnostics or {}),
        }
        error_codes: List[str] = []
        if result.error_code:
            per_task_entry["error_code"] = result.error_code
            error_codes.append(result.error_code)

        diagnostics: Dict[str, Any] = dict(result.diagnostics or {})
        diagnostics.update(
            {
                "merge_strategy": plan.merge_strategy,
                "plan_reason": plan.plan_reason,
                "task_count": 1,
                "latency_ms": latency_ms,
                "retrieval_latency_ms": latency_ms,  # 하위 호환: 기존 dashboard용
                "per_task": [per_task_entry],
                "merged_total": len(result.evidences),
                "returned": len(result.evidences),
                "error_codes": error_codes,
            }
        )
        return result.model_copy(update={"diagnostics": diagnostics})

    # ------------------------------------------------------------------
    # Merge strategies
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_evidences(
        evidences: List[CanonicalEvidence],
        *,
        strategy: str,
    ) -> List[CanonicalEvidence]:
        if not evidences:
            return []

        if strategy == "by_score_dedup":
            evidences = sorted(evidences, key=lambda ev: -float(ev.score or 0.0))
            return _dedup_by_identity(evidences)

        if strategy == "by_axis_groupby":
            # 각 그룹의 순서를 보존하면서 dedup
            return _dedup_by_identity(evidences)

        # "single" 또는 unknown
        return _dedup_by_identity(evidences)


# ============================================================================
# Helpers
# ============================================================================

def _dedup_by_identity(evidences: List[CanonicalEvidence]) -> List[CanonicalEvidence]:
    """identity 기준 중복 제거. 등장 순서 유지."""
    seen: set[str] = set()
    out: List[CanonicalEvidence] = []
    for ev in evidences:
        if ev.identity in seen:
            continue
        seen.add(ev.identity)
        out.append(ev)
    return out
