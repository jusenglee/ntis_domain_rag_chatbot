"""제약형 Context Router — anchor 후보 선택 + rewrite hint만 반환.

Router는 전략을 결정하지 않는다.
- mode, relation, target_cols, join_key_mode 출력 금지
- pjt_id / pjt_no / rst_id 생성 금지 (로컬 매핑으로만 복원)
- deterministic matcher 우선, LLM fallback은 clarification 직전 한정
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from apps.conversation.followup_anchor import (
    _RELATIVE_LAST_PATTERNS as _LAST_PATTERNS,
    _RELATIVE_FIRST_PATTERNS as _FIRST_PATTERNS,
    _RELATIVE_ORDINAL_PATTERNS as _ORDINAL_PATTERNS,
    _is_temporal_choegeun,
)
from apps.conversation.view_state import RecentMentionRecord

logger = logging.getLogger("Chatbot_Server")


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class ContextRouterDecision(BaseModel):
    status: Literal["resolved", "ambiguous", "unresolved"] = "unresolved"
    source: Literal["deterministic_recent_mentions", "llm_recent_mentions", "none"] = "none"
    selected_candidate_index: Optional[int] = None
    rewritten_query_hint: Optional[str] = None
    confidence: float = 0.0
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Deterministic matchers (공유 패턴은 followup_anchor에서 import)
# ---------------------------------------------------------------------------

_DEICTIC_PROJECT = (
    re.compile(r"그\s*과제"),
    re.compile(r"이\s*과제"),
    re.compile(r"해당\s*과제"),
)
_DEICTIC_PERF = (
    re.compile(r"그\s*(?:성과|논문|특허|보고서)"),
    re.compile(r"이\s*(?:성과|논문|특허|보고서)"),
    re.compile(r"해당\s*(?:성과|논문|특허|보고서)"),
)
_YEAR_PATTERN = re.compile(r"(\d{4})\s*년\s*(?:꺼|것|거|도)?")

_KIND_KEYWORDS = {
    "project": ("과제", "프로젝트"),
    "perf": ("성과", "논문", "특허", "보고서", "기술"),
    "people": ("연구자", "연구원", "사람"),
    "org": ("기관", "회사", "조직"),
}


def _detect_kind(question: str) -> Optional[str]:
    text = str(question or "").strip()
    for kind, keywords in _KIND_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return kind
    return None


def _filter_by_kind(candidates: List[RecentMentionRecord], kind: Optional[str]) -> List[RecentMentionRecord]:
    if not kind:
        return candidates
    return [c for c in candidates if str(c.entity_kind or "").strip().lower() == kind]


def _filter_by_year(candidates: List[RecentMentionRecord], year: str) -> List[RecentMentionRecord]:
    return [c for c in candidates if c.year == year]


def _deterministic_resolve(
    question: str,
    candidates: List[RecentMentionRecord],
) -> ContextRouterDecision:
    """규칙 기반으로 recent_mention 후보에서 하나를 선택한다."""
    if not candidates:
        return ContextRouterDecision(status="unresolved", source="none", reason="no_candidates")

    text = str(question or "").strip()
    kind = _detect_kind(text)
    filtered = _filter_by_kind(candidates, kind) if kind else candidates
    if not filtered:
        filtered = candidates

    # "마지막/최근/방금" → 마지막 항목 (최근 temporal guard 적용)
    temporal_guard = _is_temporal_choegeun(text)
    for pattern in _LAST_PATTERNS:
        if pattern.search(text):
            if temporal_guard and "최근" in pattern.pattern:
                continue
            idx = len(filtered) - 1
            return ContextRouterDecision(
                status="resolved",
                source="deterministic_recent_mentions",
                selected_candidate_index=candidates.index(filtered[idx]),
                confidence=0.85,
                reason="last_reference",
            )

    # "첫 번째" → 첫 항목
    for pattern in _FIRST_PATTERNS:
        if pattern.search(text):
            return ContextRouterDecision(
                status="resolved",
                source="deterministic_recent_mentions",
                selected_candidate_index=candidates.index(filtered[0]),
                confidence=0.85,
                reason="first_reference",
            )

    # "N번째"
    for pattern in _ORDINAL_PATTERNS:
        match = pattern.search(text)
        if match:
            n = int(match.group(1))
            if 1 <= n <= len(filtered):
                return ContextRouterDecision(
                    status="resolved",
                    source="deterministic_recent_mentions",
                    selected_candidate_index=candidates.index(filtered[n - 1]),
                    confidence=0.85,
                    reason="ordinal_reference",
                )

    # "2025년꺼" — 연도 축 보조 해석
    year_match = _YEAR_PATTERN.search(text)
    if year_match:
        year_str = year_match.group(1)
        year_filtered = _filter_by_year(filtered, year_str)
        if len(year_filtered) == 1:
            return ContextRouterDecision(
                status="resolved",
                source="deterministic_recent_mentions",
                selected_candidate_index=candidates.index(year_filtered[0]),
                confidence=0.8,
                reason="year_axis_match",
                rewritten_query_hint=f"연도={year_str}",
            )
        if len(year_filtered) > 1:
            return ContextRouterDecision(
                status="ambiguous",
                source="deterministic_recent_mentions",
                confidence=0.4,
                reason="year_axis_multiple",
            )

    # 직시적 참조: "그 과제", "그 성과" — 후보 1개면 확정
    deictic_patterns = _DEICTIC_PROJECT if kind == "project" else _DEICTIC_PERF if kind == "perf" else []
    for pattern in deictic_patterns:
        if pattern.search(text):
            if len(filtered) == 1:
                return ContextRouterDecision(
                    status="resolved",
                    source="deterministic_recent_mentions",
                    selected_candidate_index=candidates.index(filtered[0]),
                    confidence=0.75,
                    reason="deictic_single_candidate",
                )
            return ContextRouterDecision(
                status="ambiguous",
                source="deterministic_recent_mentions",
                confidence=0.3,
                reason="deictic_multiple_candidates",
            )

    # 후보 1개 + 일반 참조
    if len(filtered) == 1:
        return ContextRouterDecision(
            status="resolved",
            source="deterministic_recent_mentions",
            selected_candidate_index=candidates.index(filtered[0]),
            confidence=0.6,
            reason="single_candidate_fallback",
        )

    return ContextRouterDecision(
        status="ambiguous" if len(filtered) > 1 else "unresolved",
        source="deterministic_recent_mentions",
        confidence=0.2,
        reason="no_deterministic_match",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def route_context(
    *,
    question: str,
    recent_mentions: List[RecentMentionRecord],
    active_scope_summary: Optional[Dict[str, Any]] = None,
) -> ContextRouterDecision:
    """제약형 Context Router. deterministic 해석 우선, LLM fallback은 향후 확장.

    Returns:
        ContextRouterDecision — 선택된 후보 index + rewrite hint.
        mode/relation/target_cols/join_key_mode는 절대 포함하지 않는다.
    """
    if not recent_mentions:
        return ContextRouterDecision(status="unresolved", source="none", reason="no_recent_mentions")

    decision = _deterministic_resolve(question, recent_mentions)
    if decision.status == "resolved":
        return decision

    # LLM fallback은 1차 구현에서 placeholder로 둔다.
    # 조건: explicit id 없고, scope_resolver 결과가 clarification이고,
    #        recent_mention 후보가 1개 이상 있고, deterministic으로 못 고른 경우.
    # 향후 LLM fallback 구현 시 아래 조건에서만 호출:
    # - 후보 리스트에는 title/year/lead_org/entity_kind/index만 넣음
    # - 출력은 index만
    # - mode, relation, target_cols, join_key_mode 출력 금지
    # - pjt_id, pjt_no 등 내부 키 생성 금지

    return decision
