"""HTTP route layer for the NTIS RAG server.\n\nThis module owns FastAPI request and response contracts only.\nHeavy domain logic stays in the graph and service helpers so `apps/api/main.py` remains the composition root.\n"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from apps.api.rag_mapper.schema_types import DataTag
from apps.api.streaming.contracts import AnswerArtifact, ErrorArtifact, StreamEvent
from apps.api.streaming.emitter import AsyncStreamEmitter
from apps.api.streaming.sse_encoder import encode_sse_payload, encode_stream_event
from apps.core.metrics import MetricSnapshot
from apps.core.schemas import strategy_spec_to_response


class QueryRequest(BaseModel):
    """`/query/*` ??? ???? HTTP ?? ????."""
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
    # Keep route dependencies explicit so runtime wiring changes do not require hidden globals.
    # Keep route wiring explicit so the app factory remains the composition root.
    """route layer? ??? ?? runtime ???? ????? ???."""
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


def register_routes(app: FastAPI, deps: RouteDeps) -> None:
    """FastAPI app? home, query, health, metrics route? ????."""
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

    def _get_graph(request: Request) -> Any:
        """app.state?? ???? graph? ????."""
        return getattr(getattr(request.app, "state", None), "graph", None)

    def _get_metrics_http(request: Request) -> Any:
        """app.state?? metrics client? ????."""
        return getattr(getattr(request.app, "state", None), "metrics_http", None)

    def _get_kv_store(request: Request) -> Any:
        """app.state?? memory/kv store? ????."""
        return getattr(getattr(request.app, "state", None), "kv_store", None)

    def _dump_model(value: Any) -> Any:
        """pydantic model? JSON-serializable dict? ??? ??? ?? ??? ??."""
        if value is None:
            return None
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump()
        return value

    def _stream_data(tag: str, **payload: Any) -> str:
        """SSE ???? `data: ...` ??? ?????."""
        return encode_sse_payload(tag, **payload)

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
            return "SOLAR"
        if normalized == "gemma":
            return "GEMMA"
        return normalized.upper() or "UNKNOWN"

    def _stream_chunk(model_key: str, content: str, **extra: Any) -> str:
        normalized = _normalize_stream_model_key(model_key)
        return _stream_data(
            "chunk",
            model=_resolve_stream_model_label(normalized),
            model_key=normalized or None,
            content=content,
            **extra,
        )

    def _emit_legacy_stream_event(event: StreamEvent) -> list[str]:
        payloads = [encode_stream_event(event)]
        if event.kind == "answer.chunk":
            payloads.append(
                _stream_chunk(
                    event.model_key or "unknown",
                    event.content or "",
                    **dict(event.meta or {}),
                )
            )
        elif event.kind == "answer.final":
            payloads.append(_stream_data("answer", answer=event.content or "", **dict(event.meta or {})))
        elif event.kind == "reference.set":
            payloads.append(_stream_data("reference", reference=list((event.meta or {}).get("references") or [])))
        elif event.kind == "clarification":
            payloads.append(_stream_data("clarification", clarification=(event.meta or {}).get("clarification")))
        elif event.kind == "error":
            payloads.append(_stream_data("error", **dict(event.meta or {})))
        elif event.kind == "done":
            payloads.append(_stream_data("status", status="done", **dict(event.meta or {})))
        return payloads

    def _pick_reference_value(reference: Dict[str, Any], doc: Dict[str, Any], *keys: str) -> Optional[str]:
        """reference/doc payload?? ??? ?? ?? ?? ???? ???."""
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
        """project source? pjt_id? reference.id? ????."""
        tag = str(reference.get("tag") or doc.get("tag") or "").strip()
        if tag == DataTag.PROJECT.value:
            return True
        return bool(_pick_reference_value(reference, doc, "pjt_id")) and not bool(
            _pick_reference_value(reference, doc, "rst_id")
        )

    def _resolve_reference_id(reference: Dict[str, Any], doc: Dict[str, Any]) -> Optional[str]:
        """reference id? source semantics? ?? canonical id? ????."""
        if _is_project_reference_source(reference, doc):
            return _pick_reference_value(reference, doc, "pjt_id")
        return _pick_reference_value(reference, doc, "rst_id")

    def _resolve_reference_title(reference: Dict[str, Any], doc: Dict[str, Any]) -> Optional[str]:
        """reference/doc payload?? ????? ??? ??? ???."""
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
        """context doc? API ??? reference payload? ?????."""
        mapped = rag_mapper.get_references(doc)
        if not isinstance(mapped, dict):
            mapped = {}
        result = dict(mapped)
        result["tag"] = str(result.get("tag") or doc.get("tag") or "").strip() or None
        result["id"] = _resolve_reference_id(result, doc)
        result["title"] = _resolve_reference_title(result, doc)
        return result

    def _validate_request_override_ranges(overrides: dict[str, Any]) -> None:
        """Reject malformed LLM override values before they reach provider backends."""
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

    # ??? ?? ????? ?? ?? ??? ?????? ???? ??
    def _build_request_overrides(payload: QueryRequest) -> dict[str, Any]:
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

        _validate_request_override_ranges(overrides)
        return overrides


    async def _runtime_status(request: Request) -> dict[str, Any]:
        """graph, kv, metrics ??? ??? health payload? ???."""
        kv_store = _get_kv_store(request)
        graph_ready = _get_graph(request) is not None
        metrics_ready = _get_metrics_http(request) is not None
        kv_connected = False
        if kv_store:
            kv_connected = await kv_store.ping()
        ready = graph_ready
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
        """index template? ??? ?? UI?, ??? ??? fallback HTML? ????."""
        if not template_index_path.exists():
            return HTMLResponse(
                content="<html><body><h3>NTIS RAG Chatbot</h3><p>index template unavailable.</p></body></html>",
                status_code=200,
            )
        return HTMLResponse(content=template_index_path.read_text(encoding="utf-8"), status_code=200)

    @app.post("/query/stream")
    async def query_stream(payload: QueryRequest, request: Request) -> StreamingResponse:
        """???? ??? ?? SSE endpoint?."""
        question = payload.question
        conversation_id = payload.conversation_id or str(uuid.uuid4())
        request_id = f"{conversation_id}-{uuid.uuid4().hex[:8]}"

        request_overrides = _build_request_overrides(payload)
        graph = _get_graph(request)

        async def event_generator():
            """graph event stream? chunk, status, reference, error ???? ?? ?????."""
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
            )
            request_started_at = time.perf_counter()
            emitter = AsyncStreamEmitter()
            final_state: Optional[Dict[str, Any]] = None
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
                    final_state = {"context": [], "merge_debug": {}, "selected_answer_meta": {}}
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
                            answer = str(output.get("answer_gemma") or output.get("answer_solar") or "").strip()
                            if answer:
                                for payload_line in _emit_legacy_stream_event(
                                    _next_route_event(kind="answer.chunk", model_key="gemma", content=answer, meta={})
                                ):
                                    yield payload_line
                                final_state["answer"] = answer
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
                    if "answer" in final_state and "answer_artifact" not in final_state:
                        final_state["answer_artifact"] = AnswerArtifact(
                            text=str(final_state.get("answer") or ""),
                            answer_kind="llm_collected",
                            stream_metrics={},
                            user_visible_final_required=True,
                            meta={"answer_source": (final_state.get("merge_debug", {}) or {}).get("selected_answer_source")},
                        )
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
                    question_analysis = final_state.get("question_analysis") if isinstance(final_state, dict) else None
                documents_used = final_state.get("context", []) if isinstance(final_state, dict) else []
                clarification_payload = final_state.get("clarification") if isinstance(final_state, dict) else None
                selected_answer_meta = final_state.get("selected_answer_meta", {}) if isinstance(final_state, dict) else {}
                merge_debug = final_state.get("merge_debug", {}) if isinstance(final_state, dict) else {}
                selected_artifact = final_state.get("answer_artifact") if isinstance(final_state, dict) else None
                if not isinstance(selected_artifact, AnswerArtifact):
                    answer_text = str(final_state.get("answer") or "") if isinstance(final_state, dict) else ""
                    selected_artifact = AnswerArtifact(
                        text=answer_text.strip(),
                        answer_kind="llm_collected",
                        stream_metrics=dict(selected_answer_meta or {}),
                        user_visible_final_required=True,
                        meta={"answer_source": merge_debug.get("selected_answer_source"), "model_key": merge_debug.get("selected_model")},
                    )

                if clarification_payload is None and isinstance(selected_artifact, AnswerArtifact) and selected_artifact.clarification is not None:
                    clarification_payload = {
                        "clarification_type": selected_artifact.clarification.clarification_type,
                        "message": selected_artifact.clarification.message,
                        "candidates": list(selected_artifact.clarification.candidates),
                        "resume_token": dict(selected_artifact.clarification.resume_token),
                    }

                if clarification_payload:
                    clarification_event = _next_route_event(
                        kind="clarification",
                        meta={"clarification": clarification_payload},
                    )
                    for payload_line in _emit_legacy_stream_event(clarification_event):
                        yield payload_line

                if selected_artifact.text and selected_artifact.user_visible_final_required:
                    final_event = _next_route_event(
                        kind="answer.final",
                        model_key=_normalize_stream_model_key(str(merge_debug.get("selected_model") or selected_artifact.meta.get("model_key") or "")),
                        content=selected_artifact.text,
                        meta=selected_artifact.to_meta_dict(),
                    )
                    for payload_line in _emit_legacy_stream_event(final_event):
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

                solar_done = final_state.get("answer_solar_meta", {}) if isinstance(final_state, dict) else {}
                gemma_done = final_state.get("answer_gemma_meta", {}) if isinstance(final_state, dict) else {}
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

    @app.post("/query/debug")
    async def query_debug(payload: QueryRequest, request: Request) -> Dict[str, Any]:
        """graph? ???? answer, question_analysis, strategy_summary? ??? JSON? ????."""
        question = payload.question
        conversation_id = payload.conversation_id or str(uuid.uuid4())
        request_id = f"{conversation_id}-{uuid.uuid4().hex[:8]}"
        request_overrides = _build_request_overrides(payload)
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

            question_analysis = final_state.get("question_analysis") if isinstance(final_state, dict) else None
            knowledge_sufficiency = final_state.get("knowledge_sufficiency") if isinstance(final_state, dict) else None
            strategy = final_state.get("strategy") if isinstance(final_state, dict) else None

            total_ms = compute_total_ms_from_start(request_started_at)
            log_event("REQ.END", request_id=request_id, conversation_id=conversation_id, stage="debug_done", total_ms=total_ms)
            messages = final_state.get("messages", []) if isinstance(final_state, dict) else []
            output_message = getattr(messages[-1], "content", "") if messages else ""

            merge_debug = final_state.get("merge_debug", {}) if isinstance(final_state, dict) else {}
            selected_answer_meta = final_state.get("selected_answer_meta", {}) if isinstance(final_state, dict) else {}

            return {
                "success": True,
                "conversation_id": conversation_id,
                "answer_gemma": final_state.get("answer_gemma") if isinstance(final_state, dict) else None,
                "answer_solar": final_state.get("answer_solar") if isinstance(final_state, dict) else None,
                "output_message": output_message,
                "question_analysis": _dump_model(question_analysis),
                "strategy_summary": strategy_spec_to_response(strategy),
                "knowledge_sufficiency": _dump_model(knowledge_sufficiency),
                "documents_used": len(final_state.get("context", []) if isinstance(final_state, dict) else []),
                "latencies": final_state.get("latencies", {}) if isinstance(final_state, dict) else {},
                "total_time": total_ms,
                "processing_strategy": getattr(knowledge_sufficiency, "requires_new_knowledge", None) if knowledge_sufficiency else "unknown",
                "clarification": final_state.get("clarification") if isinstance(final_state, dict) else None,
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
        """?? ?? ??? health payload ??? ?? ????."""
        payload = await _runtime_status(request)
        return JSONResponse(content=payload, status_code=200 if payload["ready"] else 503)

    @app.get("/health/details")
    async def health_details(request: Request) -> JSONResponse:
        """?? health payload???? ?? ??? 200 ??? ????."""
        payload = await _runtime_status(request)
        return JSONResponse(content=payload, status_code=200)

    @app.get("/metrics", response_model=MetricSnapshot, response_model_by_alias=True)
    async def get_metrics(request: Request) -> MetricSnapshot | JSONResponse:
        """metrics client? ???? ?? ???? ??? ????."""
        metrics_http = _get_metrics_http(request)
        if metrics_http is None:
            return JSONResponse(
                content={"error": "runtime_not_ready", "error_code": "RUNTIME_NOT_READY", "reason": "metrics client unavailable"},
                status_code=503,
            )
        return await collect_metrics_snapshot(metrics_http)

    @app.get("/metrics/stream")
    async def stream_metrics(request: Request) -> StreamingResponse:
        """???? ???? ?? ??? SSE? ?????."""
        metrics_http = _get_metrics_http(request)

        async def event_generator() -> Any:
            """graph event stream? chunk, status, reference, error ???? ?? ?????."""
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

