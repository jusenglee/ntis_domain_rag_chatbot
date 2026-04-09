"""표준 domain payload를 TagSchema로 정의하는 mapper 규칙이다."""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_standard_title(values: Dict[str, Any]) -> str:
    """표준 payload에서 title로 쓸 문자열을 조합한다."""
    name = values.get("std_nm", "표준명 미상")
    
    return name


def get_standard_schema() -> TagSchema:
    """표준 tag에 대한 TagSchema를 반환한다.

    label_map, reference_map, title 규칙을 한곳에 묶어 mapper registry가 이 domain을 같은 방식으로 읽게 한다.
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
