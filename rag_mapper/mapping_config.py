from dataclasses import dataclass, field
from typing import Dict, Optional, List, Callable, Any
from enum import Enum


class DataTag(str, Enum):
    """데이터 태그 정의 (콘텐츠 타입)"""
    PROJECT = "IRD_NAI_PJT_INFO"
    RESEARCHER = "IRD_NAI_PJT_MP"


@dataclass
class TagSchema:
    """
    각 tag별 스키마를 통합 관리
    
    Attributes:
        tag: 데이터 태그 (콘텐츠 타입 식별자)
        label_map: 필드명 -> 자연어 라벨 매핑
        reference_map: reference 추출 시 사용할 필드 매핑
        title_fields: title을 구성할 필드명 리스트
        title_formatter: title 생성 함수
        data_fields: 데이터가 위치한 필드명 리스트 (기본값: ["meta_basic", "meta_detail"])
    """
    tag: DataTag
    label_map: Dict[str, str]
    reference_map: Dict[str, str]
    title_fields: Optional[List[str]] = None
    title_formatter: Optional[Callable[[Dict[str, Any]], str]] = None
    data_fields: List[str] = field(default_factory=lambda: ["meta_basic", "meta_detail"])
    
    def __post_init__(self):
        """스키마 유효성 검증"""
        # label_map의 모든 key를 소문자로 정규화
        self.label_map = {k.lower(): v for k, v in self.label_map.items()}
        
        # title_fields와 title_formatter는 함께 설정되어야 함
        if bool(self.title_fields) != bool(self.title_formatter):
            raise ValueError(
                "title_fields와 title_formatter는 함께 설정되어야 합니다"
            )
    
    def get_label(self, field: str) -> Optional[str]:
        """
        필드의 자연어 라벨 조회
        
        Args:
            field: 조회할 필드명
            
        Returns:
            자연어 라벨 또는 None
        """
        return self.label_map.get(field.lower())
    
    def get_reference_fields(self) -> Dict[str, str]:
        """
        reference 맵 조회
        
        Returns:
            reference 필드 매핑 딕셔너리
        """
        return self.reference_map.copy()
    
    def get_title_fields(self) -> List[str]:
        """
        title을 구성할 필드명들 조회
        
        Returns:
            title 구성 필드 리스트
        """
        return self.title_fields or []
    
    def get_data_fields(self) -> List[str]:
        """
        데이터가 위치한 필드명 리스트 조회
        
        Returns:
            데이터 필드명 리스트
        """
        return self.data_fields.copy()
    
    def format_title(self, data: dict) -> Optional[str]:
        """
        title을 포맷하여 반환
        
        Args:
            data: title 생성에 필요한 데이터
            
        Returns:
            포맷된 title 또는 None
        """
        if not self.title_formatter or not self.title_fields:
            return None
        
        # 필요한 필드들을 data에서 추출
        values = {
            field: data[field]
            for field in self.title_fields
            if field in data and data[field] is not None
        }
        
        try:
            return self.title_formatter(values)
        except Exception:
            # 포맷팅 중 에러 발생 시 None 반환
            return None


class SchemaRegistry:
    """스키마 레지스트리 관리 클래스"""
    
    def __init__(self):
        self._schemas: Dict[DataTag, TagSchema] = {}
        self._initialized = False
    
    def register(self, schema: TagSchema) -> None:
        """
        스키마 등록
        
        Args:
            schema: 등록할 TagSchema
        """
        self._schemas[schema.tag] = schema
    
    def get(self, tag: DataTag) -> Optional[TagSchema]:
        """
        tag에 해당하는 스키마 조회
        
        Args:
            tag: 조회할 DataTag
            
        Returns:
            TagSchema 또는 None
        """
        return self._schemas.get(tag)
    
    def initialize(self) -> None:
        """도메인별 스키마 초기화"""
        if self._initialized:
            return
        
        from rag_mapper.domains.project import get_project_schema
        from rag_mapper.domains.researcher import get_researcher_schema
        
        self.register(get_project_schema())
        self.register(get_researcher_schema())
        
        self._initialized = True
    
    def is_initialized(self) -> bool:
        """초기화 여부 확인"""
        return self._initialized


# 전역 레지스트리 인스턴스
_registry = SchemaRegistry()
_registry.initialize()


def get_schema_registry() -> SchemaRegistry:
    """
    스키마 레지스트리 인스턴스 반환
    
    Returns:
        SchemaRegistry 인스턴스
    """
    return _registry


# 하위 호환성을 위한 SCHEMA_REGISTRY
SCHEMA_REGISTRY = _registry._schemas