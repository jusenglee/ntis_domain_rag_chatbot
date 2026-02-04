"""
기술요약정보 도메인 스키마

기술요약정보 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from rag_mapper.mapping_config import TagSchema, DataTag


def _format_tech_summary_title(values: Dict[str, Any]) -> str:
    """
    기술요약정보 title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - kor_tech_nm: 기술요약정보명
    
    Returns:
        kor_tech_nm
    """
    name = values.get("kor_tech_nm", "기술요약정보명 미상")
    
    return name


def get_tech_summary_schema() -> TagSchema:
    """
    기술요약정보 스키마 생성
    
    Returns:
        기술요약정보용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.TECH_SUMMARY,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "rst_id": "성과ID",
            "kor_tech_nm": "국문기술명",
            "eng_tech_nm": "영문기술명",
            "tech_sum_clas_dfn": "기술의정의",
            "tech_sum_clas_des": "기술의내용",
            "appl_area_info": "적용분야",
            "kor_kywd": "국문키워드",
            "eng_kywd": "영문키워드",
            "tech_dev_stat": "기술완성도",
        
            # --- meta_detail (상세 정보) ---
            "tech_tran_type_nm": "기술이전유형",
            "tech_tran_yn": "기술이전여부",
            "tech_tran_ddct_dept_nm": "기술이전전담부서명",
            "tech_tran_admin": "기술이전담당자명",
            "tech_tran_pric_org_nm": "기술이전담당자 기관명",
            "tech_tran_pric_tel_no": "기술이전담당자 전화번호",
            "tech_tran_pric_email": "기술이전담당자 이메일",

            # --- prtcp_mp (연구원 정보) ---
            "hm_nm":"연구원 이름",
            "blng_org_nm":"연구원 소속"
        },
        
        # reference 추출 매핑
        reference_map={
            "id": "rst_id",
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["kor_tech_nm"],
        title_formatter=_format_tech_summary_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )