"""Raw-payload 기반 팩트 short-circuit resolver (Evidence 계층).

ADR-0013 후속 정리에 따라 기존 `apps.conversation.fact_followup_resolver`의
팩트 답변 조립 로직을 Evidence 계층으로 이전한다.

책임 분리 원칙:
- `apps/conversation/raw_payload_store.py` — 메모리 직렬화/보관/프루닝 (state ownership)
- `apps/conversation/view_state.py` — 대화 상태 (focus/anchor)
- `apps/evidence/derived_facts_builder.py` — payload → facts 정규화
- `apps/evidence/memory_facts_resolver.py` — facts + 질문 패턴 → answer artifact (**본 모듈**)

retrieval_workflow는 이제 Evidence 단일 호출자를 가진다:
    from apps.evidence.memory_facts_resolver import (
        resolve_followup_from_facts,
        diagnose_followup_fact_miss,
    )

불변 조건 유지:
- planner strategy 불변
- raw payload는 prompt에 직접 주입되지 않는다 (본 모듈은 `answer_artifact`만 합성)
- pjt_id / pjt_no 분리 유지
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from apps.api.streaming.contracts import AnswerArtifact
from apps.conversation.raw_payload_store import (
    build_anchor_key_from_view_entity,
    decompress_raw_payload,
    get_anchor_record,
)
from apps.conversation.view_state import get_active_subject_entity
from apps.evidence.derived_facts_builder import build_eager_facts, build_lazy_facts, needs_lazy_facts


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _has_any(text: str, *tokens: str) -> bool:
    return any(token in text for token in tokens)


def _contains_all(text: str, groups: list[list[str]]) -> bool:
    return all(any(token in text for token in group) for group in groups)


def _first_nonempty_text(*values: Any) -> str:
    for value in values:
        text = _normalize_text(value)
        if text:
            return text
    return ""


def _answer_from_facts(question: str, eager_facts: Dict[str, Any], lazy_facts: Dict[str, Any]) -> Optional[str]:
    text = _normalize_text(question).lower()
    participant_count = int(eager_facts.get("participant_count") or 0)
    participant_org_count = int(eager_facts.get("participant_org_count") or 0)
    lead_names = [str(item).strip() for item in list(eager_facts.get("lead_researcher_names") or []) if str(item).strip()]
    people_preview = [dict(item) for item in list(eager_facts.get("people_preview") or []) if isinstance(item, dict)]
    org_preview = [dict(item) for item in list(eager_facts.get("org_preview") or []) if isinstance(item, dict)]
    matched_people_subset = [dict(item) for item in list(lazy_facts.get("matched_people_subset") or []) if isinstance(item, dict)]
    matched_org_subset = [dict(item) for item in list(lazy_facts.get("matched_org_subset") or []) if isinstance(item, dict)]
    period_start = _first_nonempty_text(
        eager_facts.get("period_start"),
        eager_facts.get("start_date"),
        eager_facts.get("sttus_start_dt"),
    )
    period_end = _first_nonempty_text(
        eager_facts.get("period_end"),
        eager_facts.get("end_date"),
        eager_facts.get("sttus_end_dt"),
    )
    total_budget = _first_nonempty_text(
        eager_facts.get("total_budget"),
        eager_facts.get("research_cost_total"),
        eager_facts.get("gov_budget_total"),
    )

    if _contains_all(
        text,
        [
            ["총", "몇", "number", "count", "how many"],
            ["연구자", "연구원", "researcher", "participant"],
        ],
    ):
        return f"이 과제의 참여 연구자는 총 {participant_count}명입니다."

    if _has_any(
        text,
        "연구책임자",
        "책임 연구자",
        "총괄 책임자",
        "lead researcher",
        "principal investigator",
    ):
        if lead_names:
            return f"연구책임자는 {', '.join(lead_names)}입니다."
        return "현재 활성 anchor 기준으로 연구책임자 이름을 확인하지 못했습니다."

    if _contains_all(
        text,
        [
            ["총", "몇", "number", "count", "how many"],
            ["기관", "소속", "organization", "org"],
        ],
    ):
        return f"참여기관은 총 {participant_org_count}개입니다."

    if _has_any(text, "참여기관", "참여 기관", "기관 목록", "소속 기관"):
        org_names = [str(item.get("org_nm") or "").strip() for item in (matched_org_subset or org_preview) if str(item.get("org_nm") or "").strip()]
        if org_names:
            return f"현재 anchor 기준 참여기관 예시는 {', '.join(org_names[:5])}입니다."
        return "현재 활성 anchor 기준으로 참여기관 정보를 확인하지 못했습니다."

    if _has_any(text, "참여연구자", "참여 연구자", "연구자 목록", "연구자 이름"):
        people_names = [str(item.get("name") or "").strip() for item in (matched_people_subset or people_preview) if str(item.get("name") or "").strip()]
        if people_names:
            return f"현재 anchor 기준 연구자 예시는 {', '.join(people_names[:5])}입니다."
        return "현재 활성 anchor 기준으로 연구자 정보를 확인하지 못했습니다."

    # 연구 기간 (period) — "기간", "언제부터", "시작일", "종료일"
    if _has_any(text, "연구기간", "연구 기간", "사업기간", "사업 기간", "기간", "시작일", "종료일", "언제부터", "언제까지"):
        if period_start or period_end:
            start_txt = period_start or "미상"
            end_txt = period_end or "미상"
            return f"연구 기간은 {start_txt} ~ {end_txt}입니다."
        return None

    # 연구비 / 총 사업비 (budget)
    if _has_any(text, "연구비", "사업비", "총 사업비", "총사업비", "예산"):
        if total_budget:
            return f"총 연구비는 {total_budget}입니다."
        return None

    return None


def resolve_followup_from_facts(
    *,
    question: str,
    view_state: Any,
    raw_payload_memory: Dict[str, Any],
    base_route: str,
) -> Optional[Dict[str, Any]]:
    """Active anchor의 raw payload로부터 팩트 기반 직접 답변을 합성한다.

    성공 시 `answer_artifact`와 `raw_payload_record` 를 포함한 dict를 반환하고,
    실패(활성 anchor/record/payload 없음 또는 패턴 매칭 실패) 시 None을 반환한다.
    실패 사유를 얻으려면 `diagnose_followup_fact_miss`를 호출한다.
    """
    active_entity = get_active_subject_entity(view_state)
    anchor_key = build_anchor_key_from_view_entity(active_entity)
    if not anchor_key:
        return None
    record = get_anchor_record(raw_payload_memory, anchor_key=anchor_key)
    if record is None:
        return None
    payload = decompress_raw_payload(record)
    if not payload:
        return None

    eager_facts = build_eager_facts(payload)
    lazy_facts = (
        build_lazy_facts(payload)
        if needs_lazy_facts(base_route=base_route, people_terms=None, person_ids=None, org_terms=None, org_role=None, question=question)
        else {}
    )
    answer_text = _answer_from_facts(question, eager_facts, lazy_facts)
    if not answer_text:
        return None

    artifact = AnswerArtifact(
        text=answer_text,
        answer_kind="fact_followup",
        stream_metrics={"content_chars": len(answer_text), "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        meta={"answer_source": "raw_payload_facts", "anchor_key": anchor_key},
    )
    return {
        "answer_artifact": artifact,
        "anchor_hit": True,
        "followup_resolved_by_facts": True,
        "raw_payload_record": record,
        "eager_facts": eager_facts,
        "lazy_facts": lazy_facts,
    }


def diagnose_followup_fact_miss(
    *,
    question: str,
    view_state: Any,
    raw_payload_memory: Dict[str, Any],
    base_route: str,
) -> Dict[str, Any]:
    """resolve_followup_from_facts가 None을 반환한 이유를 분류해 반환한다.

    ADR-0013에 정의된 reason code:
    - no_anchor_key: active entity 또는 anchor key 미존재
    - no_record: anchor는 있으나 raw_payload record가 없음
    - no_payload: record는 있으나 decompress 실패/빈 payload
    - pattern_unmatched: payload는 있으나 질문이 fact 패턴에 매칭되지 않음
    """
    active_entity = get_active_subject_entity(view_state)
    anchor_key = build_anchor_key_from_view_entity(active_entity)
    if not anchor_key:
        return {
            "reason": "no_anchor_key",
            "anchor_key": None,
            "has_anchor": active_entity is not None,
            "has_record": False,
        }
    record = get_anchor_record(raw_payload_memory, anchor_key=anchor_key)
    if record is None:
        return {
            "reason": "no_record",
            "anchor_key": anchor_key,
            "has_anchor": True,
            "has_record": False,
        }
    payload = decompress_raw_payload(record)
    if not payload:
        return {
            "reason": "no_payload",
            "anchor_key": anchor_key,
            "has_anchor": True,
            "has_record": True,
        }
    return {
        "reason": "pattern_unmatched",
        "anchor_key": anchor_key,
        "has_anchor": True,
        "has_record": True,
    }


__all__ = [
    "resolve_followup_from_facts",
    "diagnose_followup_fact_miss",
]
