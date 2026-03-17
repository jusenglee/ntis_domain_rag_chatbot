"""
화합물 도메인 스키마

화합물 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_compound_title(values: Dict[str, Any]) -> str:
    """
    화합물 title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - compound_nm: 화합물명
    
    Returns:
        compound_nm
    """
    name = values.get("compound_nm", "화합물명 미상")
    
    return name


def get_compound_schema() -> TagSchema:
    """
    화합물 스키마 생성
    
    Returns:
        화합물용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.COMPOUND,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "rst_id": "성과ID",
            "compound_nm": "화합물명",
            "chem_fmla": "화학식",
            "molecular_wt": "분자량",
            "deposit_no": "기탁번호",
            "deposit_docu_grant_dt": "기탁필증부여일",
            "depositor_nm": "기탁자명",
            "deposit_org_nm": "기탁기관명",
        
            # --- meta_detail (상세 정보) ---
            "dest_alrd_gwan_eng_abrv_nm": "기탁자 기관명 영문약어",

            # --- prtcp_mp (연구원 정보) ---
            "hm_nm":"연구원 이름",
            "blng_org_nm":"연구원 소속"
        },
        
        # reference 추출 매핑
        reference_map={
            "id": "rst_id",
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["compound_nm"],
        title_formatter=_format_compound_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )
