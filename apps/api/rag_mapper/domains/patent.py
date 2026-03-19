"""특허 domain payload를 TagSchema로 정의하는 mapper 규칙이다."""

from typing import Dict, Any
from apps.api.rag_mapper.schema_types import TagSchema, DataTag


def _format_patent_title(values: Dict[str, Any]) -> str:
    """특허 payload에서 title로 쓸 문자열을 조합한다."""
    name = values.get("ipr_invention_nm", "특허명 미상")
    
    return name


def get_patent_schema() -> TagSchema:
    """특허 tag에 대한 TagSchema를 반환한다.

    label_map, reference_map, title 규칙을 한곳에 묶어 mapper registry가 이 domain을 같은 방식으로 읽게 한다.
    """
    return TagSchema(
        tag=DataTag.PATENT,
        
        # 필드명 -> 자연어 라벨 매핑
        label_map={
        
            # --- meta_basic (기본 정보) ---
            "rst_id": "성과ID",
            "ipr_regist_type_nm": "지적재산권등록유형",
            "ipr_invention_nm": "발명의명칭",
            "ipr_clss_slct_nm": "지적재산권종류",
            "ipr_regist_nat_nm": "출원(등록)국가",
            "ipr_regist_dt": "지적재산권등록일자",
            "ipr_frgn_app_yn": "해외출원여부",
            "aply_no": "출원번호",
            "regist_no": "등록번호",
            "ipr_aply_dt": "지적재산권출원일자",
            "clpr_no": "우선권주장번호",
        
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
        title_fields=["ipr_invention_nm"],
        title_formatter=_format_patent_title,
        data_fields=["meta_basic", "meta_detail", "prtcp_mp"]
    )
