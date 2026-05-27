"""Phase 7: EvidenceCuratorAgent — SearchResult + DialogueIntent → EvidenceBundle.

설계 원칙:
    - LLM 없는 결정적 정리기.
    - display_rank를 최종 고정한다. **이 값이 ReferenceManifest.published_rank의 진실원이며**,
      AnswerAgent가 만든 답변의 [N] 인용이 가리키는 번호이다.
    - 같은 사업(pjt_no)의 다년차 또는 같은 과제의 성과들을 그룹으로 묶어 EvidenceBundle.groups에
      넣는다. items는 평탄한 리스트로 유지 (AnswerAgent가 그룹 정보를 prompt 구조화에 활용).
    - view 종류로 AnswerAgent의 프롬프트 템플릿이 분기된다.

view 결정 규칙:
    - intent.kind == "ask_detail" 또는 단일 item → single_detail
    - intent.kind == "compare" → comparison_table
    - intent.kind == "stats" → stats_summary
    - 명시적 subject가 있음 → subject_activity
    - 그 외 → list_compact
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from loguru import logger

from apps.pipeline.agents.contracts import (
    DialogueIntent,
    EntityResolution,
    EvidenceBundle,
    EvidenceGroup,
    EvidenceView,
)
from apps.pipeline.contracts import CanonicalEvidence, SearchResult


# ============================================================================
# EvidenceCuratorAgent
# ============================================================================

class EvidenceCuratorAgent:
    """SearchResult + DialogueIntent → EvidenceBundle.

    호출 흐름:
        1. SearchResult.status가 'empty' 또는 'error' → view='empty'
        2. view 결정
        3. items에 display_rank 1..N 재부여 (RetrievalAgent 결과 그대로 사용)
        4. view에 따라 groups 채움
        5. EvidenceBundle 반환
    """

    def curate(
        self,
        *,
        result: SearchResult,
        intent: DialogueIntent,
        resolution: Optional[EntityResolution] = None,
    ) -> EvidenceBundle:
        t0 = time.perf_counter()
        # 빈/오류 결과 → view='empty'
        if result.status in ("empty", "error") or not result.evidences:
            return EvidenceBundle(
                view="empty",
                items=[],
                groups=[],
                total_unique=0,
                total_before_curation=int(result.total_hits or 0),
                diagnostics={
                    "result_status": result.status,
                    "result_error": result.error_code,
                    "curation_latency_ms": (time.perf_counter() - t0) * 1000,
                },
            )

        view = _decide_view(intent=intent, evidences=result.evidences, resolution=resolution)
        items = _finalize_display_rank(result.evidences)
        groups = _build_groups(view=view, items=items, intent=intent)
        # single_detail view에서 evidence.child_entities 통계 (P1-3 회귀 관측용)
        child_entity_count = 0
        if view == "single_detail" and items:
            child_entity_count = len(items[0].child_entities or [])
        # subject_activity view에서 활동 요약 통계 산출 (AnswerAgent가 prompt에 노출)
        activity_summary: Optional[Dict[str, Any]] = None
        if view == "subject_activity" and items:
            activity_summary = _compute_activity_summary(
                items,
                subject_name=(intent.subject_name or "").strip() or None,
            )
        diagnostics: Dict[str, Any] = {
            "view": view,
            "intent_kind": intent.kind,
            "items_in": len(result.evidences),
            "items_out": len(items),
            "group_count": len(groups),
            "child_entity_count": child_entity_count,
            "result_status": result.status,
            "curation_latency_ms": (time.perf_counter() - t0) * 1000,
        }
        if activity_summary is not None:
            diagnostics["activity_summary"] = activity_summary
        # retrieval score 분포를 EvidenceBundle로 전파 (AnswerAgent가 prompt에 노출).
        # 휴리스틱 threshold 없이 LLM이 신뢰도 판단에 활용.
        result_diag = result.diagnostics or {}
        score_dist = result_diag.get("score_distribution")
        if score_dist is not None:
            diagnostics["score_distribution"] = score_dist

        bundle = EvidenceBundle(
            view=view,
            items=items,
            groups=groups,
            total_unique=len(items),
            total_before_curation=int(result.total_hits or len(items)),
            diagnostics=diagnostics,
        )
        logger.info(
            f"[EvidenceCurator] bundle_ready(증거 묶음 준비 완료) "
            f"view={view}(노출 뷰) items={len(items)}(증거 건수) "
            f"groups={len(groups)}(그룹 수) intent_kind={intent.kind}(의도 종류)"
        )
        return bundle


# ============================================================================
# View decision
# ============================================================================

def _decide_view(
    *,
    intent: DialogueIntent,
    evidences: List[CanonicalEvidence],
    resolution: Optional[EntityResolution],
) -> EvidenceView:
    """view 종류 결정.

    우선순위:
        1. intent.kind == 'ask_detail' or 단일 item → single_detail
        2. intent.kind == 'compare' → comparison_table
        3. intent.kind == 'stats' → stats_summary
        4. resolution.subject 또는 intent.subject_name 있음 → subject_activity
        5. 그 외 → list_compact
    """
    n = len(evidences)
    if intent.kind == "ask_detail" or intent.action_hint == "detail":
        return "single_detail" if n >= 1 else "empty"
    if intent.kind == "compare":
        return "comparison_table"
    if intent.kind == "stats":
        return "stats_summary"
    has_subject = bool(intent.subject_name) or (resolution is not None and resolution.subject is not None)
    if has_subject:
        return "subject_activity"
    return "list_compact"


# ============================================================================
# display_rank 고정
# ============================================================================

def _finalize_display_rank(evidences: List[CanonicalEvidence]) -> List[CanonicalEvidence]:
    """items에 display_rank를 1..N으로 재부여.

    snapshot_rank 필드를 그대로 display_rank로 활용한다 (CanonicalEvidence는 별도 display_rank
    필드가 없으므로 snapshot_rank가 진실원이 된다). 이 값이 답변 본문의 [N] 인용과
    ReferenceManifest.published_rank의 기준이다.
    """
    out: List[CanonicalEvidence] = []
    for i, ev in enumerate(evidences, start=1):
        if ev.snapshot_rank == i:
            out.append(ev)
        else:
            out.append(ev.model_copy(update={"snapshot_rank": i}))
    return out


# ============================================================================
# Activity summary (subject_activity view 통계 — AnswerAgent prompt에 노출)
# ============================================================================

def _compute_activity_summary(
    items: List[CanonicalEvidence],
    subject_name: Optional[str] = None,
) -> Dict[str, Any]:
    """subject_activity view의 evidence를 분석해 한눈 요약 통계 생성.

    Args:
        items: subject_activity view에 들어갈 evidence 목록.
        subject_name: anchor 인물명. 지정되면 evidence별 facts.participant_role_map에서
            매칭되는 항목의 role 분포를 subject_roles로 집계 (2026-05-26 추가).

    Returns:
        {
          "total": N,
          "by_source": {"project": int, "perf": int},
          "year_min": int|None, "year_max": int|None,
          "top_orgs": [{"name": str, "count": int}, ...] (상위 3),
          "subject_roles": [{"role": str, "count": int}, ...]   # subject_name 매칭 시
        }
    """
    by_source: Dict[str, int] = {}
    org_counts: Dict[str, int] = {}
    years: List[int] = []
    subject_role_counts: Dict[str, int] = {}
    subject_match_count = 0
    subject_norm = (subject_name or "").strip()
    for ev in items:
        src = (ev.source_type or "unknown").lower()
        by_source[src] = by_source.get(src, 0) + 1
        # lead_org_name (roles) — list of str
        lead_orgs = ev.roles.get("lead_org_name") if ev.roles else None
        if isinstance(lead_orgs, list):
            for org in lead_orgs:
                key = (str(org) or "").strip()
                if not key:
                    continue
                org_counts[key] = org_counts.get(key, 0) + 1
        # stan_yr / year / period
        year = _extract_year_from_facts(ev.facts or {})
        if year is not None:
            years.append(year)
        # subject 인물의 role 추적 (anchor matching)
        if subject_norm:
            role_map = (ev.facts or {}).get("participant_role_map") or []
            if isinstance(role_map, list):
                for entry in role_map:
                    if not isinstance(entry, dict):
                        continue
                    name = str(entry.get("name") or "").strip()
                    if name == subject_norm:
                        subject_match_count += 1
                        role = str(entry.get("role") or "").strip() or "(역할 미상)"
                        subject_role_counts[role] = subject_role_counts.get(role, 0) + 1

    top_orgs = sorted(org_counts.items(), key=lambda kv: -kv[1])[:3]
    summary: Dict[str, Any] = {
        "total": len(items),
        "by_source": by_source,
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "top_orgs": [{"name": k, "count": v} for k, v in top_orgs],
    }
    if subject_role_counts:
        summary["subject_roles"] = [
            {"role": r, "count": c}
            for r, c in sorted(subject_role_counts.items(), key=lambda kv: -kv[1])
        ]
        summary["subject_match_count"] = subject_match_count
    return summary


def _extract_year_from_facts(facts: Dict[str, Any]) -> Optional[int]:
    """evidence.facts에서 연도 추출 (stan_yr/year 우선)."""
    for source in (
        facts.get("stan_yr"),
        (facts.get("meta_basic") or {}).get("stan_yr") if isinstance(facts.get("meta_basic"), dict) else None,
        facts.get("year"),
    ):
        if source is None:
            continue
        text = str(source).strip()
        if not text:
            continue
        digits = "".join(ch for ch in text[:8] if ch.isdigit())
        if len(digits) < 4:
            continue
        try:
            year = int(digits[:4])
        except ValueError:
            continue
        if 1900 <= year <= 2100:
            return year
    return None


# ============================================================================
# Group building
# ============================================================================

def _build_groups(
    *,
    view: EvidenceView,
    items: List[CanonicalEvidence],
    intent: DialogueIntent,
) -> List[EvidenceGroup]:
    """view에 따라 그룹 구성.

    - subject_activity : pjt_no 기준 그룹 (같은 사업의 다년차/성과 묶음)
    - comparison_table : compare_targets 기준 그룹
    - 그 외 view       : 그룹 없음
    """
    if view == "subject_activity":
        return _group_by_pjt_no(items)
    if view == "comparison_table":
        return _group_by_compare_targets(items, intent)
    return []


def _group_by_pjt_no(items: List[CanonicalEvidence]) -> List[EvidenceGroup]:
    """같은 pjt_no를 가진 evidences를 그룹으로 묶는다.

    pjt_no가 없는 항목은 각자 단독 그룹 (group_key = identity).
    """
    buckets: Dict[str, List[int]] = defaultdict(list)
    labels: Dict[str, str] = {}
    for ev in items:
        pjt_no = (ev.ids.get("pjt_no") or "").strip()
        key = pjt_no or f"single::{ev.identity}"
        buckets[key].append(ev.snapshot_rank)
        # 그룹 라벨: pjt_no가 있으면 사업명(title 첫 항목), 없으면 단독 제목
        if key not in labels:
            labels[key] = ev.title or (pjt_no or ev.identity)

    groups: List[EvidenceGroup] = []
    # 정렬: 그룹 멤버의 가장 빠른 rank 순
    sorted_keys = sorted(buckets.keys(), key=lambda k: min(buckets[k]))
    for key in sorted_keys:
        ranks = sorted(buckets[key])
        if key.startswith("single::"):
            # 단독 항목은 group으로 묶지 않음 (의미 부담만 늘림). pjt_no가 있는 그룹만 의미 그룹으로 유지.
            continue
        if len(ranks) < 2:
            continue
        groups.append(
            EvidenceGroup(
                group_key=key,
                group_label=labels[key],
                role=None,
                member_ranks=ranks,
            )
        )
    return groups


def _group_by_compare_targets(items: List[CanonicalEvidence], intent: DialogueIntent) -> List[EvidenceGroup]:
    """compare 의도일 때 compare_targets별 그룹 (title substring 매칭으로 분류).

    Heuristic: 각 target name이 evidence title 또는 identity에 포함되는지 확인.
    매칭이 안 되면 'other' 그룹.
    """
    groups: List[EvidenceGroup] = []
    matched_ranks_per_target: Dict[str, List[int]] = defaultdict(list)
    other_ranks: List[int] = []

    for ev in items:
        title = (ev.title or "").strip()
        matched_target = None
        for target in intent.compare_targets:
            if target.name and target.name in title:
                matched_target = target.name
                break
        if matched_target:
            matched_ranks_per_target[matched_target].append(ev.snapshot_rank)
        else:
            other_ranks.append(ev.snapshot_rank)

    for target in intent.compare_targets:
        ranks = matched_ranks_per_target.get(target.name, [])
        if not ranks:
            continue
        groups.append(
            EvidenceGroup(
                group_key=f"compare::{target.name}",
                group_label=target.name,
                role=target.kind,
                member_ranks=sorted(ranks),
            )
        )
    if other_ranks:
        groups.append(
            EvidenceGroup(
                group_key="compare::other",
                group_label="기타",
                role=None,
                member_ranks=sorted(other_ranks),
            )
        )
    return groups
