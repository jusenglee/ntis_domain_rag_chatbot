"""
생물자원 도메인 스키마

생물자원 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_organism_resource_title(values: Dict[str, Any]) -> str:
    """
    생물자원 title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - orgsm_resource_nm: 생물자원명
    
    Returns:
        orgsm_resource_nm
    """
    name = values.get("orgsm_resource_nm", "생물자원명 미상")
    
    return name


def get_organism_resource_schema() -> TagSchema:
    """
    생물자원 스키마 생성
    
    Returns:
        생물자원용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.ORGANISM_RESOURCE,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "rst_id": "성과ID",
            "orgsm_resource_nm": "생물자원명",
            "orgsm_resource_cls_nm": "생물자원분류코드명",
            "depositor_nm": "기탁자명",
            "depositor_org_nm": "기탁기관명",
            "kywd": "키워드",
            "deposit_no": "기탁번호",
            "deposit_docu_grant_dt": "기탁필증부여일자",
            "orgsm_resource_no": "생물자원번호",
        
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
        title_fields=["orgsm_resource_nm"],
        title_formatter=_format_organism_resource_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )
