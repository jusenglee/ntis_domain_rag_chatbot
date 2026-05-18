"""Exact by-id 조회.

pjt_id / pjt_no / rst_id / person_no / org_id 를 Qdrant payload 필터로 직접 매칭한다.
SearchAgent의 strategy=exact_lookup 분기에서 호출된다.

이 모듈은 hybrid 검색 / 스코어 정렬 / drift 처리 등을 일절 하지 않는다.
한 가지 책임: 주어진 axis의 값으로 컬렉션에서 매칭되는 모든 point를 가져온다.
"""

from __future__ import annotations

from typing import Any, List, Sequence

from loguru import logger
from qdrant_client.models import FieldCondition, Filter, MatchAny


def lookup_by_axis(
    *,
    qdrant_client: Any,
    collection: str,
    axis: str,
    values: Sequence[str],
    limit: int = 50,
) -> List[Any]:
    """주어진 axis(pjt_id 등)의 value 집합으로 컬렉션에서 일치하는 모든 point를 돌려준다.

    Args:
        qdrant_client: QdrantClient 인스턴스
        collection: ntis_project_v1 / ntis_perf_v1
        axis: pjt_id / pjt_no / rst_id / person_no / org_id
        values: 매칭할 값 리스트 (OR 매치)
        limit: 최대 반환 수

    Returns:
        Qdrant scored_points (또는 records) 리스트. 매칭이 없으면 빈 리스트.
    """

    cleaned = [str(v).strip() for v in (values or []) if str(v).strip()]
    if not cleaned:
        return []

    # axis → payload field 매핑.
    # NTIS payload는 meta_basic / meta_detail / top-level에 식별자를 가지므로 OR로 시도한다.
    payload_paths = _payload_paths_for_axis(axis)
    if not payload_paths:
        logger.warning(f"[exact_lookup] unknown axis: {axis}")
        return []

    should_conditions = [
        FieldCondition(key=path, match=MatchAny(any=cleaned))
        for path in payload_paths
    ]

    # OR 매칭: should는 기본적으로 "at least 1 match"
    qdrant_filter = Filter(should=should_conditions)

    try:
        points, _ = qdrant_client.scroll(
            collection_name=collection,
            scroll_filter=qdrant_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
    except Exception as exc:
        logger.warning(f"[exact_lookup] qdrant scroll failed: collection={collection} axis={axis} err={exc}")
        return []

    return list(points or [])


def _payload_paths_for_axis(axis: str) -> List[str]:
    """식별자 축이 payload의 어느 경로에 보존되어 있는지 반환.

    NTIS schema는 동일 식별자를 (a) top-level, (b) meta_basic, (c) meta_detail에 둘 수 있어
    모두 시도한다. exact_lookup의 false negative를 줄이는 비용으로 OR-match 비용을 감수.
    """
    axis = (axis or "").strip().lower()
    if axis == "pjt_id":
        return ["pjt_id", "meta_basic.pjt_id", "meta_detail.pjt_id"]
    if axis == "pjt_no":
        return ["pjt_no", "meta_basic.pjt_no", "meta_detail.pjt_no"]
    if axis == "rst_id":
        return ["rst_id", "meta_basic.rst_id", "meta_detail.rst_id"]
    if axis == "person_no":
        return [
            "person_no",
            "hm_id",
            "meta_basic.person_no",
            "meta_detail.person_no",
            "prtcp_mp.person_no",
            "prtcp_mp.hm_id",
        ]
    if axis == "org_id":
        return ["org_id", "meta_basic.org_id", "meta_detail.org_id"]
    return []
