"""Hybrid (dense + sparse) Qdrant 검색.

SearchAgent의 strategy=hybrid_search / subject_anchor / detail_anchor 분기가 사용한다.
기존 apps.retrieval.retrieval의 dense_retrieve_hybrid_multi 류 함수를 대신하며,
drift 감지/raw_query fallback 같은 분기를 일절 갖지 않는다.

NTIS 운영 컬렉션은 named vector를 사용한다 (예: ``dense``). 본 모듈은 부팅 시 컬렉션의
실제 vector 이름을 자동 검출해 캐시한 뒤 `Prefetch.using`에 전달한다. 이전 구현이 default
unnamed vector로 호출해 ``Wrong input: Not existing vector name error`` 가 나는 회귀를 방지.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from loguru import logger
from qdrant_client.models import (
    FieldCondition,
    Filter,
    FusionQuery,
    MatchAny,
    Prefetch,
    Range,
)

from apps.pipeline.contracts import FilterBundle, IdentifierBundle, SubjectAnchor


# 컬렉션별 dense vector 이름 캐시 (None = unnamed default)
_DENSE_VECTOR_NAME_CACHE: dict[str, Optional[str]] = {}


def hybrid_search(
    *,
    qdrant_client: Any,
    embed_model: Any,
    collection: str,
    query: str,
    filters: FilterBundle,
    subject: Optional[SubjectAnchor] = None,
    identifiers: Optional[IdentifierBundle] = None,
    limit: int = 20,
) -> List[Any]:
    """단일 컬렉션에 대해 hybrid 검색을 수행해 scored points를 반환.

    Args:
        qdrant_client: QdrantClient
        embed_model: dense embedding을 생산하는 모델 (HuggingFaceEmbedding 등)
        collection: ntis_project_v1 / ntis_perf_v1
        query: 자연어 query (텍스트 보조 신호)
        filters: 구조화 필터
        subject: 사람/기관 anchor (있으면 nested payload 매칭에 사용)
        identifiers: 식별자 후보 (있으면 hybrid의 후처리 rank booster 또는 OR 매치)
        limit: 결과 상한

    Returns:
        ScoredPoint 리스트. 매칭이 없으면 빈 리스트.
    """

    query_text = (query or "").strip()
    if not query_text and not (subject or (filters and filters.has_any())):
        return []

    qdrant_filter = _build_qdrant_filter(
        filters=filters,
        subject=subject,
        identifiers=identifiers,
        collection=collection,
    )

    try:
        dense_vec = embed_model.get_query_embedding(query_text or _fallback_query_text(subject, filters))
    except Exception as exc:
        logger.warning(f"[hybrid_search] embedding failed: collection={collection} err={exc}")
        return []

    dense_vector_name = _resolve_dense_vector_name(qdrant_client, collection)
    prefetch = Prefetch(query=dense_vec, limit=limit * 3, using=dense_vector_name) if dense_vector_name else Prefetch(query=dense_vec, limit=limit * 3)

    try:
        result = qdrant_client.query_points(
            collection_name=collection,
            prefetch=[prefetch],
            query=FusionQuery(fusion="rrf"),
            query_filter=qdrant_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
            timeout=60,
        )
    except Exception as exc:
        logger.warning(f"[hybrid_search] query_points failed: collection={collection} err={exc}")
        return []

    points = list(getattr(result, "points", []) or [])
    return points


# ---------------------------------------------------------------------------
# Vector name discovery
# ---------------------------------------------------------------------------

def _resolve_dense_vector_name(qdrant_client: Any, collection: str) -> Optional[str]:
    """컬렉션의 named dense vector 이름을 한 번만 조회해 캐시.

    Returns:
        named vector 이름 (예: "dense", "e5", "default") 또는
        unnamed default vector 사용 시 None.
    """
    if collection in _DENSE_VECTOR_NAME_CACHE:
        return _DENSE_VECTOR_NAME_CACHE[collection]

    name: Optional[str] = None
    try:
        info = qdrant_client.get_collection(collection_name=collection)
        vectors_cfg = getattr(getattr(getattr(info, "config", None), "params", None), "vectors", None)
        # vectors_cfg는 두 형태:
        #   (a) VectorParams 단일 (unnamed default) → name = None
        #   (b) Dict[str, VectorParams] (named vectors) → 첫 키
        if vectors_cfg is None:
            name = None
        elif hasattr(vectors_cfg, "size"):
            # 단일 VectorParams 객체 (size 속성 존재)
            name = None
        elif isinstance(vectors_cfg, dict):
            keys = list(vectors_cfg.keys())
            if not keys:
                name = None
            elif len(keys) == 1:
                name = keys[0]
            else:
                # 여러 named vector가 있으면 우선순위: dense > default > 첫 키
                preferred = ("dense", "default", "e5", "e5i", "embedding")
                chosen = next((k for k in preferred if k in keys), keys[0])
                name = chosen
        else:
            name = None
    except Exception as exc:
        logger.warning(
            f"[hybrid_search] vector name discovery failed: collection={collection} err={exc}; "
            "falling back to unnamed default vector"
        )
        name = None

    _DENSE_VECTOR_NAME_CACHE[collection] = name
    logger.info(f"[hybrid_search] resolved dense vector name: collection={collection} name={name!r}")
    return name


# ---------------------------------------------------------------------------
# Filter builder
# ---------------------------------------------------------------------------

def _build_qdrant_filter(
    *,
    filters: FilterBundle,
    subject: Optional[SubjectAnchor],
    identifiers: Optional[IdentifierBundle],
    collection: str,
) -> Optional[Filter]:
    """FilterBundle/Subject/Identifier를 Qdrant payload filter로 변환.

    원칙: 모든 매칭은 must 결합. 동일 축 내 여러 값은 MatchAny.
    "name_must" 같은 유효하지 않은 정책 이름을 silent fallback 하지 않는다.
    """

    must: List[Any] = []
    should: List[Any] = []
    must_not: List[Any] = []

    # 1. 식별자 anchor (강제)
    if identifiers and identifiers.has_any():
        for axis_name in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
            values = getattr(identifiers, axis_name)
            if not values:
                continue
            must.append(_match_any_across_paths(_payload_paths_for_axis(axis_name), values))

    # 2. Subject anchor
    #
    # NTIS payload는 모든 데이터에 참여연구자/참여기관 정보를 보존한다. 효율적인 필드:
    #   - prtcp_mp_hm_nm_list: top-level array of 참여연구자 이름 (예: ["신동구", "이지철", ...])
    #     → 이 필드는 평탄화되어 있어 keyword index를 걸 수 있는 1순위 후보
    #   - prtcp_mp[].hm_id: nested array of person_no (예: "ntis:B551186-...HMO.xxxx")
    #   - prtcp_mp[].blng_org_nm: nested array of 참여연구자 소속 기관
    #   - org_nm (top-level): 수행기관 (lead org)
    #   - prtcp_org[].org_nm: nested array of 참여기관
    #
    # 분기:
    #   - primary_id(person_no/org_id) 있음 → 정확 매칭 must
    #   - 이름 + 소속 있음 → 두 조건 must (top-level 평탄화 필드 우선 사용)
    #   - 이름만 있음 → prtcp_mp_hm_nm_list (top-level array) must (평탄화이므로 비교적 빠름)
    if subject is not None:
        if subject.kind == "people":
            pid = subject.primary_id()
            if pid:
                must.append(_match_any_across_paths(_payload_paths_for_axis("person_no"), [pid]))
            else:
                # 이름은 top-level 평탄화 array `prtcp_mp_hm_nm_list`로 매칭한다.
                # nested `prtcp_mp.hm_nm`은 인덱스가 없으면 매우 느리므로 보조용.
                must.append(
                    _match_any_across_paths(
                        ["prtcp_mp_hm_nm_list", "prtcp_mp.hm_nm"],
                        [subject.display_name],
                    )
                )
                if subject.affiliation_org_name:
                    # 소속은 top-level `org_nm`(수행기관) 또는 prtcp_mp.blng_org_nm(참여연구자 소속)
                    # 또는 prtcp_org.org_nm(참여기관) 중 하나에서 매칭되면 된다.
                    must.append(
                        _match_any_across_paths(
                            [
                                "org_nm",
                                "prtcp_mp.blng_org_nm",
                                "prtcp_org.org_nm",
                                "meta_basic.pjt_prfrm_org_nm",
                            ],
                            [subject.affiliation_org_name],
                        )
                    )
        elif subject.kind == "org":
            oid = subject.primary_id()
            if oid:
                must.append(_match_any_across_paths(_payload_paths_for_axis("org_id"), [oid]))
            else:
                # 기관 이름 매칭: 수행기관(org_nm) 또는 참여기관(prtcp_org.org_nm)
                must.append(
                    _match_any_across_paths(
                        ["org_nm", "prtcp_org.org_nm", "meta_basic.pjt_prfrm_org_nm"],
                        [subject.display_name],
                    )
                )

    # 3. FilterBundle
    if filters.lead_org_name:
        must.append(_match_any_across_paths(["lead_org_name", "org_nm", "meta_basic.org_nm"], filters.lead_org_name))
    if filters.participant_org_name:
        must.append(
            _match_any_across_paths(
                ["prtcp_org.org_nm", "participant_org_name", "meta_basic.participant_org_name"],
                filters.participant_org_name,
            )
        )
    if filters.perf_type:
        must.append(_match_any_across_paths(["perf_type", "meta_basic.perf_type", "tag"], filters.perf_type))
    if filters.year_from or filters.year_to:
        # NTIS payload는 stan_yr / start_dt 등 다양한 시점 필드를 보유. 가장 보편적인 stan_yr 기준.
        gte = filters.year_from
        lte = filters.year_to
        must.append(
            FieldCondition(
                key="stan_yr",
                range=Range(gte=gte, lte=lte),
            )
        )

    if not (must or should or must_not):
        return None
    return Filter(
        must=must or None,
        should=should or None,
        must_not=must_not or None,
    )


def _match_any_across_paths(paths: Sequence[str], values: Sequence[str]) -> Filter:
    """동일한 값을 여러 payload path 중 하나에서 매칭하는 OR 필터.

    qdrant-client의 Filter.should는 기본적으로 "at least 1 match"로 동작한다.
    """
    cleaned = [str(v).strip() for v in values if str(v).strip()]
    return Filter(
        should=[FieldCondition(key=path, match=MatchAny(any=cleaned)) for path in paths],
    )


def _payload_paths_for_axis(axis: str) -> List[str]:
    """exact_lookup과 동일한 path 셋을 공유한다.

    실제 NTIS payload 구조 기준:
      - pjt_id : top-level + meta_basic 양쪽에 존재
      - pjt_no : top-level + meta_basic 양쪽에 존재
      - rst_id : top-level + meta_basic 양쪽에 존재
      - person_no(hm_id) : nested prtcp_mp[].hm_id 만 존재 (top-level person_no 없음)
      - org_id          : top-level은 없고 prtcp_org[].org_cd가 기관 코드.
                          (별도 org_id 필드는 거의 비어 있다)
    """
    axis = axis.lower()
    if axis == "pjt_id":
        return ["pjt_id", "meta_basic.pjt_id"]
    if axis == "pjt_no":
        return ["pjt_no", "meta_basic.pjt_no"]
    if axis == "rst_id":
        return ["rst_id", "doc_id", "meta_basic.rst_id"]
    if axis == "person_no":
        # 실제 payload에서 person_no는 prtcp_mp[].hm_id로만 존재. nested key 형식.
        return ["prtcp_mp.hm_id"]
    if axis == "org_id":
        # org_id는 거의 비어있고 실질적으로 org_cd가 식별자 역할
        return ["prtcp_org.org_cd"]
    return []


def _fallback_query_text(subject: Optional[SubjectAnchor], filters: FilterBundle) -> str:
    """query가 비었지만 anchor가 있을 때 임베딩에 넘길 보조 텍스트."""
    parts: List[str] = []
    if subject:
        parts.append(subject.display_name)
        if subject.affiliation_org_name:
            parts.append(subject.affiliation_org_name)
    parts.extend(filters.lead_org_name)
    parts.extend(filters.domain_keywords)
    return " ".join(parts) or " "
