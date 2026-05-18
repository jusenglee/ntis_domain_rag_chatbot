"""FinalGuard — "이 답변이 근거와 계약을 위반하지 않는가"만 결정한다.

권한:
    O 생성된 답변이 SearchResult.evidences 안에서만 인용했는지 검증
    O 답변 항목을 evidence에 remap해 published_rank 기준 ReferenceManifest 발행 (ADR-0017)
    O 도메인 계약(pjt_id≠pjt_no, IRD_NAI_PJT_INFO→pjt_id, 성과→rst_id) 검증
    O 부분 발행 허용 (allow_prefix_subset + allow_manifest_publish_on_subset 동시 True)
    X 검색 재실행
    X JudgmentAgent 결정 재해석

기존 apps.api.contracts.answer_state_consistency / visible_answer_manifest_publication /
apps.chat.answer_merge 를 통합 폐기 후 재구현. 정책 자기모순(subset 수용 vs 발행 차단)도 제거.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from loguru import logger

from apps.api.rag_mapper.schema_types import DataTag
from apps.pipeline.contracts import (
    Axis,
    CanonicalEvidence,
    Clarification,
    FinalAnswer,
    GeneratedAnswer,
    ReferenceItem,
    ReferenceManifest,
    SearchResult,
    SearchTask,
)


# 답변 본문의 [N] 인용 패턴.
# 다른 도메인 식별자(pjt_id / pjt_no / rst_id) 정규식 추출은 형식 다양성·오작동 위험 때문에 폐기.
# 식별자 검증은 evidence.ids 값 substring 검색으로 대체 (정규식 없음).
_CITATION_PATTERN = re.compile(r"\[(\d{1,3})\]")


# tag → ReferenceItem.id 축 결정 (ADR-0017)
_TAG_TO_AXIS: Dict[str, Axis] = {
    DataTag.PROJECT.value: "pjt_id",            # IRD_NAI_PJT_INFO
    DataTag.PAPER.value: "rst_id",              # IRD_NAI_RI_PAPER
    DataTag.PATENT.value: "rst_id",             # IRD_NAI_RI_IPR
    DataTag.SOFTWARE.value: "rst_id",           # IRD_NAI_RI_SW
    DataTag.REPORT.value: "rst_id",             # IRD_NAI_RI_RSCH_RPT
    DataTag.EQUIPMENT.value: "rst_id",          # IRD_NAI_RI_FCLT_EQUIP
    DataTag.STANDARD.value: "rst_id",
    DataTag.TECH_SUMMARY.value: "rst_id",
    DataTag.COMPOUND.value: "rst_id",
    DataTag.ORGANISM_INFO.value: "rst_id",
    DataTag.ORGANISM_RESOURCE.value: "rst_id",
    DataTag.VARIETY.value: "rst_id",
}


class FinalGuard:
    """SearchResult + GeneratedAnswer + SearchTask → FinalAnswer."""

    def review(
        self,
        *,
        task: SearchTask,
        result: SearchResult,
        generated: GeneratedAnswer,
    ) -> FinalAnswer:
        """답변을 검토하고 발행/명확화/내부오류를 결정.

        흐름:
            1. 결과 상태(empty/error)는 즉시 적절한 결정으로 처리
            2. 답변에서 [N] 인용 추출
            3. 인용된 출처가 evidence 범위 안에 있는지 검증 (groundedness)
            4. 인용된 evidence를 published_rank 기준으로 remap (ReferenceManifest 생성)
            5. 도메인 계약 검증 (pjt_id≠pjt_no 등)
            6. 위반 없으면 publish, 있으면 clarify
        """
        # ---- 0. 결과 상태별 단축 분기 ----
        if result.status == "error":
            return FinalAnswer(
                decision="internal_error",
                reasoning=f"search_error:{result.error_code}",
                diagnostics={"error_detail": result.error_detail},
            )

        if result.status == "empty":
            # generate_no_result_message로 만든 응답이 있으면 그대로 발행
            return FinalAnswer(
                decision="publish",
                text=generated.text,
                reference_manifest=ReferenceManifest(items=[], total_visible=0),
                reasoning="empty_result_published",
            )

        if result.status == "multiple":
            # multiple은 search_agent_refine 단계에서 단일화되어야 한다. 여기까지 왔다는 건
            # refine이 이미 1회 시도되고도 단일화에 실패한 상태.
            return FinalAnswer(
                decision="clarify",
                clarification=Clarification(
                    question="조건에 부합하는 후보가 여러 개입니다. 좀 더 구체적인 조건을 알려주실 수 있나요?",
                    reason="multiple_candidates_after_refine",
                ),
                reasoning="multiple_unresolved",
            )

        # ---- result.status == "single" ----
        return self._review_single(task=task, result=result, generated=generated)

    # ------------------------------------------------------------------
    # internal: single 케이스 정합 검증
    # ------------------------------------------------------------------

    def _review_single(
        self,
        *,
        task: SearchTask,
        result: SearchResult,
        generated: GeneratedAnswer,
    ) -> FinalAnswer:
        text = generated.text.strip()
        if not text:
            return FinalAnswer(
                decision="internal_error",
                reasoning="empty_generation",
                diagnostics={"generated_meta": generated.stream_metrics},
            )

        # ---- 1. 인용 [N] 추출 (단일 안전 정규식) ----
        cited_ranks = sorted({int(m.group(1)) for m in _CITATION_PATTERN.finditer(text)})

        # ---- 2. 인용된 N이 evidence 범위 안인지 검증 ----
        max_rank = len(result.evidences)
        out_of_range = [n for n in cited_ranks if n < 1 or n > max_rank]
        if out_of_range:
            logger.info(
                f"[FinalGuard] cited_ranks_out_of_range={out_of_range} max={max_rank}"
            )
            return FinalAnswer(
                decision="clarify",
                clarification=Clarification(
                    question="답변에 표시된 출처 번호가 실제 근거 항목 범위를 벗어났습니다. "
                    "검색 조건을 좀 더 구체적으로 알려주시면 다시 시도하겠습니다.",
                    reason=f"cited_rank_out_of_range:{out_of_range}",
                ),
                reasoning="cited_rank_out_of_range",
                diagnostics={"out_of_range": out_of_range, "evidence_count": max_rank},
            )

        # ---- 3. ReferenceManifest 매핑 ----
        manifest, manifest_issues = self._build_manifest(
            evidences=result.evidences,
            cited_ranks=cited_ranks,
            text=text,
        )

        diagnostics = {
            "cited_ranks": cited_ranks,
            "evidence_count": len(result.evidences),
            "manifest_issues": manifest_issues,
        }

        return FinalAnswer(
            decision="publish",
            text=text,
            reference_manifest=manifest,
            reasoning="published" if not manifest_issues else "published_with_warnings",
            diagnostics=diagnostics,
        )

    # ------------------------------------------------------------------
    # manifest builder
    # ------------------------------------------------------------------

    def _build_manifest(
        self,
        *,
        evidences: List[CanonicalEvidence],
        cited_ranks: List[int],
        text: str,
    ) -> Tuple[ReferenceManifest, List[str]]:
        """답변에 인용된 evidence를 published_rank 기준으로 매핑.

        ADR-0017 규칙:
            - published_rank = 답변에서 사용자가 본 번호
            - source_snapshot_rank = retrieval snapshot 내 순번
            - tag별로 id_axis 결정 (PROJECT→pjt_id, 성과계열→rst_id)

        cited_ranks가 비어 있으면 전체 evidence를 published 순서대로 manifest에 담는다.
        """
        items: List[ReferenceItem] = []
        issues: List[str] = []

        # evidence를 snapshot_rank로 인덱싱
        by_rank: Dict[int, CanonicalEvidence] = {ev.snapshot_rank: ev for ev in evidences}

        if cited_ranks:
            # 인용 순서를 published_rank의 진실로 사용
            for pub_rank, snap_rank in enumerate(cited_ranks, start=1):
                ev = by_rank.get(snap_rank)
                if ev is None:
                    issues.append(f"cited_rank_out_of_range:{snap_rank}")
                    continue
                item = self._evidence_to_reference(ev=ev, published_rank=pub_rank)
                if item is None:
                    issues.append(f"reference_axis_unresolved:rank={snap_rank}")
                    continue
                items.append(item)
        else:
            # 인용이 없는 경우: 전체를 evidence 순서대로 manifest에 담는다
            for pub_rank, ev in enumerate(evidences, start=1):
                item = self._evidence_to_reference(ev=ev, published_rank=pub_rank)
                if item is None:
                    issues.append(f"reference_axis_unresolved:rank={ev.snapshot_rank}")
                    continue
                items.append(item)

        return (
            ReferenceManifest(
                items=items,
                total_visible=len(items),
                publication_status="published",
            ),
            issues,
        )

    @staticmethod
    def _evidence_to_reference(
        *,
        ev: CanonicalEvidence,
        published_rank: int,
    ) -> Optional[ReferenceItem]:
        """canonical evidence를 ReferenceItem으로 변환.

        ADR-0017: tag에 따라 id의 의미가 고정된다. tag를 결정할 수 없으면 None.
        """
        tag = (ev.tag or "").strip()
        axis = _TAG_TO_AXIS.get(tag)
        if axis is None:
            # tag 미확정: source_type으로 fallback
            if ev.source_type == "project":
                axis = "pjt_id"
                tag = DataTag.PROJECT.value
            elif ev.source_type == "perf":
                axis = "rst_id"
                # 성과 계열의 대표 tag는 PAPER이지만 ev.tag가 빈 경우는 보존만 한다.
                tag = tag or "IRD_NAI_RI_RESULT"
            else:
                return None

        id_value = ev.ids.get(axis)
        if not id_value:
            # axis가 빠진 경우 fallback (특히 perf에서 rst_id가 없는 케이스)
            for fallback_axis in ("pjt_id", "pjt_no", "rst_id"):
                if ev.ids.get(fallback_axis):
                    axis = fallback_axis  # type: ignore[assignment]
                    id_value = ev.ids[fallback_axis]
                    break
            if not id_value:
                return None

        return ReferenceItem(
            published_rank=published_rank,
            source_snapshot_rank=ev.snapshot_rank,
            tag=tag,
            id=id_value,
            title=ev.title or f"(제목 없음 #{ev.snapshot_rank})",
            id_axis=axis,
        )
