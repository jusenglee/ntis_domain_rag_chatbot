"""품종 domain payload를 TagSchema로 정의하는 mapper 규칙이다."""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_variety_title(values: Dict[str, Any]) -> str:
    """품종 payload에서 title로 쓸 문자열을 조합한다."""
    name = values.get("nvr_nm", "신품종명 미상")
    
    return name


def get_variety_schema() -> TagSchema:
    """품종 tag에 대한 TagSchema를 반환한다.

    label_map, reference_map, title 규칙을 한곳에 묶어 mapper registry가 이 domain을 같은 방식으로 읽게 한다.
    """
    return TagSchema(
        tag=DataTag.VARIETY,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "rst_id": "성과번호",
            "nvr_applicant_regist_slct_nm": "출원 등록구분명",
            "nvr_nm": "신품종명칭",
            "nvr_applicant_regist_nat_nm": "출원(등록)국가",
            "nvr_aply_no": "출원번호",
            "nvr_regist_no": "등록번호",
            "nvr_aply_dt": "출원일자",
            "nvr_regist_dt": "등록일자",
            "nvr_prot_psbl_yn": "분양가능여부",
            "nvr_open_dt": "공개일자",
            "nvr_ovrsea_applicant_slct_nm": "해외출원구분명",
            "nvr_deposit_no": "기탁번호",
            "nvr_deposit_dt": "기탁일자",
    
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
        title_fields=["nvr_nm"],
        title_formatter=_format_variety_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )
