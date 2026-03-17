"""
QNA 도메인 스키마

QNA 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_qna_title(values: Dict[str, Any]) -> str:
    """
    QNA title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - question: QNA명
    
    Returns:
        question
    """
    name = values.get("question", "QNA 제목 미상")
    
    return name


def get_qna_schema() -> TagSchema:
    """
    QNA 스키마 생성
    
    Returns:
        QNA용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.QNA,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "question": "질문",
            "answer": "답변",
            "type": "문의 유형",
            "date": "문의 날짜",
        
            # --- meta_detail (상세 정보) ---
        },
        
        # reference 추출 매핑
        reference_map={
            "id": "rst_id",
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["question"],
        title_formatter=_format_qna_title,
        data_fields=["meta_basic", "meta_detail"]
    )
