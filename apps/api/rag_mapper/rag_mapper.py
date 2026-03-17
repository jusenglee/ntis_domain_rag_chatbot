from typing import Dict, Any, List
from copy import deepcopy
from apps.api.rag_mapper.mapping_config import get_schema_registry
from apps.api.rag_mapper.schema_types import DataTag, TagSchema

"""원본 NTIS/QnA payload를 RAG 친화적인 평탄 구조로 바꾸는 매퍼.

핵심 규칙:
- tag별 스키마에 따라 자연어 라벨로 키를 변환한다.
- title은 가능하면 top-level로 승격해 context builder가 일관되게 사용하게 한다.
- 원본에 이미 유효한 title이 있으면 보존한다.
"""


class MappingError(Exception):
    """매핑 처리 중 발생하는 에러"""
    pass


class RagMapper:
    """도메인별 `TagSchema`를 이용해 원본 문서를 RAG 표시용 구조로 변환한다."""
    """
    RAG(Retrieval-Augmented Generation) 시스템용 데이터 매퍼
    기존 중첩 구조를 유지하면서 매칭되는 key만 자연어로 변환
    """
    # 매칭되지 않는 필드 포함 여부 설정
    # True: 매칭되지 않는 필드도 원본 key 그대로 포함
    # False: 매칭되는 필드만 포함 (매칭되지 않는 필드는 제외)
    include_unmapped_fields: bool = False

    @classmethod
    def map(cls, item: dict) -> dict:
        """단일 문서를 RAG 표준 형태로 매핑한다.

        title 보존/생성, meta_basic/meta_detail 평탄화, label 매핑이 모두 여기서 일어난다.
        회귀 위험이 큰 함수라 관련 테스트(`test_rag_mapper_title_preserve.py`)와 같이 봐야 한다.
        """
        """
        item의 기존 중첩 구조를 유지하면서 매칭되는 필드만 자연어 라벨로 변환
        title 필드는 top-level로 추출하고 meta에서 제거
        
        Args:
            item: 변환할 원본 데이터
            
        Returns:
            변환된 데이터
            
        Raises:
            MappingError: 매핑 처리 중 에러 발생
            
        Example:
            >>> item = {
            ...     "tag": "IRD_NAI_PJT_INFO",
            ...     "meta_basic": {"pjt_id": "PJT-001", "kor_pjt_nm": "프로젝트명"}
            ... }
            >>> mapped = RagMapper.map(item)
            >>> print(mapped["title"])
            프로젝트명(2023)
        """
        cls._validate_item(item)

        schema = cls._get_schema(item)
        result = deepcopy(item)
        result["tag"] = item["tag"]

        has_valid_existing_title = cls._has_valid_title(result.get("title"))

        # title 추출 및 처리
        if not has_valid_existing_title:
            cls._process_title(result, schema)

        # 데이터 필드 매핑
        cls._map_data_fields(result, schema)

        return result

    @staticmethod
    def _has_valid_title(title: Any) -> bool:
        """기존 title 값의 유효성 검증."""
        if title is None:
            return False

        if not isinstance(title, str):
            return True

        normalized = title.strip()
        if not normalized:
            return False

        return normalized.lower() != "none"

    @classmethod
    def get_researcher_info(cls, item: dict) -> List[str]:
        """
        연구원 정보를 (이름, 소속) 기준 엔트리로 분리해서 반환
        """
        researchers = item.get("prtcp_mp", [])
        results: List[str] = []

        for researcher in researchers:
            name = researcher.get("hm_nm")
            org = researcher.get("blng_org_nm") or "소속미상"

            if name:
                results.append(
                    f"- {name}({org})"
                )

        return results

    @classmethod
    def get_references(cls, item: dict) -> Dict[str, str]:
        """
        tag에 따라 reference 값을 추출
        
        Args:
            item: reference를 추출할 원본 데이터
            
        Returns:
            reference 딕셔너리 (tag, id, title 등)
            
        Raises:
            MappingError: reference 추출 중 에러 발생
            
        Example:
            >>> item = {"tag": "IRD_NAI_PJT_INFO", "meta_basic": {"pjt_id": "PJT-001"}}
            >>> refs = RagMapper.get_references(item)
            >>> print(refs["id"])
            PJT-001
        """
        cls._validate_item(item)
        schema = cls._get_schema(item)
        data = cls._extract_data(item, schema)

        result = {"tag": item["tag"]}

        # title 추가
        title = schema.format_title(data)
        if title:
            result["title"] = title

        # 다른 reference 필드 추가
        for ref_key, field_name in schema.get_reference_fields().items():
            if ref_key != "title" and field_name in data and data[field_name] is not None:
                result[ref_key] = str(data[field_name])

        return result

    @classmethod
    def _validate_item(cls, item: Any) -> None:
        """
        item 유효성 검증
        
        Args:
            item: 검증할 데이터
            
        Raises:
            MappingError: item이 유효하지 않은 경우
        """
        if not isinstance(item, dict):
            raise MappingError("item은 dict여야 합니다")

        if "tag" not in item:
            raise MappingError("item에 'tag' 필드가 필수입니다")

    @classmethod
    def _get_schema(cls, item: dict) -> TagSchema:
        """입력 tag에 맞는 스키마를 레지스트리에서 찾는다."""
        """
        item의 tag에 해당하는 스키마 조회
        
        Args:
            item: tag를 포함한 데이터
            
        Returns:
            TagSchema 인스턴스
            
        Raises:
            MappingError: 지원하지 않는 tag이거나 스키마가 없는 경우
        """
        tag_value = item["tag"]

        try:
            tag = DataTag(tag_value)
        except ValueError:
            print(f"{item}")
            raise MappingError(f"지원하지 않는 tag입니다: {tag_value}")

        registry = get_schema_registry()
        schema = registry.get(tag)

        if not schema:
            raise MappingError(f"tag에 대한 스키마가 없습니다: {tag}")

        return schema

    @classmethod
    def _extract_data(cls, item: dict, schema: TagSchema) -> dict:
        """
        item에서 데이터 필드를 찾아 반환
        
        Args:
            item: 원본 데이터
            schema: TagSchema 인스턴스
            
        Returns:
            추출된 데이터 딕셔너리
            
        Raises:
            MappingError: 데이터 필드를 찾을 수 없는 경우
        """
        data_fields = schema.get_data_fields()

        for field_name in data_fields:
            if field_name in item:
                value = item[field_name]
                if value is None:
                    return {}
                if isinstance(value, dict):
                    return value

        raise MappingError(
            f"item에 데이터 필드({data_fields} 중 하나)가 필수입니다"
        )

    @classmethod
    def _process_title(cls, result: dict, schema: TagSchema) -> None:
        """유효한 기존 title이 없을 때만 schema 규칙으로 title을 생성한다."""
        """
        title 추출 및 데이터 필드에서 제거
        
        Args:
            result: 결과 데이터 (in-place 수정)
            schema: TagSchema 인스턴스
        """
        title_fields = schema.get_title_fields()
        if not title_fields:
            return

        data_fields = schema.get_data_fields()

        # 첫 번째 데이터 필드에서 title 값 찾기
        for data_field in data_fields:
            if data_field not in result or not isinstance(result[data_field], dict):
                continue

            source_data = result[data_field]
            title = schema.format_title(source_data)

            if title:
                result["title"] = title
                # title 구성 필드들을 데이터에서 제거
                for field in title_fields:
                    source_data.pop(field, None)

            break

    @classmethod
    def _map_data_fields(cls, result: dict, schema: TagSchema) -> None:
        """schema의 label_map을 사용해 meta 계층 키를 자연어 라벨로 변환한다."""
        """
        모든 데이터 필드의 key를 자연어 라벨로 변환
        
        Args:
            result: 결과 데이터 (in-place 수정)
            schema: TagSchema 인스턴스
        """
        data_fields = schema.get_data_fields()

        for data_field in data_fields:
            if data_field in result and isinstance(result[data_field], dict):
                result[data_field] = cls._map_fields(
                    result[data_field],
                    schema.label_map,
                    include_unmapped=cls.include_unmapped_fields
                )

    @staticmethod
    def _map_fields(data: dict, label_map: dict, include_unmapped: bool = None) -> dict:
        """
        data의 key를 label_map의 자연어로 변환
        
        Args:
            data: 변환할 데이터
            label_map: 필드명 -> 라벨 매핑
            include_unmapped: 매칭되지 않는 필드 포함 여부
                             None이면 클래스 설정값(include_unmapped_fields) 사용
            
        Returns:
            변환된 데이터
        """
        # include_unmapped가 명시되지 않으면 클래스 설정값 사용
        if include_unmapped is None:
            include_unmapped = RagMapper.include_unmapped_fields

        result = {}

        for raw_key, raw_value in data.items():
            label_key = label_map.get(raw_key.lower())

            if label_key is not None:
                # 매칭되는 필드: 자연어 라벨로 변환
                result[label_key] = raw_value
            elif include_unmapped:
                # 매칭되지 않는 필드: 원본 key 유지 (설정에 따라)
                result[raw_key] = raw_value
            # else: 매칭되지 않는 필드 제외

        return result
