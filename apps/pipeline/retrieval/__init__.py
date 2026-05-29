"""SearchAgent가 사용하는 Qdrant retrieval primitives.

이 서브패키지는 다음 세 가지만 담당한다:
    - exact_lookup: pjt_id/pjt_no/rst_id/person_no by-id 조회
    - qdrant_search: hybrid (dense + lexical) 검색
    - canonical_normalizer: Qdrant point → CanonicalEvidence 변환

기존 apps.retrieval.* 의 RAG 오케스트레이션, drift 감지, filter policy fallback 등은
모두 폐기되었다. SearchAgent는 본 서브패키지의 함수만 호출한다.
"""

from apps.pipeline.retrieval.canonical_normalizer import normalize_qdrant_points
from apps.pipeline.retrieval.exact_lookup import lookup_by_axis
from apps.pipeline.retrieval.qdrant_search import hybrid_search

__all__ = ["hybrid_search", "lookup_by_axis", "normalize_qdrant_points"]
