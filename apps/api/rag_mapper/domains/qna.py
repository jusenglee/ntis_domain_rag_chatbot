"""QnA domain payload를 TagSchema로 정의하는 mapper 규칙이다."""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_qna_title(values: Dict[str, Any]) -> str:
    """QnA payload에서 title로 쓸 문자열을 조합한다."""
    name = values.get("question", "QNA 제목 미상")
    
    return name


def get_qna_schema() -> TagSchema:
    """QnA tag에 대한 TagSchema를 반환한다.

    label_map, reference_map, title 규칙을 한곳에 묶어 mapper registry가 이 domain을 같은 방식으로 읽게 한다.
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
