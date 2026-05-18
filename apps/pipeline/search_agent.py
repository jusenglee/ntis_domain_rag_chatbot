"""SearchAgent — SearchTask만 입력으로 받아 SearchResult를 만든다.

권한:
    O 주어진 SearchTask의 식별자/필터/anchor를 그대로 검색 엔진에 전달
    O 결과를 canonical evidence로 정규화
    O 상태(single/multiple/empty/error)를 판정
    X 사용자 의도를 재해석하지 않음
    X query 텍스트 drift 감지나 raw_query fallback 같은 휴리스틱 없음
    X JudgmentAgent 결정을 우회/수정하지 않음

기존 apps.retrieval.retrieval_workflow.node_rag_search / rag_retriever.resolve_rag_queries
및 RAG_LOOKUP_FILTER_POLICY silent fallback 등 모든 로직을 폐기 후 재구현.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

from loguru import logger

from apps.pipeline.contracts import (
    AXIS_PRIORITY,
    CanonicalEvidence,
    Collection,
    SearchResult,
    SearchResultStatus,
    SearchTask,
)
from apps.pipeline.retrieval import (
    hybrid_search,
    lookup_by_axis,
    normalize_qdrant_points,
)


class SearchAgent:
    """SearchTask 단일 입력 → SearchResult 단일 출력.

    의존성:
        qdrant_client : Qdrant 검색용
        embed_e5i / embed_e5 : 임베딩 모델 (E5 instruct + E5 base, 컬렉션별로 분리)
    """

    def __init__(self, *, qdrant_client: Any, embed_e5i: Any, embed_e5: Any) -> None:
        self._qdrant_client = qdrant_client
        self._embed_e5i = embed_e5i
        self._embed_e5 = embed_e5

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    async def execute(self, task: SearchTask) -> SearchResult:
        """SearchTask를 실행하고 SearchResult를 돌려준다.

        본 메서드는 절대로 예외를 사용자에게 흘리지 않는다 — 모든 실패는 status=error로 감싼다.
        """
        t0 = time.perf_counter()
        try:
            evidences, total_hits = await self._dispatch(task)
        except Exception as exc:  # noqa: BLE001 — 모든 에러는 status=error로 흡수
            logger.exception(f"[SearchAgent] dispatch failed: task={task.strategy} err={exc}")
            return SearchResult(
                status="error",
                error_code="search_dispatch_failed",
                error_detail=str(exc),
                diagnostics={"strategy": task.strategy, "latency_ms": (time.perf_counter() - t0) * 1000},
            )

        latency_ms = (time.perf_counter() - t0) * 1000
        visible = evidences[: task.display_limit]
        status = self._judge_status(task=task, hits=total_hits)
        diagnostics = {
            "strategy": task.strategy,
            "latency_ms": latency_ms,
            "collections": list(task.collections),
        }

        # status별 evidence 노출 정책:
        #   - empty   : []
        #   - multiple: visible (refine 단계가 판단에 활용)
        #   - single  + action=detail : 단일 후보 1건
        #   - single  + 기타(list/stats/topic) : display_limit까지 (사용자에게 보여줄 N건)
        if status == "empty":
            return SearchResult(status="empty", evidences=[], total_hits=0, diagnostics=diagnostics)
        if status == "multiple":
            return SearchResult(
                status="multiple",
                evidences=visible,
                total_hits=total_hits,
                diagnostics=diagnostics,
            )
        if status == "single":
            if task.action == "detail":
                evidences_out = visible[:1]
            else:
                evidences_out = visible
            return SearchResult(
                status="single",
                evidences=evidences_out,
                total_hits=total_hits,
                diagnostics=diagnostics,
            )
        # 이론상 도달 불가
        return SearchResult(
            status="error",
            error_code="unknown_status",
            error_detail=f"status={status}",
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _dispatch(self, task: SearchTask) -> tuple[List[CanonicalEvidence], int]:
        """strategy에 따른 분기 디스패치.

        Returns:
            (evidences[: task.limit], total_hits_before_limit)
            total_hits는 dedup 후·limit 자르기 전 카운트. status 판정에 쓰임.
        """
        if task.strategy == "exact_lookup":
            return self._exec_exact_lookup(task)
        if task.strategy == "subject_anchor":
            return self._exec_hybrid_with_anchor(task)
        if task.strategy == "detail_anchor":
            return self._exec_detail_anchor(task)
        if task.strategy == "hybrid_search":
            return self._exec_hybrid_search(task)
        raise ValueError(f"unknown strategy: {task.strategy}")

    # ----- exact_lookup -----
    def _exec_exact_lookup(self, task: SearchTask) -> tuple[List[CanonicalEvidence], int]:
        """식별자 by-id 조회. 여러 axis가 있으면 우선순위 높은 것부터 시도."""
        ids = task.identifiers
        candidates = [
            (axis, getattr(ids, axis))
            for axis, _ in sorted(AXIS_PRIORITY.items(), key=lambda kv: -kv[1])
            if getattr(ids, axis)
        ]

        all_evidences: List[CanonicalEvidence] = []
        for axis, values in candidates:
            for collection in task.collections:
                points = lookup_by_axis(
                    qdrant_client=self._qdrant_client,
                    collection=collection,
                    axis=axis,
                    values=values,
                    limit=max(task.limit * 3, 10),
                )
                logger.info(
                    f"[exact_lookup] axis={axis} values={values[:5]} "
                    f"collection={collection} raw_points={len(points or [])}"
                )
                if not points:
                    continue
                all_evidences.extend(
                    normalize_qdrant_points(
                        points,
                        base_route=self._collection_to_route(collection),
                        action=task.action,
                    )
                )
            if all_evidences:
                logger.info(
                    f"[exact_lookup] axis={axis} resolved evidences={len(all_evidences)}; "
                    "stopping axis fallback"
                )
                break

        deduped = self._dedup_by_identity(all_evidences)
        logger.info(
            f"[exact_lookup] merged={len(all_evidences)} deduped={len(deduped)} "
            f"final_returned={min(len(deduped), task.limit)}"
        )
        return deduped[: task.limit], len(deduped)

    # ----- subject_anchor & hybrid -----
    def _exec_hybrid_with_anchor(self, task: SearchTask) -> tuple[List[CanonicalEvidence], int]:
        """subject anchor를 nested filter로 강제 주입 후 hybrid."""
        return self._run_hybrid(task)

    def _exec_hybrid_search(self, task: SearchTask) -> tuple[List[CanonicalEvidence], int]:
        """일반 hybrid (anchor/identifier 없음)."""
        return self._run_hybrid(task)

    def _exec_detail_anchor(self, task: SearchTask) -> tuple[List[CanonicalEvidence], int]:
        """단일 대상 상세를 위한 hybrid. limit=1로 강제되어 있음."""
        return self._run_hybrid(task)

    def _run_hybrid(self, task: SearchTask) -> tuple[List[CanonicalEvidence], int]:
        merged: List[CanonicalEvidence] = []
        per_collection: List[str] = []
        for collection in task.collections:
            embed_model = self._pick_embed_model(collection)
            t0 = time.perf_counter()
            points = hybrid_search(
                qdrant_client=self._qdrant_client,
                embed_model=embed_model,
                collection=collection,
                query=task.retrieval_query,
                filters=task.filters,
                subject=task.subject,
                identifiers=task.identifiers,
                limit=max(task.limit * 2, 10),
            )
            dt_ms = (time.perf_counter() - t0) * 1000
            evidences = normalize_qdrant_points(
                points,
                base_route=self._collection_to_route(collection),
                action=task.action,
            )
            merged.extend(evidences)
            per_collection.append(
                f"{collection}: raw={len(points or [])} normalized={len(evidences)} dt={dt_ms:.0f}ms"
            )

        merged.sort(key=lambda ev: -ev.score)
        deduped = self._dedup_by_identity(merged)
        logger.info(
            f"[hybrid] strategy={task.strategy} | "
            + " | ".join(per_collection)
            + f" | merged={len(merged)} deduped={len(deduped)} returned={min(len(deduped), task.limit)}"
        )
        return deduped[: task.limit], len(deduped)

    # ----- helpers -----

    @staticmethod
    def _collection_to_route(collection: str) -> str:
        if collection == "ntis_project_v1":
            return "project"
        if collection == "ntis_perf_v1":
            return "perf"
        if collection == "ntis_supports":
            return "support"
        return "project"

    def _pick_embed_model(self, collection: str) -> Any:
        """컬렉션별 임베딩 모델 매핑. 본 시스템은 project=e5i, perf=e5 관례.

        ntis_supports는 e5_qa 단일 vector라 e5_base와 호환되는 e5를 우선 사용.
        """
        if collection == "ntis_perf_v1":
            return self._embed_e5
        if collection == "ntis_supports":
            return self._embed_e5
        return self._embed_e5i

    @staticmethod
    def _dedup_by_identity(evidences: List[CanonicalEvidence]) -> List[CanonicalEvidence]:
        """identity 기준 중복 제거 (우선 등장한 항목 유지)."""
        seen: set[str] = set()
        out: List[CanonicalEvidence] = []
        for ev in evidences:
            if ev.identity in seen:
                continue
            seen.add(ev.identity)
            # snapshot_rank를 결과 내 순서로 재부여 (이후 published_rank 매핑의 기준)
            out.append(
                ev.model_copy(update={"snapshot_rank": len(out) + 1})
            )
        return out

    @staticmethod
    def _judge_status(*, task: SearchTask, hits: int) -> SearchResultStatus:
        """검색 hit 수와 task action 종류에 따라 상태 결정.

        - detail 액션: 1건만 success, 0건 empty, 2건 이상 multiple
        - list/stats/topic 액션: 1건 이상이면 single (대표 결과 사용) 또는 multiple
          → 본 시스템은 "single = 답변 가능", "multiple = 명확화 필요"의 의미로 해석
          → list 액션은 hits>=1이면 single로 처리해 정상 답변 흐름으로 보낸다.
        """
        if task.action == "detail":
            if hits == 0:
                return "empty"
            if hits == 1:
                return "single"
            return "multiple"
        # list/stats/topic
        if hits == 0:
            return "empty"
        return "single"
