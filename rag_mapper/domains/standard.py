"""
표준 도메인 스키마

표준 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from rag_mapper.mapping_config import TagSchema, DataTag


def _format_standard_title(values: Dict[str, Any]) -> str:
    """
    표준 title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - std_nm: 표준명
    
    Returns:
        ipr_invention_nm
    """
    name = values.get("std_nm", "표준명 미상")
    
    return name


def get_standard_schema() -> TagSchema:
    """
    표준 스키마 생성
    
    Returns:
        표준용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.STANDARD,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "std_type_nm": "참조표준",
            "std_nm": "표준명",
            "std_no": "표준번호",
            "std_dscrp_abstract": "표준설명",
            "std_aprv_dt": "표준승인일자",

            # --- meta_detail (상세 정보) ---
            
            # --- prtcp_mp (연구원 정보) ---
            "hm_nm":"연구원 이름",
            "blng_org_nm":"연구원 소속"
        },
        
        # reference 추출 매핑
        reference_map={
            "id": "rst_id",
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["std_nm"],
        title_formatter=_format_standard_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )