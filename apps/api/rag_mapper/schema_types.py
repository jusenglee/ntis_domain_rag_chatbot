from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class DataTag(str, Enum):
    """RAG mapper가 인식하는 원천 콘텐츠 tag 집합이다.

    각 값은 domain schema registry를 찾는 키로 쓰이며, prompt view 의미를 직접 결정하지는 않는다.
    """

    COMPOUND = "IRD_NAI_RI_COMPOUND"
    EQUIPMENT = "IRD_NAI_RI_FCLT_EQUIP"
    MANUAL = "MANUAL"
    ORGANISM_INFO = "IRD_NAI_RI_ORGSM_INFO"
    ORGANISM_RESOURCE = "IRD_NAI_RI_ORGSM_RESOURCE"
    PAPER = "IRD_NAI_RI_PAPER"
    PATENT = "IRD_NAI_RI_IPR"
    PROJECT = "IRD_NAI_PJT_INFO"
    QNA = "QNA"
    REPORT = "IRD_NAI_RI_RSCH_RPT"
    SOFTWARE = "IRD_NAI_RI_SW"
    STANDARD = "IRD_NAI_RI_TOT_STD"
    TECH_SUMMARY = "IRD_NAI_RI_TECH_INFO"
    VARIETY = "IRD_NAI_RI_NVR"


@dataclass
class TagSchema:
    """하나의 콘텐츠 tag를 canonical RAG 필드로 바꾸는 규칙 묶음이다.

    label, reference, title, data field 정책을 함께 보관해 mapper가 tag별 분기를 코드 전역에 흩뿌리지 않게 한다.
    """

    tag: DataTag
    label_map: Dict[str, str]
    reference_map: Dict[str, str]
    title_fields: Optional[List[str]] = None
    title_formatter: Optional[Callable[[Dict[str, Any]], str]] = None
    data_fields: List[str] = field(default_factory=lambda: ["meta_basic", "meta_detail"])

    def __post_init__(self) -> None:
        """label 키를 소문자로 정규화하고 title 설정이 짝을 맞췄는지 검증한다."""
        self.label_map = {k.lower(): v for k, v in self.label_map.items()}
        if bool(self.title_fields) != bool(self.title_formatter):
            raise ValueError("title_fields and title_formatter must be configured together")

    def get_label(self, field: str) -> Optional[str]:
        """원천 필드명에 대응하는 canonical label을 돌려준다."""
        return self.label_map.get(field.lower())

    def get_reference_fields(self) -> Dict[str, str]:
        """reference 추출 규칙을 얕은 복사로 돌려준다."""
        return self.reference_map.copy()

    def get_title_fields(self) -> List[str]:
        """title formatter가 참조하는 원천 필드 목록을 돌려준다."""
        return self.title_fields or []

    def get_data_fields(self) -> List[str]:
        """실제 본문 데이터를 읽을 수 있는 payload 필드 목록을 돌려준다."""
        return self.data_fields.copy()

    def format_title(self, data: dict) -> Optional[str]:
        """현재 schema의 title formatter로 title을 만들어 본다.

        필요한 formatter나 field 구성이 없으면 조용히 None을 돌려 mapper가 기존 title fallback을 계속 쓸 수 있게 한다.
        """
        if not self.title_formatter or not self.title_fields:
            return None

        values = {
            field: data[field]
            for field in self.title_fields
            if field in data and data[field] is not None
        }
        try:
            return self.title_formatter(values)
        except Exception:
            return None
