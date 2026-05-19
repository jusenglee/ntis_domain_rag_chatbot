"""NTIS RAG HTTP 라우트 (3계층 권한분리 파이프라인 전용).

ADR-0018 적용 후 슬림 버전. 다음 엔드포인트만 노출한다:
    GET  /         → 메인 UI 페이지 (templates/index.html)
    POST /query    → 단일 응답 (JSON)
    POST /query/stream → SSE 스트리밍 응답
    GET  /health   → 단순 헬스체크

기존 routes.py의 1000+ 라인 헬퍼들(metrics streaming, debug logging, contract failure 추출 등)은
모두 폐기. 새 파이프라인은 SSE 이벤트를 PipelineState.stream_emitter로 직접 publish하므로
라우트는 단순히 emitter 큐에서 빼서 SSE로 인코딩만 한다.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from apps.api.streaming.contracts import StreamEvent
from apps.api.streaming.emitter import AsyncStreamEmitter
from apps.api.streaming.sse_encoder import encode_stream_event
from apps.pipeline.agent_state import AgentPipelineState


class QueryRequest(BaseModel):
    """/query, /query/stream 공통 요청 모델."""

    question: str = Field(..., description="사용자 질문 텍스트")
    conversation_id: Optional[str] = Field(None, description="대화 세션 UUID")
    request_overrides: dict[str, Any] = Field(default_factory=dict, description="요청별 오버라이드 메타")


def register_routes(app: FastAPI, *, template_index_path: Path) -> None:
    """FastAPI 앱에 슬림 라우트를 등록한다."""

    def _get_graph(request: Request) -> Any:
        graph = getattr(getattr(request.app, "state", None), "graph", None)
        if graph is None:
            raise HTTPException(status_code=503, detail="pipeline graph not initialized")
        return graph

    def _get_kv_store(request: Request) -> Any:
        return getattr(getattr(request.app, "state", None), "kv_store", None)

    def _build_state(payload: QueryRequest, request: Request, *, emitter: Optional[AsyncStreamEmitter]) -> AgentPipelineState:
        conversation_id = (payload.conversation_id or "").strip() or str(uuid.uuid4())
        request_id = f"{conversation_id}-{uuid.uuid4().hex[:8]}"
        turn_id = f"turn:{int(time.time())}:{uuid.uuid4().hex[:8]}"
        return AgentPipelineState(
            question=payload.question.strip(),
            conversation_id=conversation_id,
            request_id=request_id,
            turn_id=turn_id,
            request_started_at=time.perf_counter(),
            request_overrides=dict(payload.request_overrides or {}),
            kv_store=_get_kv_store(request),
            stream_emitter=emitter,
        )

    def _build_response_payload(final_state: Any) -> dict[str, Any]:
        artifact = _get(final_state, "answer_artifact")
        text = str(_get(final_state, "final_answer_text", "") or "").strip()
        references = []
        if artifact is not None and getattr(artifact, "references", None):
            references = list(artifact.references)
        answer_kind = getattr(artifact, "answer_kind", "llm_collected") if artifact else "llm_collected"
        return {
            "conversation_id": _get(final_state, "conversation_id", ""),
            "request_id": _get(final_state, "request_id", ""),
            "output_message": text,
            "answer_kind": answer_kind,
            "references": references,
            "latencies": dict(_get(final_state, "latencies", {}) or {}),
            "total_ms": _calc_total_ms(final_state),
            "dialogue_intent": _dump(_get(final_state, "dialogue_intent")),
            "entity_resolution": _dump(_get(final_state, "entity_resolution")),
            "search_plan": _dump(_get(final_state, "search_plan")),
            "evidence_bundle_view": _get_evidence_view(final_state),
            "guard_decision": _dump(_get(final_state, "guard_decision")),
        }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        if not template_index_path.exists():
            return HTMLResponse(content="<h1>NTIS RAG (pipeline)</h1>", status_code=200)
        content = template_index_path.read_text(encoding="utf-8")
        return HTMLResponse(content=content)

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
        return JSONResponse(
            content={
                "ok": True,
                "graph_compiled": getattr(request.app.state, "graph", None) is not None,
                "kv_ready": getattr(request.app.state, "kv_store", None) is not None,
            }
        )

    @app.post("/query")
    async def query(payload: QueryRequest, request: Request) -> JSONResponse:
        """단일 응답 엔드포인트. 스트리밍 없이 워크플로우 결과만 JSON으로.

        빈 질문은 400으로 거부하지 않고 그대로 워크플로우에 위임한다 — DialogueAgent가
        ``kind=clarification`` 으로 안전 닫기 응답을 만든다 (R7 회귀 가드 참조).
        """
        graph = _get_graph(request)
        state = _build_state(payload, request, emitter=None)
        try:
            final_state = await graph.ainvoke(state)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[/query] graph invoke failed: err={exc}")
            raise HTTPException(status_code=500, detail=str(exc))
        return JSONResponse(content=_build_response_payload(final_state))

    @app.post("/query/stream")
    async def query_stream(payload: QueryRequest, request: Request) -> StreamingResponse:
        """SSE 스트리밍 엔드포인트.

        파이프라인이 emitter에 publish하는 동안 라우트는 큐를 읽어 SSE로 인코딩한다.
        graph 실행이 끝나면 done 이벤트 한 번 보내고 종료한다.

        빈 질문은 400으로 거부하지 않고 그대로 워크플로우에 위임한다 — DialogueAgent가
        ``kind=clarification`` 으로 안전 닫기 응답을 만든다.
        """
        graph = _get_graph(request)
        emitter = AsyncStreamEmitter()
        state = _build_state(payload, request, emitter=emitter)

        async def graph_runner() -> Any:
            try:
                final_state = await graph.ainvoke(state)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"[/query/stream] graph invoke failed: err={exc}")
                await emitter.publish(
                    StreamEvent(
                        kind="answer.chunk",
                        request_id=state.request_id,
                        content=f"내부 오류: {exc}",
                        model_key="internal_error",
                    )
                )
                final_state = None
            finally:
                await emitter.close()
            return final_state

        async def event_generator():
            # conversation 이벤트 (turn 시작 알림)
            yield encode_stream_event(
                StreamEvent(
                    kind="conversation",
                    request_id=state.request_id,
                    meta={"conversation_id": state.conversation_id, "turn_id": state.turn_id},
                )
            )

            task = asyncio.create_task(graph_runner())
            try:
                while True:
                    if emitter.closed and emitter.empty():
                        break
                    try:
                        event = await asyncio.wait_for(emitter.next_event(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # 30초 idle: 클라이언트 keep-alive 유지를 위한 빈 frame
                        yield ": keep-alive\n\n"
                        continue
                    if event is None:
                        break
                    yield encode_stream_event(event)

                final_state = await task

                # references frame (있으면)
                artifact = _get(final_state, "answer_artifact")
                if artifact is not None and getattr(artifact, "references", None):
                    yield encode_stream_event(
                        StreamEvent(
                            kind="reference.set",
                            request_id=state.request_id,
                            references=list(artifact.references),
                        )
                    )

                # done frame
                meta = _build_response_payload(final_state)
                yield encode_stream_event(
                    StreamEvent(
                        kind="done",
                        request_id=state.request_id,
                        meta=meta,
                    )
                )
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except Exception:
                        pass

        return StreamingResponse(event_generator(), media_type="text/event-stream")


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _get(state: Any, key: str, default: Any = None) -> Any:
    if state is None:
        return default
    if isinstance(state, dict):
        return state.get(key, default) if state.get(key, default) is not None else default
    v = getattr(state, key, default)
    return default if v is None else v


def _dump(value: Any) -> Any:
    if value is None:
        return None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump()
        except Exception:
            return None
    if hasattr(value, "__dict__"):
        try:
            return dict(value.__dict__)
        except Exception:
            return None
    return value


def _calc_total_ms(state: Any) -> float:
    started = _get(state, "request_started_at", None)
    if not started:
        return 0.0
    return (time.perf_counter() - started) * 1000.0


def _get_evidence_view(state: Any) -> Optional[str]:
    bundle = _get(state, "evidence_bundle", None)
    if bundle is None:
        return None
    return getattr(bundle, "view", None)
