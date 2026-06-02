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
from apps.api.streaming.model_keys import stream_events_for_frontend
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
        # 이중 모델 출력 — A=Solar(메인, output_message), B=Gemma(비교, secondary_output_message).
        secondary_draft = _get(final_state, "secondary_answer_draft")
        secondary_text = str(getattr(secondary_draft, "text", "") or "").strip()
        references = []
        if artifact is not None and getattr(artifact, "references", None):
            references = list(artifact.references)
        answer_kind = getattr(artifact, "answer_kind", "llm_collected") if artifact else "llm_collected"
        return {
            "conversation_id": _get(final_state, "conversation_id", ""),
            "request_id": _get(final_state, "request_id", ""),
            "output_message": text,
            "secondary_output_message": secondary_text,
            "model_answers": _build_model_answers(text, secondary_text),
            "answer_kind": answer_kind,
            "references": references,
            "latencies": dict(_get(final_state, "latencies", {}) or {}),
            "total_ms": _calc_total_ms(final_state),
            "dialogue_intent": _dump(_get(final_state, "dialogue_intent")),
            "entity_resolution": _dump(_get(final_state, "entity_resolution")),
            "search_plan": _dump(_get(final_state, "search_plan")),
            "evidence_bundle_view": _get_evidence_view(final_state),
            "guard_decision": _dump(_get(final_state, "guard_decision")),
            # 운영 진단 (도구 분기·캐시·세션 상태) — 회귀 추적·대시보드용
            "diagnostics": _build_diagnostics(final_state, artifact),
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
                    for frontend_event in stream_events_for_frontend(event):
                        yield encode_stream_event(frontend_event)

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

def _build_model_answers(primary_text: str, secondary_text: str) -> list[dict[str, Any]]:
    """이중 모델 출력을 슬롯별로 구조화 (A=Solar 메인 / B=Gemma 비교).

    스트리밍 경로는 answer.chunk(model_key)로 각 패널에 직접 흐른다. 이 목록은
    /query(비스트리밍) JSON 응답과 done 메타의 관측·디버깅 용도다. 비교 답변이
    없으면(단일 모델 롤백·즉답·결정적 메시지) A 슬롯만 포함한다.
    """
    answers: list[dict[str, Any]] = [
        {"slot": "A", "model_key": "solar", "text": primary_text},
    ]
    if secondary_text:
        answers.append({"slot": "B", "model_key": "gemma", "text": secondary_text})
    return answers


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


def _build_diagnostics(state: Any, artifact: Any) -> dict[str, Any]:
    """운영 진단 메타 — 매 turn 도구 분기·캐싱·세션 상태를 한 dict로 노출.

    포함 키:
        - dialogue_kind, sort_by, coparticipants_n
        - resolution_source, manifest_resolved_target, dropped_identifier_hints
        - plan_reason, plan_task_count
        - search_status, search_cache_hit, retrieval_diag (latency 등)
        - evidence_view, evidence_count, child_entity_count
        - guard_decision, guard_reasoning, manifest_published_n
        - session: has_subject / has_manifest / has_focused_detail / cached_ids_axis
        - artifact_meta (도구 라벨)
    """
    diag: dict[str, Any] = {}

    intent = _get(state, "dialogue_intent", None)
    if intent is not None:
        diag["dialogue_kind"] = getattr(intent, "kind", None)
        diag["sort_by"] = getattr(intent, "sort_by", "relevance")
        diag["coparticipants_n"] = len(getattr(intent, "coparticipants", []) or [])
        diag["manifest_rank_in"] = getattr(intent, "manifest_rank", None)

    resolution = _get(state, "entity_resolution", None)
    if resolution is not None:
        diag["resolution_source"] = getattr(resolution, "resolution_source", None)
        diag["resolution_target"] = getattr(resolution, "manifest_resolved_target", None) or getattr(
            resolution, "forced_target", None
        )
        res_diag = getattr(resolution, "diagnostics", None) or {}
        if isinstance(res_diag, dict):
            dropped = res_diag.get("dropped_identifier_hints")
            if dropped:
                diag["dropped_identifier_hints"] = dropped

    plan = _get(state, "search_plan", None)
    if plan is not None:
        diag["plan_reason"] = getattr(plan, "plan_reason", None)
        diag["plan_task_count"] = len(getattr(plan, "tasks", []) or [])
        diag["plan_merge_strategy"] = getattr(plan, "merge_strategy", None)

    sr = _get(state, "search_result", None)
    if sr is not None:
        diag["search_status"] = getattr(sr, "status", None)
        diag["search_evidence_n"] = len(getattr(sr, "evidences", []) or [])
        diag["search_total_hits"] = getattr(sr, "total_hits", 0)
        sr_diag = getattr(sr, "diagnostics", None) or {}
        if isinstance(sr_diag, dict):
            if sr_diag.get("cache_hit"):
                diag["search_cache_hit"] = True
                diag["search_cache_matched_axis"] = sr_diag.get("matched_axis")
            if "latency_ms" in sr_diag:
                diag["search_latency_ms"] = sr_diag["latency_ms"]

    bundle = _get(state, "evidence_bundle", None)
    if bundle is not None:
        diag["evidence_view"] = getattr(bundle, "view", None)
        diag["evidence_count"] = len(getattr(bundle, "items", []) or [])
        b_diag = getattr(bundle, "diagnostics", None) or {}
        if isinstance(b_diag, dict) and "child_entity_count" in b_diag:
            diag["child_entity_count"] = b_diag["child_entity_count"]

    guard = _get(state, "guard_decision", None)
    if guard is not None:
        diag["guard_decision"] = getattr(guard, "decision", None)
        diag["guard_reasoning"] = getattr(guard, "reasoning", None)
        ref_manifest = getattr(guard, "reference_manifest", None)
        if ref_manifest is not None:
            diag["manifest_published_n"] = len(getattr(ref_manifest, "items", []) or [])
            diag["manifest_publication_status"] = getattr(ref_manifest, "publication_status", None)

    session_state = _get(state, "session_state", None)
    if session_state is not None:
        sess: dict[str, Any] = {
            "has_subject": bool(getattr(session_state, "current_subject", None)),
            "has_manifest": bool(getattr(session_state, "published_manifest", None)),
            "has_focused_detail": bool(getattr(session_state, "focused_detail", None)),
        }
        focused = getattr(session_state, "focused_detail", None)
        if focused is not None:
            cached_ids = getattr(focused, "cached_ids", None) or {}
            sess["focused_detail_cached_axes"] = sorted(cached_ids.keys()) if isinstance(cached_ids, dict) else []
        subj = getattr(session_state, "current_subject", None)
        if subj is not None:
            sess["subject_kind"] = getattr(subj, "subject_kind", None)
            sess["subject_name"] = getattr(subj, "subject_name", None)
        diag["session"] = sess

    if artifact is not None:
        artifact_meta = getattr(artifact, "meta", None) or {}
        if isinstance(artifact_meta, dict) and artifact_meta:
            diag["artifact_meta"] = dict(artifact_meta)

    return diag
