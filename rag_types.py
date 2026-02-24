from dataclasses import dataclass
from typing import Any, Dict, List, Optional

@dataclass
class RagResult:
    stack: str
    keywords: List[str]
    hits: Any
    reranked_hits: Any
    context: str
    refs: List[str]
    timings: Dict[str, float]
    aggregation: Optional[Dict[str, Any]] = None
    llm_answer: Optional[str] = None
