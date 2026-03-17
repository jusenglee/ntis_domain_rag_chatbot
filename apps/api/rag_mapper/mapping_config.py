from typing import Dict, Optional

from apps.api.rag_mapper.schema_types import DataTag, TagSchema

"""Central registry for RAG mapper tag schemas."""


class SchemaRegistry:
    """Registers and resolves tag schemas lazily."""

    def __init__(self) -> None:
        self._schemas: Dict[DataTag, TagSchema] = {}
        self._initialized = False

    def register(self, schema: TagSchema) -> None:
        self._schemas[schema.tag] = schema

    def get(self, tag: DataTag) -> Optional[TagSchema]:
        return self._schemas.get(tag)

    def initialize(self) -> None:
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
        return self._initialized


_registry = SchemaRegistry()
_registry.initialize()


def get_schema_registry() -> SchemaRegistry:
    return _registry


# Backward-compatible direct access for legacy callers.
SCHEMA_REGISTRY = _registry._schemas
