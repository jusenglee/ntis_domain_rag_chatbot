"""NTIS RAG 서버의 HTTP 라우트 계층.

이 모듈은 FastAPI의 요청/응답 계약(Contract)을 관리합니다.
비즈니스 로직은 workflow graph와 service helper에 위임하며,
`apps/api/main.py`에서 앱 구성 시 이 라우트들을 등록합니다.
"""

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
from apps.api.request_overrides import merge_request_overrides
from apps.api.streaming.contracts import AnswerArtifact, ErrorArtifact, StreamEvent
from apps.api.streaming.emitter import AsyncStreamEmitter
from apps.api.streaming.sse_encoder import encode_stream_event
from apps.platform.metrics import MetricSnapshot
from apps.platform.schemas import strategy_spec_to_response


class QueryRequest(BaseModel):
    """
    `/query/*` 계열 엔드포인트의 공통 요청 모델.
    사용자 질문과 대화 ID, 그리고 LLM/RAG 동작을 제어하기 위한 각종 파라미터를 포함합니다.
    """
    question: str = Field(..., description="사용자의 질문 텍스트")
    conversation_id: Optional[str] = Field(None, description="대화 세션을 식별하는 UUID")
    
    # LLM 생성 파라미터 오버라이드 (선택 사항)
    temperature: Optional[float] = Field(default=None, alias="Temperature")
    top_p: Optional[float] = Field(default=None, alias="Top-P")
    max_tokens: Optional[int] = Field(default=None, alias="Max-Token")
    top_k: Optional[int] = Field(default=None, alias="Top-K")

    # RAG 검색 알고리즘 파라미터 오버라이드
    rag_min_dense_score: Optional[float] = Field(default=None, alias="RAG_MIN_DENSE_SCORE")
    rag_topk_dense: Optional[int] = Field(default=None, alias="RAG_TOPK_DENSE")
    rag_w_lex: Optional[float] = Field(default=None, alias="RAG_W_LEX")
    rag_topk_lex_cand: Optional[int] = Field(default=None, alias="RAG_TOPK_LEX_CAND")

@dataclass(frozen=True)
class RouteDeps:
    """
    라우트 계층이 외부(Composition Root)에서 주입받는 의존성 묶음.
    전역 상태를 피하고 테스트 가능성을 높이기 위해 명시적으로 의존성을 관리합니다.
    """
    template_index_path: Any  # 인덱스 페이지 템플릿 경로
    logger: Any               # 표준 로거
    log_event: Any            # 운영 이벤트 로깅 함수
    is_debug_logging_enabled: Any  # 디버그 로그 활성화 여부 확인 함수
    mask_query_for_log: Any        # 로그 기록 시 질문 마스킹 함수
    extract_stream_chunk_text_and_field: Any # 스트림 청크에서 텍스트 추출
    is_hit_source: Any             # 검색 결과 소스 판별 함수
    derive_stream_error_code: Any  # 스트림 메타데이터 기반 에러 코드 추출
    compute_total_ms_from_start: Any # 경과 시간 계산 함수
    extract_contract_failure_details: Any # 계약 위반 상세 정보 파싱
    friendly_strategy_violation_message: Any # 사용자 친화적 에러 메시지 생성
    collect_metrics_snapshot: Any  # 메트릭 스냅샷 수집 함수
    metrics_stream_interval_seconds: float # 메트릭 스트리밍 주기
    set_log_context: Any           # 로그 컨텍스트(ID 등) 설정 함수
    rag_mapper: Any                # RAG 데이터 매퍼
    human_message: Any             # LangChain HumanMessage 생성자
    strategy_violation: Any        # 전략 위반 예외 클래스
    request_defaults_loader: Any = None # 기본 요청 파라미터 로더


def register_routes(app: FastAPI, deps: RouteDeps) -> None:
    """
    FastAPI 앱에 질의(Query), 상태(Health), 지표(Metrics) 관련 라우트를 등록합니다.
    
    Args:
        app: FastAPI 인스턴스
        deps: 주입받을 의존성 객체
    """
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
        """`app.state`에 등록된 컴파일된 workflow graph를 반환합니다."""
        return getattr(getattr(request.app, "state", None), "graph", None)

    def _get_metrics_http(request: Request) -> Any:
        """`app.state`에 등록된 metrics HTTP 클라이언트를 반환합니다."""
        return getattr(getattr(request.app, "state", None), "metrics_http", None)

    def _get_kv_store(request: Request) -> Any:
        """`app.state`에 등록된 대화 메모리용 KV store를 반환합니다."""
        return getattr(getattr(request.app, "state", None), "kv_store", None)

    def _dump_model(value: Any) -> Any:
        """Pydantic 모델이나 유사 객체를 JSON 직렬화 가능한 dict로 변환합니다."""
        if value is None:
            return None
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            return model_dump()
        return value

    def _state_get(state: Any, key: str, default: Any = None) -> Any:
        """상태 객체(dict 또는 object)에서 안전하게 값을 가져옵니다."""
        if state is None:
            return default
        if isinstance(state, dict):
            value = state.get(key, default)
        else:
            value = getattr(state, key, default)
        return default if value is None else value

    def _state_get_list(state: Any, key: str) -> list[Any]:
        """상태 객체에서 list 타입 값을 가져옵니다."""
        value = _state_get(state, key, [])
        return value if isinstance(value, list) else []

    def _state_get_dict(state: Any, key: str) -> dict[str, Any]:
        """상태 객체에서 dict 타입 값을 가져옵니다."""
        value = _dump_model(_state_get(state, key, {}))
        return dict(value) if isinstance(value, dict) else {}

    def _normalize_answer_kind(value: Any) -> str:
        """답변의 종류(kind)를 표준화된 문자열로 정규화합니다."""
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
        """
        워크플로우 실행 결과 상태에서 최종 답변 아티팩트를 조립합니다.
        스트리밍 메트릭, 답변 소스, 매니페스트 등을 포함합니다.
        """
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
        view_state = _state_get(state, "view_state")
        
        # 답변 매니페스트(프론트엔드 렌더링용 구조화 데이터) 추출 로직
        publication_payload = _dump_model(selected_answer_meta.get("visible_answer_manifest_publication"))
        visible_answer_manifest_publication = dict(publication_payload) if isinstance(publication_payload, dict) else None
        visible_answer_manifest = None
        
        if isinstance(visible_answer_manifest_publication, dict):
            publication_status = str(visible_answer_manifest_publication.get("publication_status") or "").strip().lower()
            published_manifest = visible_answer_manifest_publication.get("published_manifest")
            if publication_status == "approved" and isinstance(published_manifest, dict):
                visible_answer_manifest = dict(published_manifest)
            elif publication_status == "approved" and isinstance(selected_answer_meta.get("visible_answer_manifest"), dict):
                visible_answer_manifest = dict(selected_answer_meta.get("visible_answer_manifest") or {})
        else:
            visible_answer_manifest = selected_answer_meta.get("visible_answer_manifest")
            
        if visible_answer_manifest_publication is None and not isinstance(visible_answer_manifest, dict):
            snapshot = getattr(view_state, "visible_answer_manifest", None)
            if snapshot is not None:
                visible_answer_manifest = snapshot.model_dump() if hasattr(snapshot, "model_dump") else dict(snapshot)
                
        return AnswerArtifact(
            text=final_text,
            answer_kind=_normalize_answer_kind(
                selected_answer_meta.get("answer_kind") or merge_debug.get("selected_answer_kind")
            ),
            stream_metrics=selected_answer_meta,
            user_visible_final_required=bool(selected_answer_meta.get("user_visible_final_required", True)),
            visible_answer_manifest=visible_answer_manifest if isinstance(visible_answer_manifest, dict) else None,
            visible_answer_manifest_publication=visible_answer_manifest_publication,
            meta={
                "answer_source": merge_debug.get("selected_answer_source"),
                "model_key": merge_debug.get("selected_model"),
            },
        )

    def _build_clarification_payload_from_artifact(artifact: AnswerArtifact) -> Optional[dict[str, Any]]:
        """답변 아티팩트에서 명확화(Clarification) 요청 페이로드를 추출합니다."""
        clarification = getattr(artifact, "clarification", None)
        if clarification is None:
            return None
        return {
            "clarification_type": clarification.clarification_type,
            "message": clarification.message,
            "candidates": list(clarification.candidates),
            "resume_token": dict(clarification.resume_token),
        }

    def _normalize_clarification_payload(
            state: Any,
            *,
            selected_artifact: Optional[AnswerArtifact] = None,
    ) -> Optional[dict[str, Any]]:
        """상태 객체나 아티팩트로부터 명확화 페이로드를 찾아 정규화합니다."""
        clarification_payload = _dump_model(_state_get(state, "clarification"))
        if not isinstance(clarification_payload, dict):
            clarification_payload = None
        if clarification_payload is None:
            retrieval_bundle = _state_get(state, "retrieval_bundle")
            bundle_clarification = _dump_model(getattr(retrieval_bundle, "clarification", None))
            if isinstance(bundle_clarification, dict):
                clarification_payload = bundle_clarification
        if clarification_payload is None and isinstance(selected_artifact, AnswerArtifact):
            clarification_payload = _build_clarification_payload_from_artifact(selected_artifact)
        if clarification_payload is None:
            return None

        normalized = dict(clarification_payload)
        message = str(normalized.get("message") or "").strip()
        if not message and isinstance(selected_artifact, AnswerArtifact):
            message = str(selected_artifact.text or "").strip()
        candidates = normalized.get("candidates")
        normalized["candidates"] = list(candidates) if isinstance(candidates, list) else []
        resume_token = normalized.get("resume_token")
        normalized["resume_token"] = dict(resume_token) if isinstance(resume_token, dict) else {}
        if message:
            normalized["message"] = message
        return normalized

    def _terminal_done_meta(
            *,
            artifact: Optional[AnswerArtifact] = None,
            clarification_payload: Optional[dict[str, Any]] = None,
            output_message: Optional[str] = None,
            error: Optional[bool] = None,
            error_code: Optional[str] = None,
            reason: Optional[str] = None,
            degraded: Optional[bool] = None,
            extra_meta: Optional[Dict[str, Any]] = None,
    ) -> dict[str, Any]:
        meta = artifact.to_meta_dict() if isinstance(artifact, AnswerArtifact) else {}
        if isinstance(clarification_payload, dict):
            meta["clarification"] = dict(clarification_payload)
            meta.setdefault("answer_kind", "clarification")
            meta.setdefault("user_visible_final_required", True)
        if output_message:
            meta["output_message"] = output_message
        if error is not None:
            meta["error"] = bool(error)
        if error_code:
            meta["error_code"] = error_code
        if reason:
            meta["reason"] = reason
        if degraded is not None:
            meta["degraded"] = bool(degraded)
        if isinstance(extra_meta, dict):
            meta.update(extra_meta)
        return meta

    def _normalize_stream_model_key(model_key: str) -> str:
        """모델 키 값을 표준 소문자 식별자로 변환합니다."""
        normalized = str(model_key or "").strip().lower()
        if normalized in {"solar", "upstage"}:
            return "solar"
        if normalized == "gemma":
            return "gemma"
        return normalized

    def _resolve_stream_model_label(model_key: str) -> str:
        """스트림 표시용 모델 라벨(대문자 등)을 결정합니다."""
        normalized = _normalize_stream_model_key(model_key)
        if normalized == "solar":
            return "UPSTAGE"
        if normalized == "gemma":
            return "GEMMA"
        return normalized.upper() or "UNKNOWN"

    def _emit_stream_event_lines(event: StreamEvent) -> list[str]:
        """표준 StreamEvent 객체를 SSE 라인 리스트로 변환합니다."""
        return [encode_stream_event(event)]

    def _top_level_text_value(doc: Dict[str, Any], *keys: str) -> Optional[str]:
        """딕셔너리의 여러 키 중 첫 번째로 발견되는 유효한 문자열 값을 반환합니다."""
        for key in keys:
            text = str(doc.get(key) or "").strip()
            if text:
                return text
        return None

    def _reference_tag_from_source_type(source_type: Any) -> Optional[str]:
        """데이터 소스 타입에 따른 NTIS 표준 태그를 매핑합니다."""
        normalized = str(source_type or "").strip().lower()
        mapping = {
            "project": DataTag.PROJECT.value,
            "paper": DataTag.PAPER.value,
            "patent": DataTag.PATENT.value,
            "report": DataTag.REPORT.value,
            "software": DataTag.SOFTWARE.value,
            "standard": DataTag.STANDARD.value,
            "compound": DataTag.COMPOUND.value,
            "equipment": DataTag.EQUIPMENT.value,
            "organism_info": DataTag.ORGANISM_INFO.value,
            "organism_resource": DataTag.ORGANISM_RESOURCE.value,
            "manual": DataTag.MANUAL.value,
            "tech_summary": DataTag.TECH_SUMMARY.value,
            "variety": DataTag.VARIETY.value,
            "qna": DataTag.QNA.value,
        }
        return mapping.get(normalized)

    def _reference_payload_from_doc(doc: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """검색 결과 문서로부터 프론트엔드 참조 목록에 표시할 최소 페이로드를 추출합니다."""
        tag = _top_level_text_value(doc, "tag", "source_table")
        if tag == DataTag.PROJECT.value:
            reference_id = _top_level_text_value(doc, "pjt_id", "id", "doc_id", "rst_id")
        else:
            reference_id = _top_level_text_value(doc, "rst_id", "pjt_id", "id", "doc_id")
        return {
            "tag": tag,
            "id": reference_id,
            "title": _top_level_text_value(doc, "title1", "title2", "title_text", "title"),
        }

    def _reference_payload_invalid_reason(payload: Dict[str, Optional[str]]) -> Optional[str]:
        """참조 페이로드가 필수 필드를 모두 갖추었는지 검증합니다."""
        if not payload.get("tag"):
            return "missing_tag"
        if not payload.get("id"):
            return "missing_project_or_result_id"
        if not payload.get("title"):
            return "missing_title"
        return None

    def _json_safe_reference_value(value: Any) -> Any:
        """Return a JSON-serializable copy of a reference candidate value."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(key): _json_safe_reference_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe_reference_value(item) for item in value]
        return str(value)

    def _invalid_reference_payload(
        candidate: Dict[str, Any],
        *,
        candidate_source: str,
        invalid_reason: str,
    ) -> Dict[str, Any]:
        payload = _json_safe_reference_value(candidate)
        if not isinstance(payload, dict):
            payload = {"raw_reference": payload}
        payload["invalid"] = True
        payload["invalid_reason"] = invalid_reason
        payload["candidate_source"] = candidate_source
        return payload

    def _canonical_evidence_to_reference_doc(item: Dict[str, Any]) -> Dict[str, Any]:
        """내부 증거 객체를 라우트 참조 계약에 맞는 최상위 문서 형식으로 변환합니다."""
        if not isinstance(item, dict):
            return {}
        ids = item.get("ids")
        facts = item.get("facts")
        evidence = item.get("evidence")
        source_type = _top_level_text_value(item, "source_type")

        doc: Dict[str, Any] = {}
        if source_type:
            doc["source_type"] = source_type

        if isinstance(ids, dict):
            pjt_id = _top_level_text_value(ids, "pjt_id")
            rst_id = _top_level_text_value(ids, "rst_id")
            if pjt_id:
                doc["pjt_id"] = pjt_id
            if rst_id:
                doc["rst_id"] = rst_id
        if "pjt_id" not in doc:
            pjt_id = _top_level_text_value(item, "pjt_id")
            if pjt_id:
                doc["pjt_id"] = pjt_id
        if "rst_id" not in doc:
            rst_id = _top_level_text_value(item, "rst_id")
            if rst_id:
                doc["rst_id"] = rst_id

        tag = _top_level_text_value(item, "tag") or _reference_tag_from_source_type(source_type)
        if not tag and _top_level_text_value(doc, "pjt_id"):
            tag = DataTag.PROJECT.value
        if tag:
            doc["tag"] = tag

        if isinstance(evidence, dict):
            for key in ("title1", "title2", "title_text"):
                value = _top_level_text_value(evidence, key)
                if value:
                    doc[key] = value
        for key in ("title1", "title2", "title_text"):
            if key not in doc:
                value = _top_level_text_value(item, key)
                if value:
                    doc[key] = value
        if not any(_top_level_text_value(doc, key) for key in ("title1", "title2", "title_text")):
            title = None
            if isinstance(facts, dict):
                title = _top_level_text_value(facts, "title")
            title = title or _top_level_text_value(item, "title")
            if title:
                doc["title_text"] = title
        return doc

    def _validate_request_override_ranges(overrides: dict[str, Any]) -> None:
        """LLM/RAG 파라미터 값이 유효 범위를 벗어나는지 검증합니다."""
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
        """요청 페이로드에서 명시적으로 제공된 오버라이드 값만 추출합니다."""
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

    async def _build_request_overrides(
            payload: QueryRequest,
            *,
            request_id: str,
            conversation_id: str,
            route_name: str,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """사용자 요청 값과 시스템 기본값(Oracle)을 병합하여 최종 설정을 생성합니다."""
        request_values = _extract_request_override_values(payload)
        oracle_defaults: dict[str, Any] = {}
        supported_override_count = 8
        oracle_lookup_meta: dict[str, Any] = {
            "oracle_lookup_status": "disabled" if request_defaults_loader is None else "not_needed",
            "oracle_lookup_attempted": False,
            "oracle_loaded_key_count": 0,
            "oracle_loaded_keys": [],
        }
        
        # 모든 값이 요청에 포함되지 않은 경우에만 Oracle에서 기본값을 가져옵니다.
        if request_defaults_loader is not None and len(request_values) < supported_override_count:
            load_defaults_with_meta = getattr(request_defaults_loader, "load_defaults_with_meta", None)
            load_defaults = getattr(request_defaults_loader, "load_defaults", None)
            if callable(load_defaults_with_meta):
                loaded, lookup_meta = await load_defaults_with_meta()
                if isinstance(loaded, dict):
                    oracle_defaults = dict(loaded)
                if isinstance(lookup_meta, dict):
                    oracle_lookup_meta.update(dict(lookup_meta))
            elif callable(load_defaults):
                loaded = await load_defaults()
                if isinstance(loaded, dict):
                    oracle_defaults = dict(loaded)
                oracle_lookup_meta.update(
                    {
                        "oracle_lookup_status": "loaded" if oracle_defaults else "empty",
                        "oracle_lookup_attempted": True,
                        "oracle_loaded_key_count": len(oracle_defaults),
                        "oracle_loaded_keys": sorted(oracle_defaults.keys()),
                        "oracle_lookup_loader": "legacy",
                    }
                )
        elif request_defaults_loader is not None:
            oracle_lookup_meta.update({"oracle_lookup_status": "skipped_all_request_values_present"})
            
        merged, sources = merge_request_overrides(
            request_values=request_values,
            oracle_defaults=oracle_defaults,
        )
        source_counts = {
            source_name: sum(1 for current_source in sources.values() if current_source == source_name)
            for source_name in sorted(set(sources.values()))
        }
        
        # 운영 로그: 요청 파라미터 출처 및 병합 결과 기록
        log_event(
            "REQ.ORACLE.DEFAULTS",
            request_id=request_id,
            conversation_id=conversation_id,
            stage="request_oracle_defaults",
            route=route_name,
            request_value_count=len(request_values),
            oracle_key_count=len(oracle_defaults),
            merged_key_count=len(merged),
            request_override_source_counts=source_counts or None,
            **oracle_lookup_meta,
        )
        _validate_request_override_ranges(merged)
        return merged, sources


    async def _runtime_status(request: Request) -> dict[str, Any]:
        """워크플로우 엔진 및 데이터베이스 연결 상태를 확인하여 반환합니다."""
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
        """기본 웹 UI(index.html)를 서빙합니다."""
        if not template_index_path.exists():
            return HTMLResponse(
                content="<html><body><h3>NTIS RAG Chatbot</h3><p>index template unavailable.</p></body></html>",
                status_code=200,
            )
        return HTMLResponse(content=template_index_path.read_text(encoding="utf-8"), status_code=200)

    @app.post("/query/stream")
    async def query_stream(payload: QueryRequest, request: Request) -> StreamingResponse:
        """
        질의 처리 결과를 SSE(Server-Sent Events)로 실시간 스트리밍합니다.
        가장 핵심적인 엔드포인트입니다.
        """
        question = payload.question
        conversation_id = payload.conversation_id or str(uuid.uuid4())
        request_id = f"{conversation_id}-{uuid.uuid4().hex[:8]}"

        # 1. 설정 병합 및 초기화
        request_overrides, request_override_sources = await _build_request_overrides(
            payload,
            request_id=request_id,
            conversation_id=conversation_id,
            route_name="/query/stream",
        )
        graph = _get_graph(request)

        async def event_generator():
            """그래프 실행 중 발생하는 이벤트를 SSE 형식으로 변환하여 생성하는 제너레이터입니다."""
            route_seq = 0

            def _next_route_event(
                *,
                kind: str,
                model_key: Optional[str] = None,
                content: Optional[str] = None,
                references: Optional[list[dict[str, Any]]] = None,
                meta: Optional[Dict[str, Any]] = None,
            ) -> StreamEvent:
                """다음 순번의 스트림 이벤트 객체를 생성합니다."""
                nonlocal route_seq
                route_seq += 1

                # SSE envelope 정합성 보장 (L1/L2 공통 규약 반영)
                if kind != "reference.set":
                    effective_references = None
                else:
                    effective_references = list(references or [])

                return StreamEvent(
                    kind=kind,
                    request_id=request_id,
                    seq=route_seq,
                    model_key=model_key,
                    content=content,
                    references=effective_references,
                    meta=dict(meta or {}),
                )

            # 엔진 준비 미비 시 즉시 에러 반환
            if graph is None:
                runtime_not_ready_message = "시스템이 준비되지 않았습니다. 잠시 후 다시 시도해 주세요."
                runtime_not_ready_artifact = AnswerArtifact(
                    text=runtime_not_ready_message,
                    answer_kind="error",
                    stream_metrics={},
                    user_visible_final_required=True,
                    error=ErrorArtifact(
                        error_code="RUNTIME_NOT_READY",
                        reason="compiled graph unavailable",
                        retryable=True,
                    ),
                    meta={"answer_source": "route_runtime_not_ready"},
                )
                for payload_line in _emit_stream_event_lines(
                    _next_route_event(kind="reference.set", content="null", references=[], meta={"references": []})
                ):
                    yield payload_line
                for payload_line in _emit_stream_event_lines(
                    _next_route_event(
                        kind="done",
                        meta=_terminal_done_meta(
                            artifact=runtime_not_ready_artifact,
                            output_message=runtime_not_ready_message,
                            error=True,
                            error_code="RUNTIME_NOT_READY",
                            reason="compiled graph unavailable",
                        ),
                    )
                ):
                    yield payload_line
                return

            # 대화 ID 전송
            for payload_line in _emit_stream_event_lines(
                _next_route_event(
                    kind="conversation",
                    content=conversation_id,
                    meta={"conversation_id": conversation_id},
                )
            ):
                yield payload_line

            # 운영 로그: 요청 시작 알림
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

                # 2. 워크플로우 실행 (LangGraph astream_events 또는 ainvoke)
                if not hasattr(graph, "ainvoke") and hasattr(graph, "astream_events"):
                    # 스트리밍 방식 실행
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
                        
                        # 각 노드 실행 완료 후 상태 업데이트 및 청크 스트리밍
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
                            final_state[answer_key] = str(output.get(answer_key) or "").strip()
                            final_state[answer_meta_key] = dict(output.get(answer_meta_key) or {})
                            if isinstance(output.get(artifact_key), AnswerArtifact):
                                final_state[artifact_key] = output.get(artifact_key)
                        elif kind == "on_chain_end" and node == "merge_answers":
                            output = data.get("output", {}) or {}
                            final_state.update(output)
                        elif kind == "on_chain_end" and node == "analyze_question":
                            question_analysis = data.get("output", {})
                        elif kind == "on_chat_model_stream" and node in {"generate_answer_solar", "generate_answer_gemma"}:
                            chunk = data.get("chunk")
                            chunk_text, stream_field = extract_stream_chunk_text_and_field(chunk)
                            if stream_field == "reasoning" or not chunk_text:
                                continue
                            model_key = "solar" if node == "generate_answer_solar" else "gemma"
                            for payload_line in _emit_stream_event_lines(
                                _next_route_event(kind="answer.chunk", model_key=model_key, content=chunk_text, meta={})
                            ):
                                yield payload_line
                    final_state = dict(final_state or {})
                else:
                    # 일괄 실행 방식 + emitter를 통한 내부 스트리밍
                    graph_task = asyncio.create_task(graph.ainvoke(inputs))
                    for payload_line in _emit_stream_event_lines(_next_route_event(kind="status", meta={"status": "retrieve"})):
                        yield payload_line

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
                        for payload_line in _emit_stream_event_lines(emitted_event):
                            yield payload_line

                    final_state = await graph_task
                    question_analysis = _state_get(final_state, "question_analysis")

                # 3. 답변 및 참조 목록 후처리
                documents_used = _state_get_list(final_state, "context")
                merge_debug = _state_get_dict(final_state, "merge_debug")
                selected_artifact = _build_final_answer_artifact(final_state)
                clarification_payload = _normalize_clarification_payload(
                    final_state,
                    selected_artifact=selected_artifact,
                )

                user_visible_terminal_emitted = False
                terminal_done_meta: dict[str, Any] = {}

                # 명확화 요청이 있는 경우 처리
                if clarification_payload:
                    clarification_message = str(clarification_payload.get("message") or "").strip()
                    terminal_done_meta = _terminal_done_meta(
                        artifact=selected_artifact if isinstance(selected_artifact, AnswerArtifact) else None,
                        clarification_payload=clarification_payload,
                        output_message=clarification_message or None,
                    )
                    user_visible_terminal_emitted = True

                # 최종 답변 텍스트 전송
                if not clarification_payload and isinstance(selected_artifact, AnswerArtifact) and selected_artifact.text and selected_artifact.user_visible_final_required:
                    selected_answer_kind = _normalize_answer_kind(selected_artifact.answer_kind)
                    if selected_answer_kind not in {"llm_streamed", "error", "no_result"}:
                        model_key = _normalize_stream_model_key(
                            str(merge_debug.get("selected_model") or selected_artifact.meta.get("model_key") or "solar")
                        )
                        for payload_line in _emit_stream_event_lines(
                            _next_route_event(kind="answer.chunk", model_key=model_key, content=selected_artifact.text)
                        ):
                            yield payload_line

                    terminal_done_meta = _terminal_done_meta(
                        artifact=selected_artifact,
                        output_message=selected_artifact.text if selected_answer_kind in {"error", "no_result"} else None,
                    )
                    user_visible_terminal_emitted = True

                # 답변 누락 방지 가드 (Guard)
                if not user_visible_terminal_emitted:
                    guard_reason = "route completed without final_answer_artifact/final_answer_text/clarification"
                    guard_message = "응답 생성은 완료되었지만 표시할 최종 답변이 비어 있습니다. 다시 시도해 주세요."
                    guard_artifact = AnswerArtifact(
                        text=guard_message,
                        answer_kind="error",
                        stream_metrics={},
                        user_visible_final_required=True,
                        error=ErrorArtifact(error_code="MISSING_FINAL_ANSWER", reason=guard_reason, retryable=True),
                        meta={"answer_source": "route_contract_guard", "degraded": True},
                    )
                    terminal_done_meta = _terminal_done_meta(
                        artifact=guard_artifact,
                        output_message=guard_message,
                        error_code="MISSING_FINAL_ANSWER",
                        reason=guard_reason,
                        degraded=True,
                    )

                # 4. 참조 리스트 조립 및 전송
                ref_docs = []
                seen_reference_keys = set()
                invalid_candidate_count = 0
                fallback_used = "none"
                artifact_references = list(getattr(selected_artifact, "references", []) or [])
                artifact_reference_docs = [ref for ref in artifact_references if isinstance(ref, dict)]
                artifact_candidate_count = len(artifact_reference_docs)
                canonical_evidence = _state_get_list(final_state, "canonical_evidence")
                canonical_reference_docs = [
                    _canonical_evidence_to_reference_doc(item)
                    for item in canonical_evidence
                    if isinstance(item, dict)
                ]
                canonical_reference_docs = [doc for doc in canonical_reference_docs if doc]
                canonical_candidate_count = len(canonical_reference_docs)
                fallback_docs = [doc for doc in documents_used if is_hit_source(doc) and isinstance(doc, dict)]
                documents_used_candidate_count = len(fallback_docs)

                def _append_reference_docs(candidates: list[dict[str, Any]], *, candidate_source: str) -> int:
                    """유효한 참조 문서를 목록에 추가합니다."""
                    nonlocal invalid_candidate_count
                    added_count = 0
                    for candidate in candidates:
                        normalized = _reference_payload_from_doc(candidate)
                        invalid_reason = _reference_payload_invalid_reason(normalized)
                        if invalid_reason is not None:
                            invalid_candidate_count += 1
                            # 유효하지 않은 참조 데이터 로깅
                            log_event(
                                "REFERENCE.INVALID_PAYLOAD",
                                request_id=request_id,
                                conversation_id=conversation_id,
                                stage="stream_finalization",
                                candidate_source=candidate_source,
                                invalid_reason=invalid_reason,
                                raw_tag=candidate.get("tag"),
                                raw_source_table=candidate.get("source_table"),
                                source_type=candidate.get("source_type"),
                                top_level_pjt_id=candidate.get("pjt_id"),
                                top_level_rst_id=candidate.get("rst_id"),
                                top_level_doc_id=candidate.get("doc_id"),
                                top_level_title1=candidate.get("title1"),
                                top_level_title2=candidate.get("title2"),
                                top_level_title_text=candidate.get("title_text"),
                                keys=sorted(candidate.keys()),
                            )
                            invalid_payload = _invalid_reference_payload(
                                candidate,
                                candidate_source=candidate_source,
                                invalid_reason=invalid_reason,
                            )
                            dedupe_key = (
                                "invalid",
                                candidate_source,
                                invalid_reason,
                                json.dumps(invalid_payload, ensure_ascii=False, sort_keys=True),
                            )
                            if dedupe_key in seen_reference_keys:
                                continue
                            seen_reference_keys.add(dedupe_key)
                            ref_docs.append(invalid_payload)
                            added_count += 1
                            continue
                        dedupe_key = ("valid", normalized.get("tag"), normalized.get("id"), normalized.get("title"))
                        if dedupe_key in seen_reference_keys:
                            continue
                        seen_reference_keys.add(dedupe_key)
                        ref_docs.append(normalized)
                        added_count += 1
                    return added_count

                # 참조 목록을 가져오는 계층적 전략 (Artifact -> Evidence -> Context)
                _append_reference_docs(artifact_reference_docs, candidate_source="artifact_references")

                if not ref_docs:
                    fallback_used = "canonical_evidence"
                    _append_reference_docs(canonical_reference_docs, candidate_source="canonical_evidence")

                if not ref_docs:
                    fallback_used = "documents_used"
                    _append_reference_docs(fallback_docs, candidate_source="documents_used")

                # 운영 로그: 참조 목록 구성 결과 기록
                log_event(
                    "REFERENCE.FINALIZATION",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    stage="stream_finalization",
                    artifact_candidate_count=artifact_candidate_count,
                    canonical_candidate_count=canonical_candidate_count,
                    documents_used_candidate_count=documents_used_candidate_count,
                    invalid_candidate_count=invalid_candidate_count,
                    emitted_reference_count=len(ref_docs),
                    fallback_used=fallback_used if ref_docs else "none",
                )

                # 최종 참조 리스트 전송
                ref_event = _next_route_event(
                    kind="reference.set",
                    content="null",
                    references=ref_docs,
                    meta={"references": ref_docs},
                )
                for payload_line in _emit_stream_event_lines(ref_event):
                    yield payload_line

                # 5. 스트림 완료 및 종료 로그
                solar_done = _state_get_dict(final_state, "answer_solar_meta")
                gemma_done = _state_get_dict(final_state, "answer_gemma_meta")
                log_event(
                    "STREAM.DONE",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    stage="stream_done",
                    solar_error_code=derive_stream_error_code(solar_done),
                    solar_elapsed_ms=solar_done.get("elapsed_ms"),
                    gemma_error_code=derive_stream_error_code(gemma_done),
                    gemma_elapsed_ms=gemma_done.get("elapsed_ms"),
                )
                done_event = _next_route_event(kind="done", meta=terminal_done_meta)
                for payload_line in _emit_stream_event_lines(done_event):
                    yield payload_line

            except Exception as exc:
                # 예외 처리 및 에러 SSE 전송
                logger.error("Stream Error: {}", exc, exc_info=True)
                error_code = getattr(exc, "error_code", "INTERNAL_ERROR")
                reason = getattr(exc, "reason", str(exc))
                total_ms = compute_total_ms_from_start(request_started_at)
                degraded = isinstance(exc, strategy_violation)
                
                # 운영 로그: 요청 에러 기록
                log_event(
                    "REQ.ERROR",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    stage="stream",
                    error_code=error_code,
                    reason=reason,
                    degraded=int(degraded),
                    total_ms=total_ms,
                    **extract_contract_failure_details(reason),
                )
                
                # 전략 위반(차단 정책 등) 시 사용자 친화적 메시지 전송
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
                    for payload_line in _emit_stream_event_lines(
                        _next_route_event(kind="reference.set", content="null", references=[], meta={"references": []})
                    ):
                        yield payload_line
                    for payload_line in _emit_stream_event_lines(
                        _next_route_event(
                            kind="done",
                            meta=_terminal_done_meta(
                                artifact=error_artifact,
                                output_message=user_message,
                                error_code=error_code,
                                reason=reason,
                                degraded=True,
                            ),
                        )
                    ):
                        yield payload_line
                    return
                
                # 일반 시스템 오류 전송
                error_message = "시스템 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
                error_artifact = AnswerArtifact(
                    text=error_message,
                    answer_kind="error",
                    stream_metrics={},
                    user_visible_final_required=True,
                    error=ErrorArtifact(error_code=error_code, reason=reason),
                    meta={"answer_source": "route_exception"},
                )
                for payload_line in _emit_stream_event_lines(
                    _next_route_event(kind="reference.set", content="null", references=[], meta={"references": []})
                ):
                    yield payload_line
                for payload_line in _emit_stream_event_lines(
                    _next_route_event(
                        kind="done",
                        meta=_terminal_done_meta(
                            artifact=error_artifact,
                            output_message=error_message,
                            error=True,
                            error_code=error_code,
                            reason=reason,
                            extra_meta={"error_detail": str(exc)},
                        ),
                    )
                ):
                    yield payload_line
                return
            finally:
                await emitter.close()
                set_log_context(request_id=None, conversation_id=None)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 디버그 라우트 활성화 여부
    debug_routes_enabled = os.getenv("ENABLE_DEBUG_ROUTES", "false").strip().lower() in {"1", "true", "yes", "on"}

    @app.post("/query/debug")
    async def query_debug(payload: QueryRequest, request: Request) -> Dict[str, Any]:
        """디버그용 엔드포인트: 워크플로우 실행 결과(전체 상태)를 JSON으로 반환합니다."""
        if not debug_routes_enabled:
            raise HTTPException(status_code=404, detail="not found")
            
        question = payload.question
        conversation_id = payload.conversation_id or str(uuid.uuid4())
        request_id = f"{conversation_id}-{uuid.uuid4().hex[:8]}"
        
        request_overrides, request_override_sources = await _build_request_overrides(
            payload,
            request_id=request_id,
            conversation_id=conversation_id,
            route_name="/query/debug",
        )
        graph = _get_graph(request)

        if graph is None:
            return {
                "success": False,
                "error": "runtime_not_ready",
                "error_code": "RUNTIME_NOT_READY",
                "reason": "compiled graph unavailable",
            }

        request_started_at = time.perf_counter()
        try:
            set_log_context(request_id=request_id, conversation_id=conversation_id)
            log_event(
                "REQ.START",
                request_id=request_id,
                conversation_id=conversation_id,
                stage="debug_request_start",
                q_len=len(question),
                request_overrides=request_overrides or None,
                request_override_sources=request_override_sources or None,
            )
            
            inputs = {
                "conversation_id": conversation_id,
                "request_id": request_id,
                "request_started_at": request_started_at,
                "messages": [human_message(content=question)],
                "kv_store": _get_kv_store(request),
                "request_overrides": request_overrides,
            }

            # 워크플로우 일괄 실행
            final_state = await graph.ainvoke(inputs)

            total_ms = compute_total_ms_from_start(request_started_at)
            log_event("REQ.END", request_id=request_id, conversation_id=conversation_id, stage="debug_done", total_ms=total_ms)
            
            # 결과 조립
            selected_artifact = _build_final_answer_artifact(final_state)
            return {
                "success": True,
                "conversation_id": conversation_id,
                "answer_gemma": _state_get(final_state, "answer_gemma"),
                "answer_solar": _state_get(final_state, "answer_solar"),
                "output_message": str(_state_get(final_state, "final_answer_text", "")).strip(),
                "final_answer_meta": (selected_artifact.to_meta_dict() if isinstance(selected_artifact, AnswerArtifact) else None),
                "question_analysis": _dump_model(_state_get(final_state, "question_analysis")),
                "strategy_summary": strategy_spec_to_response(_state_get(final_state, "strategy")),
                "documents_used": len(_state_get_list(final_state, "context")),
                "latencies": _state_get_dict(final_state, "latencies"),
                "total_time": total_ms,
                "clarification": _normalize_clarification_payload(final_state, selected_artifact=selected_artifact),
                "merge_debug": _state_get_dict(final_state, "merge_debug"),
            }

        except Exception as exc:
            logger.error("Debug Error: {}", exc, exc_info=True)
            total_ms = compute_total_ms_from_start(request_started_at)
            error_code = getattr(exc, "error_code", "INTERNAL_ERROR")
            log_event(
                "REQ.ERROR",
                request_id=request_id,
                conversation_id=conversation_id,
                stage="debug",
                error_type=type(exc).__name__,
                error_code=error_code,
                reason=str(exc),
                total_ms=total_ms,
            )
            return {
                "success": False,
                "error": str(exc),
                "error_code": error_code,
                "reason": str(exc),
            }
        finally:
            set_log_context(request_id=None, conversation_id=None)

    @app.get("/health")
    async def health_check(request: Request) -> JSONResponse:
        """L7 로드밸런서용 기본 헬스체크. 엔진 미준비 시 503을 반환합니다."""
        payload = await _runtime_status(request)
        return JSONResponse(content=payload, status_code=200 if payload["ready"] else 503)

    @app.get("/health/details")
    async def health_details(request: Request) -> JSONResponse:
        """상세 헬스 상태 정보를 항상 200으로 반환합니다."""
        payload = await _runtime_status(request)
        return JSONResponse(content=payload, status_code=200)

    @app.get("/metrics", response_model=MetricSnapshot, response_model_by_alias=True)
    async def get_metrics(request: Request) -> MetricSnapshot | JSONResponse:
        """현재 시스템의 메트릭 스냅샷을 반환합니다."""
        metrics_http = _get_metrics_http(request)
        if metrics_http is None:
            return JSONResponse(
                content={"error": "runtime_not_ready", "error_code": "RUNTIME_NOT_READY", "reason": "metrics client unavailable"},
                status_code=503,
            )
        return await collect_metrics_snapshot(metrics_http)

    @app.get("/metrics/stream")
    async def stream_metrics(request: Request) -> StreamingResponse:
        """메트릭 스냅샷을 SSE로 주기적으로 스트리밍합니다."""
        metrics_http = _get_metrics_http(request)

        async def event_generator() -> Any:
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
