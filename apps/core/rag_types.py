from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class RagResult:
    """`RagResult`는 현재 모듈의 책임을 표현하는 타입 또는 헬퍼 클래스입니다.

책임:
- 현재 레이어는 planner/contract/runtime 경계를 넘어 의미를 임의 보정하지 않고, 필요한 검증과 조립만 수행해야 합니다.\n    """
    stack: str
    keywords: List[str]
    hits: Any
    reranked_hits: Any
    context: str
    refs: List[str]
    timings: Dict[str, float]
    aggregation: Optional[Dict[str, Any]] = None
    llm_answer: Optional[str] = None
    debug_meta: Optional[Dict[str, Any]] = None
    canonical_evidence: Optional[List[Dict[str, Any]]] = None
    render_profile: Optional[Dict[str, Any]] = None
