"""
매뉴얼 도메인 스키마

매뉴얼 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from rag_mapper.mapping_config import TagSchema, DataTag


def _format_manual_title(values: Dict[str, Any]) -> str:
    """
    매뉴얼 title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - ipr_invention_nm: 매뉴얼명
    
    Returns:
        ipr_invention_nm
    """
    name = values.get("file_name", "매뉴얼명 미상")
    
    return name


def get_manual_schema() -> TagSchema:
    """
    매뉴얼 스키마 생성
    
    Returns:
        매뉴얼용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.MANUAL,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "section": "항목",
            "sub_section": "세부 항목",
            "guide": "절차",
            "file_name": "파일명",

            # --- meta_detail (상세 정보) ---
        },
        
        # reference 추출 매핑
        reference_map={
            "id": "rst_id",
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["file_name"],
        title_formatter=_format_manual_title,
        data_fields=["meta_basic", "meta_detail"]
    )