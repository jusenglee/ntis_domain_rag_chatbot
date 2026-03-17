"""
생명정보 도메인 스키마

생명정보 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_organism_info_title(values: Dict[str, Any]) -> str:
    """
    생명정보 title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - orgsm_info_nm: 생명정보명
    
    Returns:
        orgsm_info_nm
    """
    name = values.get("orgsm_info_nm", "생명정보명 미상")
    
    return name


def get_organism_info_schema() -> TagSchema:
    """
    생명정보 스키마 생성
    
    Returns:
        생명정보용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.ORGANISM_INFO,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "rst_id": "성과ID",
            "orgsm_info_nm": "생명정보명",
            "orgsm_info_lcls_nm": "생명정보대분류명",
            "orgsm_info_scls_nm": "생명정보소분류명",
            "orgsm_info_fom_nm": "생명정보형태명",
            "regist_docu_no": "등록필증번호",
            "regist_docu_grant_dt": "등록필증부여일자",
            "registor_nm": "등록자명",
            "regist_org_nm": "등록기관명",
            "kywd": "키워드",
            
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
        title_fields=["orgsm_info_nm"],
        title_formatter=_format_organism_info_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )
