"""Phase 1 — Tool catalog + Tool 추상.

NTIS Agentic RAG 챗봇의 자율 agentic loop(Phase 3 도입)에서 PlannerAgent가 호출할 도구들을
표준화된 계약(ToolSpec/ToolCall/Observation)으로 노출한다.

각 도구는:
    - `ToolSpec`: 정적 메타 (이름·설명·input schema·cost hint·preconditions)
    - handler: 비동기 함수 `async (args: Dict, ctx: ToolContext) -> Dict`

ToolExecutor가 ToolCall을 받아 적절한 handler를 dispatch하고, 결과/예외를 Observation으로 감싼다.

Phase 1 범위: contracts + 6개 핵심 도구 wrap. cyclic graph·Planner는 Phase 2/3.
"""

from __future__ import annotations

from apps.pipeline.tools.contracts import (
    Observation,
    ToolCall,
    ToolContext,
    ToolEntry,
    ToolExecutor,
    ToolSpec,
)

__all__ = [
    "Observation",
    "ToolCall",
    "ToolContext",
    "ToolEntry",
    "ToolExecutor",
    "ToolSpec",
]
