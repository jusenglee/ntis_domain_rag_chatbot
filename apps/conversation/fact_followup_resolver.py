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


def _answer_from_facts(question: str, eager_facts: Dict[str, Any], lazy_facts: Dict[str, Any]) -> Optional[str]:
    text = _normalize_text(question).lower()
    participant_count = int(eager_facts.get("participant_count") or 0)
    participant_org_count = int(eager_facts.get("participant_org_count") or 0)
    lead_names = [str(item).strip() for item in list(eager_facts.get("lead_researcher_names") or []) if str(item).strip()]
    people_preview = [dict(item) for item in list(eager_facts.get("people_preview") or []) if isinstance(item, dict)]
    org_preview = [dict(item) for item in list(eager_facts.get("org_preview") or []) if isinstance(item, dict)]
    matched_people_subset = [dict(item) for item in list(lazy_facts.get("matched_people_subset") or []) if isinstance(item, dict)]
    matched_org_subset = [dict(item) for item in list(lazy_facts.get("matched_org_subset") or []) if isinstance(item, dict)]

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

    return None


def resolve_followup_from_facts(
    *,
    question: str,
    view_state: Any,
    raw_payload_memory: Dict[str, Any],
    base_route: str,
) -> Optional[Dict[str, Any]]:
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
