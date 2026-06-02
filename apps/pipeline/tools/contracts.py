"""Phase 1 — Tool 계약 모델.

PlannerAgent ↔ ToolExecutor 사이의 표준화된 계약을 정의한다.

설계 원칙:
    - frozen Pydantic — extra="forbid". downstream에서 untyped dict 흐름 차단.
    - args/result는 Dict[str, Any] — 도구별 schema는 input_schema/output_schema(JSON Schema 형태)로
      ToolSpec에 자기서술. Pydantic generic 도입 없이 가볍게.
    - 예외는 결코 ToolExecutor 밖으로 누출 안 함 — Observation(status="error")으로 감싼다.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# 정적 메타: ToolSpec
# ============================================================================

class ToolSpec(BaseModel):
    """도구의 정적 메타. PlannerAgent가 LLM prompt에 주입해 도구 호출 결정을 한다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(..., min_length=1)  # 예: "search.hybrid"
    description: str = Field(..., min_length=1)
    # input_schema / output_schema는 JSON Schema 부분집합 (필수 키만 기술). Planner LLM에
    # 그대로 노출되므로 짧고 명확하게.
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    # 비용 힌트. Planner가 비싼 도구 회피·캐싱 결정에 사용.
    cost_hint: Literal["fast", "medium", "slow"] = "medium"
    # 호출 전제 조건 (예: "focused_detail_required", "manifest_required"). Planner가 검사.
    preconditions: List[str] = Field(default_factory=list)


# ============================================================================
# 호출 / 결과 계약: ToolCall / Observation
# ============================================================================

class ToolCall(BaseModel):
    """PlannerAgent가 발행하는 단일 도구 호출."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(..., min_length=1)  # ToolSpec.name과 일치
    args: Dict[str, Any] = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    reason: str = ""  # Planner가 도구를 선택한 이유 (관측용)


class Observation(BaseModel):
    """ToolExecutor가 ToolCall을 실행한 결과. Planner가 다음 step 결정 시 참조."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str
    tool: str
    status: Literal["ok", "error"] = "ok"
    result: Dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    diagnostics: Dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Tool 실행 컨텍스트
# ============================================================================

@dataclass
class ToolContext:
    """도구 handler가 받는 의존성 컨테이너.

    Phase 1에서는 의존성을 최소로 — qdrant_client/llms/search_agent/entity_resolver/session 만.
    Phase 3에서 cyclic graph 도입 시 step_state(누적 observations, plan)을 추가.
    """

    qdrant_client: Any = None
    embed_e5i: Any = None
    embed_e5: Any = None
    dialogue_llm: Any = None
    answer_llm: Any = None
    # Phase 1: 기존 컴포넌트를 도구로 wrap할 때 위임 받을 인스턴스.
    search_agent: Any = None
    entity_resolver: Any = None
    session_state: Any = None
    request_id: str = ""
    turn_id: str = ""
    conversation_id: str = ""
    # Phase 6 (ADR-0022): SearchAgent 자동 라우팅을 위한 턴 단위 컨텍스트.
    # node_tool_executor가 매 turn 주입. SearchAgent handler가 target/subject/filters를
    # 자동 결정할 때 참조한다 — Planner는 이 정보를 알 필요 없다.
    entity_resolution: Any = None   # EntityResolution | None
    dialogue_kind: str = ""          # DialogueAgent.kind ("ask_search" 등)


# ============================================================================
# Tool registry / executor
# ============================================================================

ToolHandler = Callable[[Dict[str, Any], ToolContext], Awaitable[Dict[str, Any]]]


@dataclass(frozen=True)
class ToolEntry:
    """ToolExecutor의 registry 1 entry — spec + handler 쌍."""

    spec: ToolSpec
    handler: ToolHandler


class ToolExecutor:
    """ToolCall → Observation 매핑 dispatcher.

    handler 예외는 Observation(status="error", error_code/message)으로 감싸 Planner 안정성 보장.
    """

    def __init__(self, *, registry: Dict[str, ToolEntry], context: ToolContext) -> None:
        self._registry = registry
        self._context = context

    @property
    def context(self) -> ToolContext:
        return self._context

    def specs(self) -> List[ToolSpec]:
        """등록된 도구 spec 전체 — Planner prompt에 주입할 catalog."""
        return [entry.spec for entry in self._registry.values()]

    def spec_of(self, name: str) -> Optional[ToolSpec]:
        entry = self._registry.get(name)
        return entry.spec if entry else None

    async def execute(self, call: ToolCall) -> Observation:
        entry = self._registry.get(call.tool)
        if entry is None:
            return Observation(
                call_id=call.call_id,
                tool=call.tool,
                status="error",
                error_code="unknown_tool",
                error_message=f"tool {call.tool!r} not found in registry",
            )

        t0 = time.perf_counter()
        try:
            result = await entry.handler(call.args, self._context)
        except KeyError as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            logger.warning(
                f"[ToolExecutor] missing_arg tool={call.tool} call_id={call.call_id} "
                f"err={exc} args_keys={list(call.args.keys())}"
            )
            return Observation(
                call_id=call.call_id,
                tool=call.tool,
                status="error",
                error_code="missing_arg",
                error_message=f"missing required arg: {exc}",
                latency_ms=latency,
            )
        except (TypeError, ValueError) as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            logger.warning(
                f"[ToolExecutor] invalid_arg tool={call.tool} call_id={call.call_id} err={exc}"
            )
            return Observation(
                call_id=call.call_id,
                tool=call.tool,
                status="error",
                error_code="invalid_arg",
                error_message=str(exc)[:300],
                latency_ms=latency,
            )
        except Exception as exc:  # noqa: BLE001 — 모든 실패는 안전 fallback
            latency = (time.perf_counter() - t0) * 1000.0
            logger.exception(
                f"[ToolExecutor] handler_failed tool={call.tool} call_id={call.call_id} err={exc}"
            )
            return Observation(
                call_id=call.call_id,
                tool=call.tool,
                status="error",
                error_code="handler_failed",
                error_message=str(exc)[:300],
                latency_ms=latency,
            )

        latency = (time.perf_counter() - t0) * 1000.0
        if not isinstance(result, dict):
            return Observation(
                call_id=call.call_id,
                tool=call.tool,
                status="error",
                error_code="invalid_result_type",
                error_message=f"handler returned {type(result).__name__}, expected dict",
                latency_ms=latency,
            )
        # 결과 dict 내 'diagnostics' 키가 있으면 Observation.diagnostics로 추출 (관측용).
        diagnostics = result.pop("diagnostics", {}) if isinstance(result.get("diagnostics"), dict) else {}
        return Observation(
            call_id=call.call_id,
            tool=call.tool,
            status="ok",
            result=result,
            latency_ms=latency,
            diagnostics=diagnostics,
        )
