"""
소프트웨어 도메인 스키마

소프트웨어 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from rag_mapper.mapping_config import TagSchema, DataTag


def _format_software_title(values: Dict[str, Any]) -> str:
    """
    소프트웨어 title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - sw_nm: 소프트웨어명
    
    Returns:
        sw_nm
    """
    name = values.get("sw_nm", "소프트웨어명 미상")
    
    return name


def get_software_schema() -> TagSchema:
    """
    소프트웨어 스키마 생성
    
    Returns:
        소프트웨어용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.SOFTWARE,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "rst_id": "성과ID",
            "sw_clss_nm": "소프트웨어 종류",
            "sw_nm": "소프트웨어명",
            "appl_area_expl": "적용분야",
            "sw_ftr_expl": "프로그램특징",
            "sw_regist_no": "S/W 등록번호",
            "sw_smmry": "소프트웨어개요",
            "dev_goal_des": "개발목표",
            "apprv_nm": "인증명",
            "supt_apprv_org_nm": "인증기관명",
            "apprv_regist_no": "인증등록번호",
            "apprv_regist_dt": "인증등록일자",
        
            # --- meta_detail (상세 정보) ---
            "copymtrl_form_nm": "복제물형태",
            "sell_slct_nm": "판매구분",
            "appl_std_expl": "적용표준",
            "dev_start_dt": "개발시작일자",
            "dev_end_dt": "개발종료일자",

            # --- prtcp_mp (연구원 정보) ---
            "hm_nm":"연구원 이름",
            "blng_org_nm":"연구원 소속"
        },
        
        # reference 추출 매핑
        reference_map={
            "id": "rst_id",
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["sw_nm"],
        title_formatter=_format_software_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )