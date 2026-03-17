"""
연구보고서 도메인 스키마

연구보고서 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_report_title(values: Dict[str, Any]) -> str:
    """
    연구보고서 title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - kor_rpt_title_nm: 연구보고서명
    
    Returns:
        kor_rpt_title_nm
    """
    name = values.get("kor_rpt_title_nm", "연구보고서명 미상")
    
    return name


def get_report_schema() -> TagSchema:
    """
    연구보고서 스키마 생성
    
    Returns:
        연구보고서용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.REPORT,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "rst_id": "성과ID",
            "kor_rpt_title_nm": "국문보고서제목",
            "eng_rpt_title_nm": "영문보고서제목",
            "pub_org_nm": "발행기관명",
            "pub_dt": "발행년월",
            "kor_abstract": "국문요약",
            "eng_abstract": "영문요약",
            "kor_kywd": "한글키워드",
            "eng_kywd": "영문키워드",
        
            # --- meta_detail (상세 정보) ---
            "pub_nat_nm": "발행국가명",
            "rpt_type_nm": "보고서유형명",
            "snt_lcls_all_nm": "과학기술표준분류명",
            "snt_mcls_all_nm": "과학기술분류 중분류명",
            "snt_scls_all_nm": "과학기술분류 소분류명",

            # --- prtcp_mp (연구원 정보) ---
            "hm_nm":"연구원 이름",
            "blng_org_nm":"연구원 소속"
        },
        
        # reference 추출 매핑
        reference_map={
            "id": "rst_id",
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["kor_rpt_title_nm"],
        title_formatter=_format_report_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )
