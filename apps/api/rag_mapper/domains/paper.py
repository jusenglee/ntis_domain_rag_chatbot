"""
논문 도메인 스키마

논문 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_paper_title(values: Dict[str, Any]) -> str:
    """
    논문 title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - paper_nm: 논문명
    
    Returns:
        paper_nm
    """
    name = values.get("paper_nm", "논문명 미상")
    
    return name


def get_paper_schema() -> TagSchema:
    """
    논문 스키마 생성
    
    Returns:
        논문용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.PAPER,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
            # --- meta_basic (기본 정보) ---
            "rst_id": "성과ID",
            "paper_nm": "논문명",
            "abstract_str": "초록",
            "jrnl_nm": "저널명",
            "kor_kywd": "한글키워드",
            "eng_kywd": "영문키워드",
            "paper_regist_no": "논문등록번호",

            # --- meta_detail (상세 정보) ---
            "rnd_phase_nm": "연구개발단계명",
            "rsch_exec_suj_nm": "연구수행주체명",
            "snt_std_cls_nm": "과학기술표준분류명",
            "rsch_area_cls1_all_nm": "과학기술표준분류명(신)-1",
            "rsch_area_cls2_all_nm": "과학기술표준분류명(신)-2",
            "rsch_area_cls3_all_nm": "과학기술표준분류명(신)-3",
            "mstr_lcls_nm": "부처자제분류 대분류명",
            "mstr_mcls_nm": "부처자제분류 중분류명",
            "mstr_scls_nm": "부처자제분류 소분류명",
            "rnd_clas_nm": "연구개발성격구분",
            "rnd_chrct_slct_nm": "실용화대상여부구분",
            "t6tech_all_nm": "6T관련기술명",
            "nat_strt_tech_all_nm": "국가중점과학기술명",
            "tech_lifecyc_nm": "기술수명주기명",
            "dtl_pjt_clas_nm": "세부과제성격명",
            "pjt_prgs_stat_slct_nm": "과제진행상태구분명",
            "gt_cls_cd_nm": "녹색기술분야 분류명",
            "appl_area_cls1_nm": "적용분야분류1",
            "appl_area_cls2_nm": "적용분야분류2",
            "appl_area_cls3_nm": "적용분야분류3",


            # --- prtcp_mp (연구원 정보) ---
            "hm_nm":"연구원 이름",
            "blng_org_nm":"연구원 소속"
        },
        
        # reference 추출 매핑
        reference_map={
            "id": "rst_id",
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["paper_nm"],
        title_formatter=_format_paper_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )
