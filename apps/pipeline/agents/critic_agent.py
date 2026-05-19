"""Phase 9: CriticAgent — AnswerDraft 검증 + ReferenceManifest 발행.

설계 원칙:
    - 결정적(deterministic) 검증기. LLM 호출 없음.
    - 입력: AnswerDraft + EvidenceBundle + DialogueIntent (+ repair_attempted 플래그)
    - 출력: GuardDecision (publish | repair_answer | clarify | internal_error)
    - 본 모듈이 ReferenceManifest의 최종 발행자. 답변 본문 [N] ↔ manifest.published_rank ↔
      EvidenceBundle.items[i].snapshot_rank가 모두 일치해야 한다 (ADR-0017).

검증 흐름:
    0. bundle.view == "empty" 또는 draft.template == "no_result" → publish (빈 manifest)
    1. text 비어 있음 → internal_error("empty_generation")
    2. citation 0개이면서 본문이 인용 의무를 갖는 template → repair_answer (1회 한도)
                                                          → repair 소진 후엔 clarify로 전환
    3. cited rank가 evidence 범위를 벗어남 → repair_answer (1회 한도) → 소진 후 clarify
    4. (정상) ReferenceManifest 발행 → publish

repair 1회 제한:
    - 외부 state(workflow)가 repair_attempted=True를 두 번째 invocation에 전달.
    - CriticAgent 자체는 stateless. 결정만 내림.

복구할 수 없는 위반(out_of_range, missing_citation)은 사용자에게 clarify로 안내.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from loguru import logger

from apps.api.rag_mapper.schema_types import DataTag
from apps.pipeline.agents.contracts import (
    AnswerDraft,
    DialogueIntent,
    EvidenceBundle,
    GuardDecision,
)
from apps.pipeline.contracts import (
    Axis,
    CanonicalEvidence,
    ReferenceItem,
    ReferenceManifest,
)


# 답변 본문의 [N] 인용 패턴.
_CITATION_PATTERN = re.compile(r"\[(\d{1,3})\]")


# tag → ReferenceItem.id 축 결정 (ADR-0017).
_TAG_TO_AXIS: Dict[str, Axis] = {
    DataTag.PROJECT.value: "pjt_id",
    DataTag.PAPER.value: "rst_id",
    DataTag.PATENT.value: "rst_id",
    DataTag.SOFTWARE.value: "rst_id",
    DataTag.REPORT.value: "rst_id",
    DataTag.EQUIPMENT.value: "rst_id",
    DataTag.STANDARD.value: "rst_id",
    DataTag.TECH_SUMMARY.value: "rst_id",
    DataTag.COMPOUND.value: "rst_id",
    DataTag.ORGANISM_INFO.value: "rst_id",
    DataTag.ORGANISM_RESOURCE.value: "rst_id",
    DataTag.VARIETY.value: "rst_id",
}


# 인용 의무가 있는 template (citation 0개면 repair).
_TEMPLATES_REQUIRING_CITATION = {"list", "detail", "compare", "stats"}


# ============================================================================
# CriticAgent
# ============================================================================

class CriticAgent:
    """AnswerDraft 검증 + ReferenceManifest 발행."""

    def critique(
        self,
        *,
        draft: AnswerDraft,
        bundle: EvidenceBundle,
        intent: DialogueIntent,
        repair_attempted: bool = False,
    ) -> GuardDecision:
        # ---- 0. empty/no_result → publish ----
        if bundle.view == "empty" or draft.template == "no_result":
            return GuardDecision(
                decision="publish",
                text=draft.text,
                reference_manifest=ReferenceManifest(
                    items=[], total_visible=0, publication_status="published"
                ),
                reasoning="empty_result_published",
                diagnostics={"view": bundle.view, "template": draft.template},
            )

        text = (draft.text or "").strip()

        # ---- 1. 빈 본문 ----
        if not text:
            return GuardDecision(
                decision="internal_error",
                error_code="empty_generation",
                error_reason="answer text is empty after streaming",
                reasoning="empty_generation",
                diagnostics={
                    "truncated": draft.truncated,
                    "stream_metrics": dict(draft.stream_metrics or {}),
                },
            )

        # ---- 2. citation 정합 ----
        cited_ranks = _extract_cited_ranks(text)
        max_rank = len(bundle.items)
        out_of_range = [n for n in cited_ranks if n < 1 or n > max_rank]

        if out_of_range:
            logger.info(
                f"[CriticAgent] out_of_range={out_of_range} max={max_rank} "
                f"template={draft.template} repair_attempted={repair_attempted}"
            )
            if not repair_attempted:
                return GuardDecision(
                    decision="repair_answer",
                    repair_hint=(
                        f"답변에서 인용한 [N] 중 일부가 근거 범위를 벗어났습니다 "
                        f"(범위: 1..{max_rank}, 위반: {out_of_range}). "
                        f"위 [근거 출처] 블록의 순번만 사용해 답변을 다시 작성하세요."
                    ),
                    reasoning="cited_rank_out_of_range",
                    diagnostics={"out_of_range": out_of_range, "max_rank": max_rank},
                )
            # repair 소진 → 사용자 명확화
            return GuardDecision(
                decision="clarify",
                clarification_question=(
                    "답변에 표시된 출처 번호가 근거 범위를 벗어났습니다. "
                    "검색 조건을 조금 더 구체적으로 알려주실 수 있나요?"
                ),
                reasoning="cited_rank_out_of_range_post_repair",
                diagnostics={"out_of_range": out_of_range, "max_rank": max_rank},
            )

        if draft.template in _TEMPLATES_REQUIRING_CITATION and not cited_ranks:
            logger.info(
                f"[CriticAgent] missing_citations template={draft.template} "
                f"repair_attempted={repair_attempted}"
            )
            if not repair_attempted:
                return GuardDecision(
                    decision="repair_answer",
                    repair_hint=(
                        "답변에 출처 번호 [N]이 표기되어 있지 않습니다. "
                        "[근거 출처] 블록의 순번을 각 항목 끝에 반드시 [N] 형태로 표기하세요."
                    ),
                    reasoning="missing_citations",
                    diagnostics={"template": draft.template, "max_rank": max_rank},
                )
            # repair 소진 → 사용자 명확화
            return GuardDecision(
                decision="clarify",
                clarification_question=(
                    "답변에 인용 출처가 빠져 있어 신뢰성을 보장할 수 없습니다. "
                    "질문을 조금 더 구체적으로 알려주실 수 있나요?"
                ),
                reasoning="missing_citations_post_repair",
                diagnostics={"template": draft.template},
            )

        # ---- 3. ReferenceManifest 발행 ----
        manifest, issues = _build_manifest(bundle.items)
        reasoning = "published" if not issues else "published_with_warnings"

        return GuardDecision(
            decision="publish",
            text=text,
            reference_manifest=manifest,
            reasoning=reasoning,
            diagnostics={
                "cited_ranks": cited_ranks,
                "evidence_count": max_rank,
                "manifest_issues": issues,
                "template": draft.template,
                "view": bundle.view,
                "truncated": draft.truncated,
            },
        )


# ============================================================================
# Helpers
# ============================================================================

def _extract_cited_ranks(text: str) -> List[int]:
    """본문에서 [N] 패턴을 모아 정렬·중복제거된 rank 리스트로 반환."""
    return sorted({int(m.group(1)) for m in _CITATION_PATTERN.finditer(text)})


def _build_manifest(
    evidences: List[CanonicalEvidence],
) -> Tuple[ReferenceManifest, List[str]]:
    """EvidenceBundle.items 전체를 published_rank=snapshot_rank로 manifest 발행.

    ADR-0017: cited_ranks만 골라내지 않고 모든 evidence를 발행한다. 그래야 사용자가 본
    [N]번 모두에 대해 후속 turn에서 정확한 ID 매핑이 유지된다 (manifest_rank → identifier
    해소가 깨지지 않음).
    """
    items: List[ReferenceItem] = []
    issues: List[str] = []
    for ev in evidences:
        item = _evidence_to_reference_item(ev=ev, published_rank=ev.snapshot_rank)
        if item is None:
            issues.append(f"reference_axis_unresolved:rank={ev.snapshot_rank}")
            continue
        items.append(item)
    items.sort(key=lambda it: it.published_rank)
    return (
        ReferenceManifest(
            items=items,
            total_visible=len(items),
            publication_status="published",
        ),
        issues,
    )


def _evidence_to_reference_item(
    *,
    ev: CanonicalEvidence,
    published_rank: int,
) -> Optional[ReferenceItem]:
    """canonical evidence를 ReferenceItem으로 변환.

    ADR-0017: tag에 따라 id의 의미가 고정된다.
        - IRD_NAI_PJT_INFO → pjt_id
        - IRD_NAI_RI_*     → rst_id
    """
    tag = (ev.tag or "").strip()
    axis = _TAG_TO_AXIS.get(tag)
    if axis is None:
        if ev.source_type == "project":
            axis = "pjt_id"
            tag = DataTag.PROJECT.value
        elif ev.source_type == "perf":
            axis = "rst_id"
            tag = tag or "IRD_NAI_RI_RESULT"
        else:
            return None

    id_value = ev.ids.get(axis)
    if not id_value:
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
