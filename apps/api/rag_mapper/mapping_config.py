from typing import Dict, Optional

from apps.api.rag_mapper.schema_types import DataTag, TagSchema

"""Central registry for RAG mapper tag schemas."""


class SchemaRegistry:
    """RAG mapper용 TagSchema를 lazy 초기화하고 조회하는 중앙 registry다."""

    def __init__(self) -> None:
        """비어 있는 schema 저장소와 초기화 플래그를 준비한다."""
        self._schemas: Dict[DataTag, TagSchema] = {}
        self._initialized = False

    def register(self, schema: TagSchema) -> None:
        """tag별 schema를 registry에 등록한다."""
        self._schemas[schema.tag] = schema

    def get(self, tag: DataTag) -> Optional[TagSchema]:
        """주어진 DataTag에 해당하는 schema를 돌려준다."""
        return self._schemas.get(tag)

    def initialize(self) -> None:
        """domain별 schema 팩토리를 한 번만 불러 registry를 채운다."""
        if self._initialized:
            return

        from apps.api.rag_mapper.domains.compound import get_compound_schema
        from apps.api.rag_mapper.domains.equipment import get_equipment_schema
        from apps.api.rag_mapper.domains.manual import get_manual_schema
        from apps.api.rag_mapper.domains.organism_info import get_organism_info_schema
        from apps.api.rag_mapper.domains.organism_resource import get_organism_resource_schema
        from apps.api.rag_mapper.domains.paper import get_paper_schema
        from apps.api.rag_mapper.domains.patent import get_patent_schema
        from apps.api.rag_mapper.domains.project import get_project_schema
        from apps.api.rag_mapper.domains.qna import get_qna_schema
        from apps.api.rag_mapper.domains.report import get_report_schema
        from apps.api.rag_mapper.domains.software import get_software_schema
        from apps.api.rag_mapper.domains.standard import get_standard_schema
        from apps.api.rag_mapper.domains.tech_summary import get_tech_summary_schema
        from apps.api.rag_mapper.domains.variety import get_variety_schema

        self.register(get_compound_schema())
        self.register(get_equipment_schema())
        self.register(get_manual_schema())
        self.register(get_organism_info_schema())
        self.register(get_organism_resource_schema())
        self.register(get_paper_schema())
        self.register(get_patent_schema())
        self.register(get_project_schema())
        self.register(get_qna_schema())
        self.register(get_report_schema())
        self.register(get_software_schema())
        self.register(get_standard_schema())
        self.register(get_tech_summary_schema())
        self.register(get_variety_schema())
        self._initialized = True

    def is_initialized(self) -> bool:
        """registry가 이미 초기화되었는지 알려준다."""
        return self._initialized


_registry = SchemaRegistry()
_registry.initialize()


def get_schema_registry() -> SchemaRegistry:
    """프로세스 전역에서 공유하는 schema registry를 돌려준다."""
    return _registry


# 기존 호출부 호환을 위한 direct access입니다.
SCHEMA_REGISTRY = _registry._schemas
