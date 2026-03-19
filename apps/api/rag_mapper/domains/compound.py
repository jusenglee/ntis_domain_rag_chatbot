"""화합물 domain payload를 TagSchema로 정의하는 mapper 규칙이다."""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_compound_title(values: Dict[str, Any]) -> str:
    """화합물 payload에서 title로 쓸 문자열을 조합한다."""
    name = values.get("compound_nm", "화합물명 미상")
    
    return name


def get_compound_schema() -> TagSchema:
    """화합물 tag에 대한 TagSchema를 반환한다.

    label_map, reference_map, title 규칙을 한곳에 묶어 mapper registry가 이 domain을 같은 방식으로 읽게 한다.
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
