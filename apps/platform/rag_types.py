from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from apps.evidence.citation_registry import CitationRegistry
    from apps.evidence.source_reference import SourceReference


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
    series: Optional[Dict[str, Any]] = None
    reverse_trace: Optional[Dict[str, Any]] = None
    pattern_analysis: Optional[Dict[str, Any]] = None
    multi_hop_bundle: Optional[Dict[str, Any]] = None
    llm_answer: Optional[str] = None
    debug_meta: Optional[Dict[str, Any]] = None
    canonical_evidence: Optional[List[Dict[str, Any]]] = None
    render_profile: Optional[Dict[str, Any]] = None
    prompt_units: Optional[List[Dict[str, Any]]] = None
    used_tokens: int = 0
    kept_ctx: int = 0
    discarded_ctx: int = 0
    dropped_by_floor: int = 0
    dropped_by_budget: int = 0
    compressed_count: int = 0
    lineages: Optional[List[Dict[str, Any]]] = None
    anchor_hit: bool = False
    followup_resolved_by_facts: bool = False
    # SSOT: SourceReference 리스트 + CitationRegistry. Stage 3에서 채워짐.
    source_refs: Optional[List["SourceReference"]] = None
    citation_registry: Optional["CitationRegistry"] = None
