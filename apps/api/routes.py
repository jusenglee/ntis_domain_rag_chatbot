"""NTIS RAG 서버의 HTTP 라우트 계층.\n\n이 모듈은 FastAPI 요청/응답 계약만 맡는다.\n무거운 도메인 로직은 graph와 service helper에 두어 `apps/api/main.py`가 조립 진입점으로 남게 한다.\n"""

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from apps.api.rag_mapper.schema_types import DataTag
from apps.api.services.request_overrides import merge_request_overrides
from apps.api.streaming.contracts import AnswerArtifact, ErrorArtifact, StreamEvent
from apps.api.streaming.emitter import AsyncStreamEmitter
from apps.api.streaming.sse_encoder import encode_sse_payload, encode_stream_event
from apps.core.metrics import MetricSnapshot
from apps.core.schemas import strategy_spec_to_response


class QueryRequest(BaseModel):
    """`/query/*` 계열 엔드포인트가 받는 공통 요청 모델이다."""
    question: str
    conversation_id: Optional[str] = None
    temperature: Optional[float] = Field(default=None, alias="Temperature")
    top_p: Optional[float] = Field(default=None, alias="Top-P")
    max_tokens: Optional[int] = Field(default=None, alias="Max-Token")
    top_k: Optional[int] = Field(default=None, alias="Top-K")

    rag_min_dense_score: Optional[float] = Field(default=None, alias="RAG_MIN_DENSE_SCORE")
    rag_topk_dense: Optional[int] = Field(default=None, alias="RAG_TOPK_DENSE")
    rag_w_lex: Optional[float] = Field(default=None, alias="RAG_W_LEX")
    rag_topk_lex_cand: Optional[int] = Field(default=None, alias="RAG_TOPK_LEX_CAND")

@dataclass(frozen=True)
class RouteDeps:
    # 라우트 의존성을 명시적으로 유지해 runtime wiring이 숨은 전역 상태에 기대지 않게 한다.
    # app factory가 조립 진입점 역할을 계속 맡도록 라우트 wiring도 여기서만 받는다.
    """route 계층이 외부에서 주입받는 의존성 묶음이다.

    전역 숨은 상태를 피하고, app factory가 composition root 역할을 유지하도록
    라우트가 필요한 logger, graph, mapper, observability helper를 명시적으로 전달한다.
    """
    template_index_path: Any
    logger: Any
    log_event: Any
    is_debug_logging_enabled: Any
    mask_query_for_log: Any
    extract_stream_chunk_text_and_field: Any
    is_hit_source: Any
    derive_stream_error_code: Any
    compute_total_ms_from_start: Any
    extract_contract_failure_details: Any
    friendly_strategy_violation_message: Any
    collect_metrics_snapshot: Any
    metrics_stream_interval_seconds: float
    set_log_context: Any
    rag_mapper: Any
    human_message: Any
    strategy_violation: Any
    request_defaults_loader: Any = None


def register_routes(app: FastAPI, deps: RouteDeps) -> None:
    """FastAPI 앱에 query, health, metrics 관련 라우트를 등록한다."""
    template_index_path = deps.template_index_path
    logger = deps.logger
    log_event = deps.log_event
    is_debug_logging_enabled = deps.is_debug_logging_enabled
    mask_query_for_log = deps.mask_query_for_log
    extract_stream_chunk_text_and_field = deps.extract_stream_chunk_text_and_field
    is_hit_source = deps.is_hit_source
    derive_stream_error_code = deps.derive_stream_error_code
    compute_total_ms_from_start = deps.compute_total_ms_from_start
    extract_contract_failure_details = deps.extract_contract_failure_details
    friendly_strategy_violation_message = deps.friendly_strategy_violation_message
    collect_metrics_snapshot = deps.collect_metrics_snapshot
    metrics_stream_interval_seconds = deps.metrics_stream_interval_seconds
    set_log_context = deps.set_log_context
    rag_mapper = deps.rag_mapper
    human_message = deps.human_message
    strategy_violation = deps.strategy_violation
    request_defaults_loader = deps.request_defaults_loader

    def _get_graph(request: Request) -> Any:
        """`app.state`에서 컴파일된 workflow graph를 꺼낸다."""
        return getattr(getattr(request.app, "state", None), "graph", None)

    def _get_metrics_http(request: Request) -> Any:
        """`app.state`에서 metrics HTTP 클라이언트를 꺼낸다."""
        return getattr(getattr(request.app, "state", None), "metrics_http", None)

    def _get_kv_store(request: Request) -> Any:
        """`app.state`에서 대화 메모리용 KV store를 꺼낸다."""
        return getattr(getattr(request.app, "state", None), "kv_store", None)

    def _dump_model(value: Any) -> Any:
        """Pydantic 모델이나 유사 객체를 JSON 직렬화 가능한 값으로 바꾼다."""
        if value is None:
            return None
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump()
        return value

    def _state_get(state: Any, key: str, default: Any = None) -> Any:
        if state is None:
            return default
        if isinstance(state, dict):
            value = state.get(key, default)
        else:
            value = getattr(state, key, default)
        return default if value is None else value

    def _state_get_list(state: Any, key: str) -> list[Any]:
        value = _state_get(state, key, [])
        return value if isinstance(value, list) else []

    def _state_get_dict(state: Any, key: str) -> dict[str, Any]:
        value = _dump_model(_state_get(state, key, {}))
        return dict(value) if isinstance(value, dict) else {}

    def _normalize_answer_kind(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        allowed = {
            "llm_streamed",
            "llm_collected",
            "detail_cache",
            "detail_profile",
            "clarification",
            "no_result",
            "direct_answer",
            "error",
        }
        return normalized if normalized in allowed else "llm_collected"

    def _build_final_answer_artifact(state: Any) -> Optional[AnswerArtifact]:
        explicit_artifact = _state_get(state, "final_answer_artifact")
        if isinstance(explicit_artifact, AnswerArtifact):
            return explicit_artifact

        legacy_artifact = _state_get(state, "answer_artifact")
        if isinstance(legacy_artifact, AnswerArtifact):
            return legacy_artifact

        final_text = str(_state_get(state, "final_answer_text", "") or "").strip()
        if not final_text:
            messages = _state_get_list(state, "messages")
            if messages:
                final_text = str(getattr(messages[-1], "content", "") or "").strip()
        if not final_text:
            return None

        selected_answer_meta = _state_get_dict(state, "selected_answer_meta")
        merge_debug = _state_get_dict(state, "merge_debug")
        return AnswerArtifact(
            text=final_text,
            answer_kind=_normalize_answer_kind(
                selected_answer_meta.get("answer_kind") or merge_debug.get("selected_answer_kind")
            ),
            stream_metrics=selected_answer_meta,
            user_visible_final_required=bool(selected_answer_meta.get("user_visible_final_required", True)),
            meta={
                "answer_source": merge_debug.get("selected_answer_source"),
                "model_key": merge_debug.get("selected_model"),
            },
        )

    def _stream_data(tag: str, **payload: Any) -> str:
        """SSE 한 프레임을 현재 route 규약에 맞는 문자열로 인코딩한다."""
        return encode_sse_payload(tag, **payload)

    def _stream_legacy_payload(**payload: Any) -> str:
        """Legacy flat payload는 tag envelope 없이 기존 wire shape로 유지한다."""
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def _normalize_stream_model_key(model_key: str) -> str:
        normalized = str(model_key or "").strip().lower()
        if normalized in {"solar", "upstage"}:
            return "solar"
        if normalized == "gemma":
            return "gemma"
        return normalized

    def _resolve_stream_model_label(model_key: str) -> str:
        normalized = _normalize_stream_model_key(model_key)
        if normalized == "solar":
            return "UPSTAGE"
        if normalized == "gemma":
            return "GEMMA"
        return normalized.upper() or "UNKNOWN"

    def _stream_chunk(model_key: str, content: str) -> str:
        normalized = _normalize_stream_model_key(model_key)
        return _stream_data(
            "chunk",
            model=_resolve_stream_model_label(normalized),
            content=content,
        )

    def _emit_legacy_stream_event(event: StreamEvent) -> list[str]:
        payloads = [encode_stream_event(event)]
        if event.kind == "answer.chunk":
            payloads.append(_stream_chunk(event.model_key or "unknown", event.content or ""))
        elif event.kind == "answer.final":
            meta = dict(event.meta or {})
            if bool(meta.get("degraded")):
                payloads.append(
                    _stream_data(
                        "answer",
                        answer=event.content or "",
                        error_code=str(meta.get("error_code") or "DEGRADED_FINAL"),
                        reason=str(meta.get("reason") or "degraded_final_answer"),
                        degraded=True,
                    )
                )
        elif event.kind == "reference.set":
            payloads.append(_stream_legacy_payload(reference=list((event.meta or {}).get("references") or [])))
        elif event.kind == "error":
            meta = dict(event.meta or {})
            payloads.append(
                _stream_data(
                    "error",
                    error=str(meta.get("error") or ""),
                    error_code=str(meta.get("error_code") or "INTERNAL_ERROR"),
                    reason=str(meta.get("reason") or ""),
                )
            )
        elif event.kind == "done":
            meta = dict(event.meta or {})
            payload = {"status": "done"}
            if bool(meta.get("degraded")):
                payload["degraded"] = True
            payloads.append(_stream_data("status", **payload))
        return payloads

    def _pick_reference_value(reference: Dict[str, Any], doc: Dict[str, Any], *keys: str) -> Optional[str]:
        """reference payload와 원본 doc를 넘나들며 첫 유효 값을 찾는다."""
        for key in keys:
            value = reference.get(key)
            if value is None and isinstance(doc, dict):
                value = doc.get(key)
            if value is None and isinstance(doc, dict):
                for meta_key in ("meta_basic", "meta_detail"):
                    meta = doc.get(meta_key)
                    if isinstance(meta, dict):
                        value = meta.get(key)
                        if value is not None:
                            break
            text = str(value or "").strip()
            if text:
                return text
        return None

    def _is_project_reference_source(reference: Dict[str, Any], doc: Dict[str, Any]) -> bool:
        """현재 reference가 프로젝트 원본인지 판별한다.

        `pjt_id` 기반 식별자를 우선시하고, 성과 식별자(`rst_id`)와의 의미 충돌을 피한다.
        """
        tag = str(reference.get("tag") or doc.get("tag") or "").strip()
        if tag == DataTag.PROJECT.value:
            return True
        return bool(_pick_reference_value(reference, doc, "pjt_id")) and not bool(
            _pick_reference_value(reference, doc, "rst_id")
        )

    def _resolve_reference_id(reference: Dict[str, Any], doc: Dict[str, Any]) -> Optional[str]:
        """source semantics에 맞는 canonical reference id를 결정한다."""
        if _is_project_reference_source(reference, doc):
            return _pick_reference_value(reference, doc, "pjt_id")
        return _pick_reference_value(reference, doc, "rst_id")

    def _resolve_reference_title(reference: Dict[str, Any], doc: Dict[str, Any]) -> Optional[str]:
        """reference 표시에 쓸 제목을 우선순위 규칙으로 선택한다."""
        for key in ("title", "title_text", "title1", "title2"):
            value = reference.get(key)
            if value is None and isinstance(doc, dict):
                value = doc.get(key)
            text = str(value or "").strip()
            if text:
                return text
        meta_basic = doc.get("meta_basic") if isinstance(doc, dict) else None
        if isinstance(meta_basic, dict):
            for key in ("kor_pjt_nm", "title", "paper_nm"):
                text = str(meta_basic.get(key) or "").strip()
                if text:
                    return text
        return None

    def _normalize_reference_payload(doc: Dict[str, Any]) -> Dict[str, Any]:
        """context 문서를 API 응답용 reference payload로 정규화한다."""
        mapped = rag_mapper.get_references(doc)
        if not isinstance(mapped, dict):
            mapped = {}
        result = dict(mapped)
        result["tag"] = str(result.get("tag") or doc.get("tag") or "").strip() or None
        result["id"] = _resolve_reference_id(result, doc)
        result["title"] = _resolve_reference_title(result, doc)
        return result

    def _validate_request_override_ranges(overrides: dict[str, Any]) -> None:
        """잘못된 LLM override 값이 provider backend까지 내려가기 전에 막는다."""
        temperature = overrides.get("temperature")
        if temperature is not None and float(temperature) < 0:
            raise HTTPException(status_code=422, detail="Temperature must be >= 0")

        top_p = overrides.get("top_p")
        if top_p is not None and not (0 < float(top_p) <= 1):
            raise HTTPException(status_code=422, detail="Top-P must be > 0 and <= 1")

        max_tokens = overrides.get("max_tokens")
        if max_tokens is not None and int(max_tokens) < 1:
            raise HTTPException(status_code=422, detail="Max-Token must be >= 1")

        top_k = overrides.get("top_k")
        if top_k is not None and int(top_k) < 1:
            raise HTTPException(status_code=422, detail="Top-K must be >= 1")

    def _extract_request_override_values(payload: QueryRequest) -> dict[str, Any]:
        """요청 payload에서 명시적으로 들어온 override 값만 추출한다."""
        overrides: dict[str, Any] = {}

        if payload.temperature is not None:
            overrides["temperature"] = float(payload.temperature)
        if payload.top_p is not None:
            overrides["top_p"] = float(payload.top_p)
        if payload.max_tokens is not None:
            overrides["max_tokens"] = int(payload.max_tokens)
        if payload.top_k is not None:
            overrides["top_k"] = int(payload.top_k)

        if payload.rag_min_dense_score is not None:
            overrides["RAG_MIN_DENSE_SCORE"] = float(payload.rag_min_dense_score)
        if payload.rag_topk_dense is not None:
            overrides["RAG_TOPK_DENSE"] = int(payload.rag_topk_dense)
        if payload.rag_w_lex is not None:
            overrides["RAG_W_LEX"] = float(payload.rag_w_lex)
        if payload.rag_topk_lex_cand is not None:
            overrides["RAG_TOPK_LEX_CAND"] = int(payload.rag_topk_lex_cand)
        return overrides

    async def _build_request_overrides(payload: QueryRequest) -> tuple[dict[str, Any], dict[str, str]]:
        """요청 payload와 Oracle 기본값을 병합해 최종 override와 source를 반환한다."""
        request_values = _extract_request_override_values(payload)
        oracle_defaults: dict[str, Any] = {}
        supported_override_count = 8
        if request_defaults_loader is not None and len(request_values) < supported_override_count:
            load_defaults = getattr(request_defaults_loader, "load_defaults", None)
            if callable(load_defaults):
                loaded = await load_defaults()
                if isinstance(loaded, dict):
                    oracle_defaults = dict(loaded)
        merged, sources = merge_request_overrides(
            request_values=request_values,
            oracle_defaults=oracle_defaults,
        )
        _validate_request_override_ranges(merged)
        return merged, sources


    async def _runtime_status(request: Request) -> dict[str, Any]:
        """현재 runtime 상태를 health 응답 형식으로 조립한다."""
        kv_store = _get_kv_store(request)
        graph_ready = _get_graph(request) is not None
        metrics_ready = _get_metrics_http(request) is not None
        kv_connected = False
        if kv_store:
            kv_connected = await kv_store.ping()
        ready = graph_ready and kv_connected
        status = "healthy" if graph_ready and kv_connected else ("degraded" if graph_ready else "not_ready")
        return {
            "status": status,
            "ready": ready,
            "memory_backend": "redis",
            "memory_backend_effective": type(kv_store).__name__ if kv_store else "none",
            "kv": "connected" if kv_connected else "disconnected",
            "graph": "compiled" if graph_ready else "not_ready",
            "metrics": "ready" if metrics_ready else "not_ready",
        }

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        """기본 UI 템플릿이 있으면 반환하고, 없으면 간단한 fallback HTML을 돌려준다."""
        if not template_index_path.exists():
            return HTMLResponse(
                content="<html><body><h3>NTIS RAG Chatbot</h3><p>index template unavailable.</p></body></html>",
                status_code=200,
            )
        return HTMLResponse(content=template_index_path.read_text(encoding="utf-8"), status_code=200)

    @app.post("/query/stream")
    async def query_stream(payload: QueryRequest, request: Request) -> StreamingResponse:
        """질의 처리 결과를 SSE로 스트리밍하는 주 엔드포인트다."""
        question = payload.question
        conversation_id = payload.conversation_id or str(uuid.uuid4())
        request_id = f"{conversation_id}-{uuid.uuid4().hex[:8]}"

        request_overrides, request_override_sources = await _build_request_overrides(payload)
        graph = _get_graph(request)

        async def event_generator():
            """graph 이벤트를 route 레벨 SSE 규약으로 변환해 순차 전송한다."""
            if graph is None:
                yield _stream_data("error", error="runtime_not_ready", error_code="RUNTIME_NOT_READY")
                yield _stream_data("status", status="done", error=True)
                return
            yield _stream_data("conversation", conversationId=conversation_id)
            log_event(
                "REQ.START",
                request_id=request_id,
                conversation_id=conversation_id,
                stage="request_start",
                q_len=len(question),
                q_preview=mask_query_for_log(question) if is_debug_logging_enabled() else None,
                request_overrides=request_overrides or None,
                request_override_sources=request_override_sources or None,
            )
            request_started_at = time.perf_counter()
            emitter = AsyncStreamEmitter()
            final_state: Optional[Any] = None
            question_analysis: Optional[Any] = None
            route_seq = 0

            def _next_route_event(*, kind: str, model_key: Optional[str] = None, content: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> StreamEvent:
                nonlocal route_seq
                route_seq += 1
                return StreamEvent(kind=kind, request_id=request_id, seq=route_seq, model_key=model_key, content=content, meta=dict(meta or {}))

            try:
                set_log_context(request_id=request_id, conversation_id=conversation_id)
                user_message = human_message(content=question)
                inputs = {
                    "conversation_id": conversation_id,
                    "request_id": request_id,
                    "request_started_at": request_started_at,
                    "messages": [user_message],
                    "kv_store": _get_kv_store(request),
                    "request_overrides": request_overrides,
                    "stream_emitter": emitter,
                }

                if not hasattr(graph, "ainvoke") and hasattr(graph, "astream_events"):
                    final_state = {
                        "context": [],
                        "merge_debug": {},
                        "selected_answer_meta": {},
                        "final_answer_text": None,
                        "final_answer_artifact": None,
                        "clarification": None,
                    }
                    async for event in graph.astream_events(inputs, version="v2"):
                        kind = event["event"]
                        node = event.get("metadata", {}).get("langgraph_node", "")
                        data = event.get("data", {})
                        if kind == "on_chain_end" and node == "rag_search":
                            output = data.get("output", {}) or {}
                            if isinstance(output.get("context"), list):
                                final_state["context"] = list(output.get("context") or [])
                            if isinstance(output.get("clarification"), dict):
                                final_state["clarification"] = output.get("clarification")
                        elif kind == "on_chain_end" and node in {"generate_answer_solar", "generate_answer_gemma"}:
                            output = data.get("output", {}) or {}
                            answer_key = "answer_solar" if node == "generate_answer_solar" else "answer_gemma"
                            answer_meta_key = f"{answer_key}_meta"
                            artifact_key = "answer_artifact_solar" if node == "generate_answer_solar" else "answer_artifact_gemma"
                            answer_text = str(output.get(answer_key) or "").strip()
                            answer_meta = dict(output.get(answer_meta_key) or {})
                            final_state[answer_key] = answer_text
                            final_state[answer_meta_key] = answer_meta
                            if isinstance(output.get(artifact_key), AnswerArtifact):
                                final_state[artifact_key] = output.get(artifact_key)
                        if kind == "on_chain_end" and node == "merge_answers":
                            output = data.get("output", {}) or {}
                            final_state.update(output)
                        elif kind == "on_chain_end" and node == "analyze_question":
                            question_analysis = data.get("output", {})
                        elif kind == "on_chain_end" and node == "direct_answer":
                            output = data.get("output", {}) or {}
                            final_state.update(output)
                        elif kind == "on_chat_model_stream" and node in {"generate_answer_solar", "generate_answer_gemma"}:
                            chunk = data.get("chunk")
                            chunk_text, stream_field = extract_stream_chunk_text_and_field(chunk)
                            if stream_field == "reasoning" or not chunk_text:
                                continue
                            model_key = "solar" if node == "generate_answer_solar" else "gemma"
                            for payload_line in _emit_legacy_stream_event(
                                _next_route_event(kind="answer.chunk", model_key=model_key, content=chunk_text, meta={})
                            ):
                                yield payload_line
                    final_state = dict(final_state or {})
                else:
                    graph_task = asyncio.create_task(graph.ainvoke(inputs))
                    yield _stream_data("status", status="retrieve")

                    while True:
                        if graph_task.done() and emitter.empty():
                            break
                        try:
                            emitted_event = await asyncio.wait_for(emitter.next_event(), timeout=0.1)
                        except asyncio.TimeoutError:
                            continue
                        if emitted_event is None:
                            if graph_task.done():
                                break
                            continue
                        route_seq = max(route_seq, int(getattr(emitted_event, "seq", 0) or 0))
                        for payload_line in _emit_legacy_stream_event(emitted_event):
                            yield payload_line

                    final_state = await graph_task
                    question_analysis = _state_get(final_state, "question_analysis")
                documents_used = _state_get_list(final_state, "context")
                clarification_payload = _state_get(final_state, "clarification")
                if not isinstance(clarification_payload, dict):
                    clarification_payload = None
                if clarification_payload is None:
                    retrieval_bundle = _state_get(final_state, "retrieval_bundle")
                    bundle_clarification = getattr(retrieval_bundle, "clarification", None)
                    if isinstance(bundle_clarification, dict):
                        clarification_payload = bundle_clarification
                selected_answer_meta = _state_get_dict(final_state, "selected_answer_meta")
                merge_debug = _state_get_dict(final_state, "merge_debug")
                selected_artifact = _build_final_answer_artifact(final_state)

                if clarification_payload is None and isinstance(selected_artifact, AnswerArtifact) and selected_artifact.clarification is not None:
                    clarification_payload = {
                        "clarification_type": selected_artifact.clarification.clarification_type,
                        "message": selected_artifact.clarification.message,
                        "candidates": list(selected_artifact.clarification.candidates),
                        "resume_token": dict(selected_artifact.clarification.resume_token),
                    }

                user_visible_terminal_emitted = False

                if clarification_payload:
                    clarification_event = _next_route_event(
                        kind="clarification",
                        meta={"clarification": clarification_payload},
                    )
                    for payload_line in _emit_legacy_stream_event(clarification_event):
                        yield payload_line
                    user_visible_terminal_emitted = True

                if isinstance(selected_artifact, AnswerArtifact) and selected_artifact.text and selected_artifact.user_visible_final_required:
                    final_event = _next_route_event(
                        kind="answer.final",
                        model_key=_normalize_stream_model_key(str(merge_debug.get("selected_model") or selected_artifact.meta.get("model_key") or "")),
                        content=selected_artifact.text,
                        meta=selected_artifact.to_meta_dict(),
                    )
                    for payload_line in _emit_legacy_stream_event(final_event):
                        yield payload_line
                    user_visible_terminal_emitted = True

                if not user_visible_terminal_emitted:
                    guard_reason = "route completed without final_answer_artifact/final_answer_text/clarification"
                    guard_message = "응답 생성은 완료되었지만 표시할 최종 답변이 비어 있습니다. 다시 시도해 주세요."
                    guard_artifact = AnswerArtifact(
                        text=guard_message,
                        answer_kind="error",
                        stream_metrics={},
                        user_visible_final_required=True,
                        error=ErrorArtifact(
                            error_code="MISSING_FINAL_ANSWER",
                            reason=guard_reason,
                            retryable=True,
                        ),
                        meta={"answer_source": "route_contract_guard", "degraded": True},
                    )
                    guard_event = _next_route_event(
                        kind="answer.final",
                        content=guard_message,
                        meta=guard_artifact.to_meta_dict()
                        | {
                            "error_code": "MISSING_FINAL_ANSWER",
                            "reason": guard_reason,
                            "degraded": True,
                        },
                    )
                    for payload_line in _emit_legacy_stream_event(guard_event):
                        yield payload_line

                ref_docs = []
                seen_reference_keys = set()
                for doc in documents_used:
                    if not is_hit_source(doc):
                        continue
                    normalized = _normalize_reference_payload(doc)
                    dedupe_key = (
                        normalized.get("tag"),
                        normalized.get("id"),
                        normalized.get("title"),
                    )
                    if dedupe_key in seen_reference_keys:
                        continue
                    seen_reference_keys.add(dedupe_key)
                    ref_docs.append(normalized)

                if ref_docs:
                    ref_event = _next_route_event(
                        kind="reference.set",
                        meta={"references": ref_docs},
                    )
                    for payload_line in _emit_legacy_stream_event(ref_event):
                        yield payload_line

                solar_done = _state_get_dict(final_state, "answer_solar_meta")
                gemma_done = _state_get_dict(final_state, "answer_gemma_meta")
                log_event(
                    "STREAM.DONE",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    stage="stream_done",
                    solar_error_code=derive_stream_error_code(solar_done),
                    solar_elapsed_ms=solar_done.get("elapsed_ms"),
                    solar_ttft_any_ms=solar_done.get("ttft_any_ms"),
                    solar_ttft_content_ms=solar_done.get("ttft_content_ms"),
                    solar_content_chars=solar_done.get("content_chars"),
                    gemma_error_code=derive_stream_error_code(gemma_done),
                    gemma_elapsed_ms=gemma_done.get("elapsed_ms"),
                    gemma_ttft_any_ms=gemma_done.get("ttft_any_ms"),
                    gemma_ttft_content_ms=gemma_done.get("ttft_content_ms"),
                    gemma_content_chars=gemma_done.get("content_chars"),
                )
                done_event = _next_route_event(kind="done", meta={})
                for payload_line in _emit_legacy_stream_event(done_event):
                    yield payload_line

            except Exception as exc:
                logger.error("Stream Error: %s", exc, exc_info=True)
                error_code = getattr(exc, "error_code", "INTERNAL_ERROR")
                reason = getattr(exc, "reason", str(exc))
                total_ms = compute_total_ms_from_start(request_started_at)
                degraded = isinstance(exc, strategy_violation)
                contract_failure_details = extract_contract_failure_details(reason)
                log_event(
                    "REQ.ERROR",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    stage="stream",
                    error_code=error_code,
                    reason=reason,
                    mode=(str(getattr(question_analysis, "mode", "") or "").upper() or None),
                    relation=getattr(question_analysis, "relation", None),
                    degraded=int(degraded),
                    total_ms=total_ms,
                    **contract_failure_details,
                )
                if degraded:
                    user_message = friendly_strategy_violation_message(
                        error_code=error_code,
                        reason=reason,
                        question_analysis=question_analysis,
                    )
                    error_artifact = AnswerArtifact(
                        text=user_message,
                        answer_kind="error",
                        stream_metrics={},
                        user_visible_final_required=True,
                        error=ErrorArtifact(error_code=error_code, reason=reason),
                        meta={"degraded": True, "answer_source": "strategy_violation"},
                    )
                    for payload_line in _emit_legacy_stream_event(
                        _next_route_event(
                            kind="answer.final",
                            content=user_message,
                            meta=error_artifact.to_meta_dict() | {"error_code": error_code, "reason": reason, "degraded": True},
                        )
                    ):
                        yield payload_line
                    for payload_line in _emit_legacy_stream_event(_next_route_event(kind="done", meta={"degraded": True})):
                        yield payload_line
                    return
                for payload_line in _emit_legacy_stream_event(
                    _next_route_event(
                        kind="error",
                        meta={"error": str(exc), "error_code": error_code, "reason": reason},
                    )
                ):
                    yield payload_line
                for payload_line in _emit_legacy_stream_event(_next_route_event(kind="done", meta={"error": True})):
                    yield payload_line
            finally:
                await emitter.close()
                set_log_context(request_id=None, conversation_id=None)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    debug_routes_enabled = os.getenv("ENABLE_DEBUG_ROUTES", "false").strip().lower() in {"1", "true", "yes", "on"}

    @app.post("/query/debug")
    async def query_debug(payload: QueryRequest, request: Request) -> Dict[str, Any]:
        """디버그용으로 graph 실행 결과를 JSON으로 그대로 노출한다."""
        if not debug_routes_enabled:
            raise HTTPException(status_code=404, detail="not found")
        question = payload.question
        conversation_id = payload.conversation_id or str(uuid.uuid4())
        request_id = f"{conversation_id}-{uuid.uuid4().hex[:8]}"
        request_overrides, request_override_sources = await _build_request_overrides(payload)
        graph = _get_graph(request)

        if graph is None:
            return {
                "success": False,
                "error": "runtime_not_ready",
                "error_code": "RUNTIME_NOT_READY",
                "reason": "compiled graph unavailable",
            }

        request_started_at = time.perf_counter()
        stage = "debug_request_start"
        try:
            set_log_context(request_id=request_id, conversation_id=conversation_id)
            log_event(
                "REQ.START",
                request_id=request_id,
                conversation_id=conversation_id,
                stage=stage,
                q_len=len(question),
                request_overrides=request_overrides or None,
                request_override_sources=request_override_sources or None,
            )
            user_message = human_message(content=question)

            stage = "planner_memory_build"
            inputs = {
                "conversation_id": conversation_id,
                "request_id": request_id,
                "request_started_at": request_started_at,
                "messages": [user_message],
                "kv_store": _get_kv_store(request),
                "request_overrides": request_overrides,
            }

            final_state = await graph.ainvoke(inputs)

            question_analysis = _state_get(final_state, "question_analysis")
            knowledge_sufficiency = _state_get(final_state, "knowledge_sufficiency")
            strategy = _state_get(final_state, "strategy")

            total_ms = compute_total_ms_from_start(request_started_at)
            log_event("REQ.END", request_id=request_id, conversation_id=conversation_id, stage="debug_done", total_ms=total_ms)
            messages = _state_get_list(final_state, "messages")
            output_message = str(_state_get(final_state, "final_answer_text", "") or "").strip()
            if not output_message and messages:
                output_message = str(getattr(messages[-1], "content", "") or "").strip()

            merge_debug = _state_get_dict(final_state, "merge_debug")
            selected_answer_meta = _state_get_dict(final_state, "selected_answer_meta")
            clarification = _state_get(final_state, "clarification")
            if not isinstance(clarification, dict):
                clarification = None
            if clarification is None:
                retrieval_bundle = _state_get(final_state, "retrieval_bundle")
                bundle_clarification = getattr(retrieval_bundle, "clarification", None)
                if isinstance(bundle_clarification, dict):
                    clarification = bundle_clarification
            selected_artifact = _build_final_answer_artifact(final_state)

            return {
                "success": True,
                "conversation_id": conversation_id,
                "answer_gemma": _state_get(final_state, "answer_gemma"),
                "answer_solar": _state_get(final_state, "answer_solar"),
                "output_message": output_message,
                "final_answer_meta": (selected_artifact.to_meta_dict() if isinstance(selected_artifact, AnswerArtifact) else None),
                "question_analysis": _dump_model(question_analysis),
                "strategy_summary": strategy_spec_to_response(strategy),
                "knowledge_sufficiency": _dump_model(knowledge_sufficiency),
                "documents_used": len(_state_get_list(final_state, "context")),
                "latencies": _state_get_dict(final_state, "latencies"),
                "total_time": total_ms,
                "processing_strategy": getattr(knowledge_sufficiency, "requires_new_knowledge", None) if knowledge_sufficiency else "unknown",
                "clarification": clarification,
                "merge_debug": merge_debug,
                "selected_answer_meta": selected_answer_meta,
            }

        except Exception as exc:
            logger.error("Debug Error: %s", exc, exc_info=True)
            degraded = isinstance(exc, strategy_violation)
            error_code = getattr(exc, "error_code", "INTERNAL_ERROR")
            reason = getattr(exc, "reason", str(exc))
            contract_failure_details = extract_contract_failure_details(reason)
            log_event(
                "REQ.ERROR",
                request_id=request_id,
                conversation_id=conversation_id,
                stage=stage,
                error_type=type(exc).__name__,
                error_code=error_code,
                reason=reason,
                mode=(str(getattr(question_analysis, "mode", "") or "").upper() or None),
                relation=getattr(question_analysis, "relation", None),
                degraded=int(degraded),
                total_ms=compute_total_ms_from_start(request_started_at),
                **contract_failure_details,
            )
            return {
                "success": False,
                "error": str(exc),
                "error_code": error_code,
                "reason": reason,
                **contract_failure_details,
            }
        finally:
            set_log_context(request_id=None, conversation_id=None)

    @app.get("/health")
    async def health_check(request: Request) -> JSONResponse:
        """ready 여부에 따라 200 또는 503을 반환하는 기본 health 엔드포인트다."""
        payload = await _runtime_status(request)
        return JSONResponse(content=payload, status_code=200 if payload["ready"] else 503)

    @app.get("/health/details")
    async def health_details(request: Request) -> JSONResponse:
        """상세 health payload를 항상 200으로 반환한다."""
        payload = await _runtime_status(request)
        return JSONResponse(content=payload, status_code=200)

    @app.get("/metrics", response_model=MetricSnapshot, response_model_by_alias=True)
    async def get_metrics(request: Request) -> MetricSnapshot | JSONResponse:
        """현재 수집된 metrics snapshot을 반환한다."""
        metrics_http = _get_metrics_http(request)
        if metrics_http is None:
            return JSONResponse(
                content={"error": "runtime_not_ready", "error_code": "RUNTIME_NOT_READY", "reason": "metrics client unavailable"},
                status_code=503,
            )
        return await collect_metrics_snapshot(metrics_http)

    @app.get("/metrics/stream")
    async def stream_metrics(request: Request) -> StreamingResponse:
        """metrics snapshot을 주기적으로 SSE로 밀어주는 엔드포인트다."""
        metrics_http = _get_metrics_http(request)

        async def event_generator() -> Any:
            """metrics snapshot을 끊기 전까지 주기적으로 내보낸다."""
            if metrics_http is None:
                yield "event: error\ndata: {\"error\":\"runtime_not_ready\",\"error_code\":\"RUNTIME_NOT_READY\"}\n\n"
                return
            while True:
                if await request.is_disconnected():
                    break

                snapshot = await collect_metrics_snapshot(metrics_http)
                payload = snapshot.model_dump_json(by_alias=True)
                yield f"event: metrics\ndata: {payload}\n\n"

                await asyncio.sleep(metrics_stream_interval_seconds)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

