"""
프로젝트(과제) 도메인 스키마

프로젝트 데이터의 매핑 규칙과 reference 추출 규칙을 정의합니다.
"""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_project_title(values: Dict[str, Any]) -> str:
    """
    프로젝트 title 포맷팅
    
    Args:
        values: title 생성에 필요한 필드값 딕셔너리
                - kor_pjt_nm: 국문과제명
                - stan_yr: 기준년도
    
    Returns:
        포맷된 title 문자열 (예: "해양 생태계 변화 예측 모델 개발(2023)")
    """
    kor_pjt_nm = values.get("kor_pjt_nm", "과제명 미상")
    stan_yr = values.get("stan_yr", "")
    
    if stan_yr:
        return f"{kor_pjt_nm}({stan_yr})"
    return kor_pjt_nm


def get_project_schema() -> TagSchema:
    """
    프로젝트 마스터 스키마 생성
    
    Returns:
        프로젝트용 TagSchema 인스턴스
    """
    return TagSchema(
        tag=DataTag.PROJECT,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
            # --- meta_basic (기본 정보) ---
            "pjt_id": "과제고유번호",
            "stan_yr": "기준년도",
            "pjt_no": "과제번호",
            "pjt_prfrm_org_nm": "과제수행기관명",
            "kor_pjt_nm": "국문과제명",
            "eng_pjt_nm": "영문과제명",
            "rndco_tot_amt": "연구비합계금액",
            "rsch_goal_abstract": "연구목표요약",
            "rsch_abstract": "연구내용요약",
            "kor_kywd": "한글키워드",
            "eng_kywd": "영문키워드",
            "tot_rsch_start_dt": "총연구기간시작일",
            "tot_rsch_end_dt": "총연구기간종료일",

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
            "id": "pjt_id",  # 과제 고유번호를 primary key로 사용
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["kor_pjt_nm", "stan_yr"],
        title_formatter=_format_project_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )
