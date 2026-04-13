"""기술요약 domain payload를 TagSchema로 정의하는 mapper 규칙이다."""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_tech_summary_title(values: Dict[str, Any]) -> str:
    """기술요약 payload에서 title로 쓸 문자열을 조합한다."""
    name = values.get("kor_tech_nm", "기술요약정보명 미상")
    
    return name


def get_tech_summary_schema() -> TagSchema:
    """기술요약 tag에 대한 TagSchema를 반환한다.

    label_map, reference_map, title 규칙을 한곳에 묶어 mapper registry가 이 domain을 같은 방식으로 읽게 한다.
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
