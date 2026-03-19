"""시설장비 domain payload를 TagSchema로 정의하는 mapper 규칙이다."""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_equipment_title(values: Dict[str, Any]) -> str:
    """시설장비 payload에서 title로 쓸 문자열을 조합한다."""
    name = values.get("kor_fclt_equip_nm", "시설장비명 미상")
    
    return name


def get_equipment_schema() -> TagSchema:
    """시설장비 tag에 대한 TagSchema를 반환한다.

    label_map, reference_map, title 규칙을 한곳에 묶어 mapper registry가 이 domain을 같은 방식으로 읽게 한다.
    """
    return TagSchema(
        tag=DataTag.EQUIPMENT,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
            
            # --- meta_basic (기본 정보) ---
            "rst_id": "성과ID",
            "fclt_equip_regist_no": "시설장비등록번호",
            "fclt_equip_slct_nm": "시설장비구분명",
            "kor_fclt_equip_nm": "국문시설장비명",
            "eng_fclt_equip_nm": "영문시설장비명",
            "model_nm": "모델명",
            "std_cls_nm": "표준분류명",
            "poss_org_nm": "보유기관명",
            "poss_org_type_nm": "보유기관유형명",
            "prctuse_rps_nm": "활용용도명",
            "mnfct_cmpny_nm": "제작회사명",
            "etc_prctuse_purp_des": "기타활용목적내용",
            "prctuse_stat_nm": "활용상태명",
            "fclt_equip_ftr_des": "시설장비특징내용",
            "fclt_equip_cmps_des": "시설장비구성내용",
        
            # --- meta_detail (상세 정보) ---
            "main_fclt_equip_id": "주시설장비ID",
            "main_fclt_equip_regist_no": "주시설장비등록번호",
            "fixed_asset_no": "고정자산관리번호",
            "mnfct_nat_nm": "제작국가명",
            "model_slct_nm": "모델구분명",
            "poss_org_regn_nm": "보유기관지역명",
            "org_zip": "기관우편번호",
            "poss_org_addr": "보유기관주소",
            "prctuse_scop_nm": "활용범위명",
            "copu_scop_nm": "공동활용범위명",
            "copu_mnr_nm": "공동활용방법명",
            "fclt_equip_pric_nm": "시설장비담당자명",
            "fclt_equip_cnin_tel_no": "시설장비문의처전화번호",
            "use_eg_des": "사용예시내용",

            # --- prtcp_mp (연구원 정보) ---
            "hm_nm":"연구원 이름",
            "blng_org_nm":"연구원 소속"
        },
        
        # reference 추출 매핑
        reference_map={
            "id": "rst_id",
        },
        
        # title 구성 필드 및 포맷터
        title_fields=["kor_fclt_equip_nm"],
        title_formatter=_format_equipment_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )
