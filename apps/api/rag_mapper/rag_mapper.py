"""원천 NTIS/QnA payload를 tag별 schema 규칙에 맞는 RAG 친화 구조로 바꾸는 mapper다.

원천 의미를 재해석하지 않고, title 추출과 field label 변환만 수행해
후속 context builder가 예측 가능한 canonical shape를 받게 만든다.
"""

import logging
from typing import Dict, Any, List
from copy import deepcopy
from apps.api.rag_mapper.mapping_config import get_schema_registry
from apps.api.rag_mapper.schema_types import DataTag, TagSchema


logger = logging.getLogger(__name__)



class MappingError(Exception):
    """원천 payload를 RAG mapper 규칙으로 변환하는 과정에서 나는 오류다."""
    pass


class RagMapper:
    """원천 NTIS/QnA payload를 tag별 TagSchema에 따라 canonical RAG 필드로 바꾼다.

    title을 top-level로 끌어올리고 data field key를 label로 바꾸되, 원천 의미를 임의 보정하지 않고 schema 규칙만 적용한다.
    """
    # 매칭되지 않는 필드 포함 여부 설정
    # True: 매칭되지 않는 필드도 원본 key 그대로 포함
    # False: 매칭되는 필드만 포함 (매칭되지 않는 필드는 제외)
    include_unmapped_fields: bool = False

    @classmethod
    def map(cls, item: dict) -> dict:
        """원천 item 하나를 RAG mapper 규칙에 맞는 새 dict로 변환한다.

        기존 title이 유효하면 유지하고, 없으면 schema 기반 title을 추출한 뒤 data field key를 canonical label로 바꾼다.
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
        """기존 title 값이 fallback 없이 그대로 써도 되는지 판정한다."""
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
        """참여연구자 목록을 사람이 읽기 쉬운 이름(소속) 문자열 목록으로 정리한다."""
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
        """item에서 외부 링크나 식별에 필요한 reference 필드만 추려 돌려준다."""
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
        """mapper가 처리할 최소 입력 계약을 검사한다.

        현재는 dict 여부와 tag 존재만 강제해, 이후 schema 조회가 전제 없이 동작하게 만든다.
        """
        if not isinstance(item, dict):
            raise MappingError("item은 dict여야 합니다")

        if "tag" not in item:
            raise MappingError("item에 'tag' 필드가 필수입니다")

    @classmethod
    def _get_schema(cls, item: dict) -> TagSchema:
        """item의 tag에 해당하는 TagSchema를 registry에서 찾는다.

        지원하지 않는 tag이거나 schema가 등록되지 않았으면 MappingError를 던져 mapper 범위를 명확히 한다.
        """
        tag_value = item["tag"]

        try:
            tag = DataTag(tag_value)
        except ValueError:
            logger.warning("unsupported tag received", extra={"tag": tag_value, "keys": sorted(item.keys())})
            raise MappingError(f"지원하지 않는 tag입니다: {tag_value}")

        registry = get_schema_registry()
        schema = registry.get(tag)

        if not schema:
            raise MappingError(f"tag에 대한 스키마가 없습니다: {tag}")

        return schema

    @classmethod
    def _extract_data(cls, item: dict, schema: TagSchema) -> dict:
        """schema가 지정한 data field들 중 실제 payload 본문으로 쓸 dict를 찾는다."""
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
        """schema formatter로 title을 만들고, title을 구성한 원천 필드는 data field에서 제거한다.

        이렇게 해야 title이 top-level과 본문에 중복 노출되지 않고 context builder가 일관된 위치에서 제목을 읽을 수 있다.
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
        """schema가 가리키는 각 data field의 key를 canonical label로 변환한다."""
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
        # include_unmapped가 명시되지 않으면 클래스 설정값 사용
        """원천 dict의 key를 label_map 기준 canonical 이름으로 바꾼다.

        include_unmapped 옵션이 켜진 경우에만 미매핑 필드를 남겨, mapper 출력 폭을 호출자가 제어하게 한다.
        """
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
