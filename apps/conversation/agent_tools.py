from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentToolSpec(BaseModel):
    """Tool contract exposed to the Dialogue Agent."""

    name: str
    description: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    implemented: bool = True


def _schema(properties: Dict[str, Any], *, required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required or []),
        "additionalProperties": False,
    }


def default_agent_tool_specs(*, implemented_only: bool = False) -> List[AgentToolSpec]:
    """Return the agent tool registry. Availability is not feature-flagged."""

    specs = [
        AgentToolSpec(
            name="search_ntis_domain",
            description=(
                "과제, 성과, 인력, 기관, 지원사업 등 NTIS 도메인에 대한 새로운 검색을 시작합니다. "
                "사용자가 새로운 검색 대상을 명시하거나, 이전 맥락과 무관한 새로운 질문을 할 때 사용합니다. "
                "특정 연구자/기관의 상세 활동 이력 조회가 목적일 때는 'search_subject_activity'를 우선 고려하십시오."
            ),
            input_schema=_schema(
                {
                    "query": {"type": "string", "description": "자연어 검색 질의어"},
                    "domain_head": {
                        "type": "string",
                        "enum": ["project", "perf", "people", "org", "support", "auto"],
                        "description": "검색 대상 도메인. 불분명할 경우 auto를 사용하십시오.",
                    },
                    "year_from": {"type": ["integer", "null"], "description": "검색 시작 연도 (YYYY)"},
                    "year_to": {"type": ["integer", "null"], "description": "검색 종료 연도 (YYYY)"},
                    "people_name": {"type": ["string", "null"], "description": "인력명 (도메인이 people일 때 유효)"},
                    "org_name": {"type": ["string", "null"], "description": "기관명 (도메인이 org일 때 유효)"},
                    "perf_type": {"type": ["string", "null"], "description": "성과 유형 (논문, 특허 등)"},
                    "limit": {"type": ["integer", "null"], "description": "조회 제한 건수"},
                },
                required=["query"],
            ),
        ),
        AgentToolSpec(
            name="search_subject_activity",
            description=(
                "특정 연구자 또는 기관의 활동 이력(과제 참여, 성과 실적 등)을 상세히 조회합니다. "
                "인물/기관의 식별이 명확한 경우의 활동 내역, 참여 이력, 실적 조회를 담당합니다."
            ),
            input_schema=_schema(
                {
                    "subject_kind": {"type": "string", "enum": ["people", "org"], "description": "조회 대상 유형 (인력 또는 기관)"},
                    "subject_name": {"type": "string", "description": "조회 대상 명칭"},
                    "affiliation_org_name": {"type": ["string", "null"], "description": "소속 기관명 (인물 식별 시 활용)"},
                    "year_from": {"type": ["integer", "null"], "description": "조회 시작 연도"},
                    "year_to": {"type": ["integer", "null"], "description": "조회 종료 연도"},
                    "role": {"type": ["string", "null"], "enum": ["연구책임자", "참여연구원"], "description": "수행 역할"},
                    "target": {
                        "type": "string",
                        "enum": ["project", "perf", "both"],
                        "description": "조회 대상 데이터 유형 (과제, 성과, 또는 모두)",
                    },
                    "limit": {"type": ["integer", "null"]},
                    "query": {"type": ["string", "null"], "description": "추가 필터링을 위한 질의어"},
                },
                required=["subject_kind", "subject_name"],
            ),
        ),
        AgentToolSpec(
            name="refine_current_subject",
            description=(
                "현재 대화 주제(Subject)를 유지하면서 기간, 역할, 성과 유형, 대상 전환 등으로 검색 조건을 정교화합니다. "
                "사용자가 '그 중에서 최근 3년 것만 보여줘'와 같이 이전 질문의 대상을 이어서 필터링할 때 사용합니다."
            ),
            input_schema=_schema(
                {
                    "subject_ref": {"type": "string", "enum": ["current_subject"], "description": "현재 맥락의 대상을 참조함"},
                    "affiliation_org_name": {
                        "type": ["string", "null"],
                        "description": "소속 기관명을 추가하여 대상을 더 구체화함",
                    },
                    "year_from": {"type": ["integer", "null"]},
                    "year_to": {"type": ["integer", "null"]},
                    "role": {"type": ["string", "null"]},
                    "perf_type": {"type": ["string", "null"]},
                    "target": {
                        "type": "string",
                        "enum": ["project", "perf", "activity_history", "both"],
                        "description": "조회 대상 전환 (예: 과제 목록에서 성과 목록으로)",
                    },
                    "limit": {"type": ["integer", "null"]},
                },
                required=["subject_ref"],
            ),
        ),
        AgentToolSpec(
            name="lookup_specific_entity",
            description=(
                "최근 발행된 목록(published manifest)에서 특정 항목을 직접 조회합니다. "
                "'해당 과제', '그 항목', 명시적 제목([[제목]] 등) 참조에 사용하십시오. "
                "entity_ref는 'rank:N'(목록 순위, 예: 'rank:1') 또는 'title:[제목]' 형식을 사용합니다."
            ),
            implemented=True,
            input_schema=_schema(
                {
                    "entity_ref": {"type": "string", "description": "'rank:N' 또는 'title:[제목]' 또는 내부 ID"},
                    "entity_kind": {"type": "string", "enum": ["project", "perf", "people", "org", "auto"]},
                    "detail_level": {"type": "string", "enum": ["summary", "detail"], "description": "조회 상세 수준"},
                },
                required=["entity_ref"],
            ),
        ),
        AgentToolSpec(
            name="join_project_perf",
            description="특정 과제와 관련된 성과를 찾거나, 특정 성과가 나온 과제를 추적하는 등 연계 정보를 조회합니다.",
            implemented=False,
            input_schema=_schema(
                {
                    "direction": {"type": "string", "enum": ["project_to_perf", "perf_to_project"], "description": "연계 추적 방향"},
                    "anchor_ref": {"type": "string", "description": "기준이 되는 엔티티 참조"},
                    "perf_type": {"type": ["string", "null"]},
                },
                required=["direction", "anchor_ref"],
            ),
        ),
        AgentToolSpec(
            name="ask_user_for_clarification",
            description="사용자의 질문이 모호하여 실행이 불가능할 때, 부족한 정보나 선택지를 사용자에게 요청합니다.",
            input_schema=_schema(
                {
                    "reason": {"type": "string", "description": "확인이 필요한 이유 (시스템 내부용)"},
                    "question_to_user": {"type": "string", "description": "사용자에게 직접 던질 질문 문구"},
                    "suggested_options": {"type": "array", "items": {"type": "string"}, "description": "사용자가 선택할 수 있는 예시 답변 목록"},
                },
                required=["reason", "question_to_user"],
            ),
        ),
    ]

    if implemented_only:
        return [spec for spec in specs if spec.implemented]
    return specs


def tool_spec_by_name() -> Dict[str, AgentToolSpec]:
    return {spec.name: spec for spec in default_agent_tool_specs()}
