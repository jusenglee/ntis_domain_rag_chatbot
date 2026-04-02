from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence


def hydrate_points_payload(
    qdr: Any,
    points: Sequence[Any],
    *,
    normalize_tag_value: Callable[[Any], str],
    project_tags_norm: set[str],
    perf_tags_norm: set[str],
    col_project: str,
    col_perf: str,
    tag_pjt_info: str,
    tag_pjt_mp: str,
    tag_pjt_org: str,
    rag_collection_allowlist: Sequence[str],
    chunk_size: int = 128,
) -> None:
    """검색 결과 point의 얕은 payload를 원본 payload로 다시 수화한다.

    재조회한 payload로 교체하되, score나 내부 추적용 _final_* 메타는 보존해서
    후속 정렬과 로그가 hydration 이후에도 동일한 디버그 정보를 유지하게 만든다.
    """
    internal_keys = {
        "_collection", "_rrf",
        "_raw_rrf", "_raw_kw", "_raw_f", "_raw_family", "_raw_tag",
        "_final_rrf", "_final_kw", "_final_f", "_final_family", "_final_tag",
        "_final_total", "_legacy_total",
    }
    if not points:
        return

    def _get(point: Any, key: str, default: Any = None) -> Any:
        """point나 record 객체에서 속성을 안전하게 읽는다."""
        return getattr(point, key, default)

    def _set(point: Any, key: str, value: Any) -> None:
        """point 객체에 수화된 payload를 다시 주입한다."""
        setattr(point, key, value)

    def _infer_collection_from_payload(payload: dict) -> Optional[str]:
        """payload의 tag 정보를 보고 어느 컬렉션에서 다시 읽어야 하는지 추정한다.

        project/perf 태그 집합만 사용해 판정하므로, 원천 의미를 바꾸지 않고 hydration 대상 컬렉션만 좁힌다.
        """
        if not isinstance(payload, dict):
            return None
        candidate_tags = []
        tag_value = payload.get("tag")
        if tag_value:
            candidate_tags.append(tag_value)
        tags_value = payload.get("tags")
        if isinstance(tags_value, list):
            candidate_tags.extend([tag for tag in tags_value if tag])
        for tag in candidate_tags:
            norm = normalize_tag_value(tag)
            if norm in project_tags_norm:
                return col_project
            if norm in perf_tags_norm:
                return col_perf
        return None

    def _fallback_collection() -> Optional[str]:
        """허용 컬렉션이 하나뿐일 때만 마지막 fallback 컬렉션을 반환한다.

        복수 allowlist에서 임의 선택을 하지 않아 잘못된 컬렉션 hydration을 막는다.
        """
        allow_list = list(rag_collection_allowlist)
        if len(allow_list) == 1:
            return allow_list[0]
        return None

    def _normalize_project_tag(payload: dict) -> None:
        """project payload 안의 세부 project tag를 대표 tag로 정규화한다.

        mapper와 renderer가 project 계열을 한 태그로 보게 하려는 보정이며, 다른 컬렉션 의미까지 합치지는 않는다.
        """
        if not isinstance(payload, dict):
            return
        tag_value = payload.get("tag")
        if tag_value in {tag_pjt_mp, tag_pjt_org}:
            payload["tag"] = tag_pjt_info
        tags_value = payload.get("tags")
        if isinstance(tags_value, list):
            payload["tags"] = [tag_pjt_info if tag in {tag_pjt_mp, tag_pjt_org} else tag for tag in tags_value]

    buckets: Dict[str, List[Any]] = {}
    for point in points:
        payload = _get(point, "payload", {}) or {}
        collection = None
        if isinstance(payload, dict):
            collection = payload.get("_collection")
        if not collection:
            collection = _get(point, "_collection", None)
        if not collection:
            collection = _infer_collection_from_payload(payload)
        if not collection:
            collection = _fallback_collection()
        if not collection:
            continue
        buckets.setdefault(collection, []).append(point)

    for collection_name, point_list in buckets.items():
        step = max(1, int(chunk_size))
        for index in range(0, len(point_list), step):
            chunk = point_list[index : index + step]
            ids = [_get(point, "id") for point in chunk if _get(point, "id", None) is not None]
            if not ids:
                continue
            records = qdr.retrieve(
                collection_name=collection_name,
                ids=ids,
                with_payload=True,
                with_vectors=False,
            )
            record_payload: Dict[str, dict] = {}
            for record in records or []:
                record_id = _get(record, "id", None)
                if record_id is None:
                    continue
                record_payload[str(record_id)] = _get(record, "payload", {}) or {}

            for point in chunk:
                point_id = _get(point, "id", None)
                if point_id is None:
                    continue
                key = str(point_id)
                if key not in record_payload:
                    continue
                prev_payload = _get(point, "payload", {}) or {}
                if not isinstance(prev_payload, dict):
                    prev_payload = {}
                preserved_internal = {
                    key: value
                    for key, value in prev_payload.items()
                    if isinstance(key, str) and (key in internal_keys or key.startswith("_final_"))
                }
                hydrated_payload = record_payload[key]
                if not isinstance(hydrated_payload, dict):
                    hydrated_payload = {}
                _normalize_project_tag(hydrated_payload)
                hydrated_payload.update(preserved_internal)
                _set(point, "payload", hydrated_payload)
