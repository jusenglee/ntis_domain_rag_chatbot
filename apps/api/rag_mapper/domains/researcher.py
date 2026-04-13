"""연구자 domain payload를 TagSchema로 정의하는 mapper 규칙이다."""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_researcher_title(values: Dict[str, Any]) -> str:
    """연구자 payload에서 title로 쓸 문자열을 조합한다."""
    name = values.get("name", "미상")
    org = values.get("org", "소속 미상")
    
    return f"{name}({org})"


def get_researcher_schema() -> TagSchema:
    """연구자 tag에 대한 TagSchema를 반환한다.

    label_map, reference_map, title 규칙을 한곳에 묶어 mapper registry가 이 domain을 같은 방식으로 읽게 한다.
    """
    return TagSchema(
        tag=DataTag.RESEARCHER,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
            "emp_id": "연구자ID",
            "name": "연구자명",
            "org": "소속",
            "role": "역할",
        },
        
        # reference 추출 매핑
        reference_map={
            "id": "emp_id",  # 연구자 ID를 primary key로 사용
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["name", "org"],
        title_formatter=_format_researcher_title
    )
