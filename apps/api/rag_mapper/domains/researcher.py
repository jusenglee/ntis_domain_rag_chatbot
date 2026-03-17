"""
연구자 도메인 스키마

연구자 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_researcher_title(values: Dict[str, Any]) -> str:
    """
    연구자 title 포맷팅
    
    이름과 소속을 조합하여 "이름(소속)" 형태로 반환합니다.
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - name: 연구자명
                - org: 소속기관
    
    Returns:
        포맷된 title 문자열 (예: "김연구(한국해양연구원)")
    """
    name = values.get("name", "미상")
    org = values.get("org", "소속 미상")
    
    return f"{name}({org})"


def get_researcher_schema() -> TagSchema:
    """
    연구자 스키마 생성
    
    Returns:
        연구자용 TagSchema 인스턴스
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
