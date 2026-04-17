from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import re
import time
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from apps.api.contracts.workflow_models import measure_latency as measure_latency_impl

from apps.retrieval.rag_runtime_observability import get_code_fingerprint_fields

_CONTRACT_REASON_PATTERN = re.compile(
    r"reason=(?P<contract_fail_reason>[a-z_]+),\s*"
    r"reranked=(?P<reranked_count>\d+),\s*"
    r"min_reranked=(?P<min_reranked>\d+)"
    r"(?:,\s*empty_result_policy=(?P<empty_result_policy>[a-z_]+))?"
)


def sha256_file(path: Path) -> str:
    """파일 내용의 sha256 digest를 계산한다.
    배포 산출물이나 prompt template의 변경 여부를 가볍게 비교할 때 쓴다.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup_file_logging(*, logger_name: str, log_path: str = "logs/app.log") -> logging.Logger:
    """파일과 stderr에 동시 기록하는 애플리케이션 logger를 초기화한다.
    rotating file handler, stream handler, vLLM/OpenAI compat logger level을 한 지점에서 맞추는 운영 헬퍼다.
    """
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    app_logger = logging.getLogger(logger_name)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=50 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    fh.setLevel(logging.INFO)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)

    app_logger.handlers.clear()
    app_logger.addHandler(fh)
    app_logger.addHandler(sh)

    vllm_client_level = os.getenv("VLLM_CLIENT_LOG_LEVEL", "INFO").upper()
    logging.getLogger("openai_compat_llm").setLevel(getattr(logging, vllm_client_level, logging.INFO))
    return app_logger


logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

logger = setup_file_logging(logger_name="Chatbot_Server")
measure_latency = partial(measure_latency_impl, logger_obj=logger)
_SHORT_ANSWER_MAX_TOKENS_HINT = int(os.getenv("SHORT_ANSWER_MAX_TOKENS_HINT", "4096"))
_FOLLOW_UP_MAX_TOKENS_HINT = int(os.getenv("FOLLOW_UP_MAX_TOKENS_HINT", "4096"))
_APP_MAIN_FILE_PATH = Path(__file__).resolve().parent / "app_factory.py"
_CODE_FINGERPRINT_FIELDS: Dict[str, str] = {
    "app_main_sha256": sha256_file(_APP_MAIN_FILE_PATH),
    **get_code_fingerprint_fields(),
}


def select_max_tokens_hint(
    qa: Any,
    *,
    short_answer_max_tokens_hint: int,
    follow_up_max_tokens_hint: int,
) -> Optional[int]:
    """question analysis 모드에 따라 LLM 생성 상한 hint를 고른다.
    JOIN은 후속 설명이 상대적으로 길어질 수 있어 follow-up hint를 주고, SEARCH/LOOKUP은 짧은 답변 상한을 쓴다.
    """
    if not qa:
        return None
    if qa.mode == "JOIN":
        return follow_up_max_tokens_hint
    if qa.mode in (None, "SEARCH", "LOOKUP"):
        return short_answer_max_tokens_hint
    return None


def truncate_text(value: Optional[str], limit: int) -> str:
    """로그나 메타 표시에 쓸 문자열을 지정 길이로 잘라낸다.
    원문 텍스트 의미를 바꾸지 않고 뒷부분만 생략표로 대체한다.
    """
    if not value:
        return ""
    text = str(value)
    if limit > 0 and len(text) > limit:
        return text[:limit] + "..."
    return text


def is_debug_logging_enabled() -> bool:
    """`RAG_DEBUG` 환경변수로 디버그 로깅 상태를 판정한다.
    여러 truthy 표현을 허용해 운영 환경과 로컬 환경의 설정 차이를 흡수한다.
    """
    return str(os.getenv("RAG_DEBUG", "0")).strip().lower() in {"1", "true", "yes", "on"}


def log_section(title: str, content: str) -> None:
    """Emit colored section logs only when debug logging is enabled."""

    if not is_debug_logging_enabled():
        return

    header = f"\n\033[96m{'=' * 10} [{title}] {'=' * 10}\033[0m"
    footer = f"\033[96m{'=' * 30}\033[0m\n"
    logger.info("%s\n%s\n%s", header, content, footer)


def mask_query_for_log(query: str, *, max_len: int = 80) -> str:
    """사용자 질의를 로그용 짧은 표현으로 자른다.
    전문을 그대로 남기지 않고 앞부분만 보여줘 개인정보와 긴 prompt가 로그를 오염시키지 않게 한다.
    """
    text = str(query or "").strip()
    if not text:
        return ""
    clipped = text[:max_len]
    return clipped + ("...(truncated)" if len(text) > max_len else "")


def derive_stream_error_code(meta: Dict[str, Any]) -> Optional[str]:
    """stream meta에서 상태를 대표하는 표준 에러 코드를 뽑아낸다.
    deadline 위반, char limit, empty stream 같은 상태를 API 응답과 로그에서 같은 이름으로 다룰 수 있게 한다.
    """
    if not meta:
        return None
    if bool(meta.get("ttft_deadline_exceeded")):
        return "TTFT_DEADLINE_EXCEEDED"
    if bool(meta.get("gen_deadline_exceeded")):
        return "GEN_DEADLINE_EXCEEDED"
    if bool(meta.get("deadline_exceeded")):
        return "DEADLINE_EXCEEDED"
    if bool(meta.get("char_limited")):
        return "CHAR_LIMITED"
    if int(meta.get("stream_content_emitted_chunks") or 0) == 0:
        return "EMPTY_STREAM"
    return None


def extract_contract_failure_details(reason: str) -> Dict[str, Any]:
    """계약 위반 reason 문자열에서 관측용 메타를 추출한다.

    route 레이어는 예외 객체 전체를 모를 수 있으므로, strict contract 오류 문자열에 실린 핵심 필드를
    다시 파싱해 REQ.ERROR 같은 운영 로그에 남긴다.
    """
    text = str(reason or "").strip()
    if not text:
        return {}
    match = _CONTRACT_REASON_PATTERN.search(text)
    if not match:
        return {}
    details: Dict[str, Any] = {
        "contract_fail_reason": match.group("contract_fail_reason"),
        "reranked_count": int(match.group("reranked_count")),
        "contract_min_reranked": int(match.group("min_reranked")),
    }
    empty_result_policy = match.group("empty_result_policy")
    if empty_result_policy:
        details["empty_result_policy"] = empty_result_policy
    return details


def compute_total_ms_from_start(request_started_at: Optional[float]) -> Optional[float]:
    """요청 시작 시점으로부터 지금까지의 경과 시간을 ms로 계산한다.
    음수 경과시간은 clock anomaly로 간주하고 버려, 로그가 잘못된 latency를 남기지 않게 한다.
    """
    if request_started_at is None:
        return None
    elapsed_sec = time.perf_counter() - request_started_at
    if elapsed_sec < 0:
        return None
    return round(elapsed_sec * 1000.0, 1)


def extract_stream_chunk_text_and_field(chunk: Any) -> Tuple[Optional[str], Optional[str]]:
    """LangChain stream chunk에서 본문 텍스트와 stream field을 추출한다.
    message wrapper 유무와 무관하게 같은 tuple을 돌려 SSE 계층이 chunk shape에 덤 민감하게 한다.
    """
    msg = getattr(chunk, "message", None) or chunk
    text = getattr(msg, "content", None)
    ak = getattr(msg, "additional_kwargs", {}) or {}
    return text, ak.get("stream_field")


def normalize_none_string(value: Any) -> Any:
    """반정형 payload 안에 섞인 문자열 `"None"`을 실제 `None`으로 복원한다.
    list/dict를 재귀적으로 순회해 planner·memory·stream meta에 섞인 legacy 표현을 정리한다.
    """
    if isinstance(value, str) and value.strip() == "None":
        return None
    if isinstance(value, list):
        return [normalize_none_string(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_none_string(item) for key, item in value.items()}
    return value


def has_payload_index(client: Any, collection_name: str, field_name: str) -> bool:
    """Qdrant 컬렉션에 특정 payload field 인덱스가 있는지 확인한다.
    인덱스가 없는 필드에 exact filter를 걸었다가 성능이 너무 나빠지는 경로를 사전에 피하게 한다.
    """
    try:
        collection_info = client.get_collection(collection_name=collection_name)
    except Exception:
        return False

    payload_schema = getattr(collection_info, "payload_schema", None)
    if not isinstance(payload_schema, dict):
        return False
    return field_name in payload_schema


def log_event(name: str, **fields: Any) -> None:
    """Log an operations event with shared fingerprint and policy metadata."""

    payload = {"event": name, **_CODE_FINGERPRINT_FIELDS}
    if fields.get("policy_mode") is None:
        payload["policy_mode"] = (
            "strict"
            if str(os.getenv("RAG_STRICT_STRATEGY_CONSISTENCY", "1")).strip().lower() in {"1", "true", "yes", "y"}
            else "compat"
        )
    for key, value in fields.items():
        if value is None:
            continue
        payload[key] = value
    logger.info("[OPS] %s", json.dumps(payload, ensure_ascii=False, default=str))


def _state_log_summary_fields(state: Any, total_ms: Optional[int] = None) -> Dict[str, Any]:
    """Extract summary log fields from workflow state without pulling in app boot imports."""

    question_analysis = getattr(state, "question_analysis", None)
    strategy = getattr(state, "strategy", None)
    intent_payload = getattr(state, "intent_payload", None)
    retrieval_runtime_meta = dict(getattr(state, "retrieval_runtime_meta", {}) or {})
    normalized_intent = getattr(intent_payload, "normalized_intent", None) if intent_payload is not None else None
    strategy_meta = dict(getattr(intent_payload, "strategy_meta", None) or {}) if intent_payload is not None else {}
    context = getattr(state, "context", None) or []
    merge_debug = getattr(state, "merge_debug", None) or {}
    timings = getattr(state, "timings", None) or {}

    target_cols = getattr(strategy, "target_collections", None)
    if target_cols is None:
        target_cols = (
            getattr(normalized_intent, "target_cols", None)
            if normalized_intent is not None and not isinstance(normalized_intent, dict)
            else None
        )
    if target_cols is None and isinstance(normalized_intent, dict):
        target_cols = normalized_intent.get("target_cols")
    if target_cols is None:
        target_cols = getattr(question_analysis, "target_cols", None)

    base_route = getattr(normalized_intent, "base_route", None)
    if base_route is None and isinstance(normalized_intent, dict):
        base_route = normalized_intent.get("base_route")

    planner_base_route = getattr(question_analysis, "head", None) or getattr(question_analysis, "base_route", None)
    hard_contract = getattr(question_analysis, "hard_contract", None)
    soft_strategy_hints = getattr(question_analysis, "soft_strategy_hints", None)

    return {
        "request_id": getattr(state, "request_id", None),
        "conversation_id": getattr(state, "conversation_id", None),
        "stage": "summary",
        "strategy_source": "execution_strategy" if strategy is not None else "assembled_question_analysis_only",
        "base_route": base_route,
        "planner_base_route": planner_base_route,
        "mode": getattr(strategy, "mode", None) or getattr(question_analysis, "mode", None),
        "relation": getattr(strategy, "relation", None) or getattr(question_analysis, "relation", None),
        "target_cols": list(target_cols) if isinstance(target_cols, (list, tuple)) else target_cols,
        "question_analysis_mode": getattr(question_analysis, "mode", None),
        "question_analysis_relation": getattr(question_analysis, "relation", None),
        "question_analysis_join_key_mode": getattr(question_analysis, "join_key_mode", None),
        "execution_mode": getattr(strategy, "mode", None),
        "execution_relation": getattr(strategy, "relation", None),
        "execution_join_key_mode": getattr(strategy, "join_key_mode", None),
        "join_key_mode": getattr(strategy, "join_key_mode", None),
        "join_key_source": getattr(strategy, "join_key_source", None),
        "join_compile_selection": getattr(strategy, "join_compile_selection", None),
        "hop2_key_strategy": getattr(strategy, "hop2_key_strategy", None),
        "resolved_runtime_key_kind": getattr(strategy, "resolved_runtime_key_kind", None),
        "join_keys_used_count": getattr(strategy, "join_keys_used_count", None),
        "query_graph_kind": getattr(strategy, "query_graph_kind", None),
        "anchor_summary": getattr(strategy, "anchor_summary", None),
        "anchor_resolution_status": getattr(strategy, "anchor_resolution_status", None),
        "ambiguity_codes": list(getattr(strategy, "ambiguity_codes", tuple()) or []),
        "resolved_researcher_count": getattr(strategy, "resolved_researcher_count", None),
        "resolved_org_count": getattr(strategy, "resolved_org_count", None),
        "aggregation_kind": getattr(strategy, "aggregation_kind", None),
        "aggregation_metric": timings.get("info.aggregation_metric") or None,
        "aggregation_group_by": timings.get("info.aggregation_group_by") or None,
        "aggregation_threshold": timings.get("info.aggregation_threshold"),
        "aggregation_result_count": timings.get("info.aggregation_result_count"),
        "failed_step": timings.get("info.failed_step") or None,
        "series_kind": getattr(strategy, "series_kind", None),
        "series_result_count": timings.get("info.series_result_count"),
        "series_bucket_count": timings.get("info.series_bucket_count"),
        "pattern_kind": getattr(strategy, "pattern_kind", None) or timings.get("info.pattern_kind") or None,
        "bundle_kind": getattr(strategy, "bundle_kind", None) or timings.get("info.bundle_kind") or None,
        "bundle_target_count": timings.get("info.bundle_target_count"),
        "bundle_project_count": timings.get("info.bundle_project_count"),
        "bundle_item_count": timings.get("info.bundle_item_count"),
        "guidance_required": (
            getattr(strategy, "guidance_required", None)
            if strategy is not None
            else timings.get("info.guidance_required")
        ),
        "pattern_result_count": timings.get("info.pattern_result_count"),
        "pattern_subject_count": timings.get("info.pattern_subject_count"),
        "pattern_support_doc_count": timings.get("info.pattern_support_doc_count"),
        "reverse_trace_enabled": getattr(strategy, "reverse_trace_enabled", None),
        "reverse_trace_hop_count": timings.get("info.reverse_trace_hop_count")
        or getattr(strategy, "reverse_trace_hop_count", None),
        "origin_project_count": timings.get("info.origin_project_count"),
        "followup_perf_count": timings.get("info.followup_perf_count"),
        "planner_mode": getattr(question_analysis, "mode", None),
        "planner_relation": getattr(question_analysis, "relation", None),
        "planner_target_cols": getattr(question_analysis, "target_cols", None),
        "intent_payload_version": getattr(intent_payload, "intent_payload_version", None),
        "strategy_version": getattr(question_analysis, "strategy_version", None),
        "resolved_project_key_axis": getattr(hard_contract, "resolved_project_key_axis", None),
        "project_key_axis_locked": getattr(hard_contract, "project_key_axis_locked", None),
        "unsupported_project_key_alias_count": len(getattr(hard_contract, "unsupported_project_key_aliases", []) or []),
        "soft_strategy_semantic_kind": getattr(soft_strategy_hints, "semantic_kind", None),
        "soft_strategy_has_prev_anchor": getattr(soft_strategy_hints, "has_prev_anchor", None),
        "project_key_policy": timings.get("info.project_key_policy") or getattr(strategy, "project_key_policy", None),
        "join_resolution_policy": timings.get("info.join_resolution_policy")
        or getattr(strategy, "join_resolution_policy", None),
        "resolved_runtime_join_mode": timings.get("info.resolved_runtime_join_mode") or None,
        "dual_branch_used": timings.get("info.dual_branch_used"),
        "candidate_project_key_count": timings.get("info.candidate_project_key_count")
        or strategy_meta.get("candidate_project_key_count"),
        "candidate_perf_key_count": timings.get("info.candidate_perf_key_count")
        or strategy_meta.get("candidate_perf_key_count"),
        "followup_resolution_status": strategy_meta.get("followup_resolution_status"),
        "followup_reference_kind": strategy_meta.get("followup_reference_kind"),
        "selected_prev_index": strategy_meta.get("selected_prev_index"),
        "selected_prev_context_kind": strategy_meta.get("selected_prev_context_kind"),
        "seed_source": strategy_meta.get("seed_source"),
        "raw_query": getattr(state, "question", None),
        "planner_query": timings.get("info.planner_query") or None,
        "resolved_retrieval_query": getattr(state, "resolved_retrieval_query", None),
        "actual_retrieval_query": getattr(state, "actual_retrieval_query", None),
        "retrieval_query_mismatch": int(
            bool(
                getattr(state, "actual_retrieval_query", None)
                and getattr(state, "resolved_retrieval_query", None)
                and getattr(state, "actual_retrieval_query", None) != getattr(state, "resolved_retrieval_query", None)
            )
        ),
        "docs_found": len(context),
        "selected_model": merge_debug.get("selected_model"),
        "selection_reason": merge_debug.get("selection_reason"),
        "state_consistency_status": (
            (merge_debug.get("selected_state_consistency") or {}).get("status")
            if isinstance(merge_debug.get("selected_state_consistency"), dict)
            else None
        ),
        "state_consistency_reason_codes": (
            list((merge_debug.get("selected_state_consistency") or {}).get("reason_codes") or [])
            if isinstance(merge_debug.get("selected_state_consistency"), dict)
            else None
        ),
        "visible_answer_manifest_status": merge_debug.get("visible_answer_manifest_status"),
        "rendered_context_used": int(bool(getattr(state, "rendered_context_used", False))),
        "contract_fail_reason": timings.get("info.contract_fail_reason") or None,
        "empty_result_policy": timings.get("info.empty_result_policy") or None,
        "reranked_count": timings.get("info.reranked_count"),
        "degraded": int(bool(getattr(state, "degraded", False))),
        "total_ms": total_ms,
        # L2 layer fields
        "l2_orchestrator_owned": retrieval_runtime_meta.get("orchestrator_owned"),
        "l2_policy_name": retrieval_runtime_meta.get("policy_name"),
        "l2_execution_kind": retrieval_runtime_meta.get("execution_kind"),
        "l2_recovery_applied": retrieval_runtime_meta.get("recovery_applied"),
        "l2_legacy_retry_allowed": retrieval_runtime_meta.get("legacy_retry_allowed"),
    }

