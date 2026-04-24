"""NTIS RAG API의 런타임 유틸리티 및 헬퍼 모듈.

로깅 구성, 텍스트 처리, 운영 이벤트 로깅(log_event), 상태 요약 데이터 추출 등
API 서버 운영에 필요한 다양한 공통 기능을 제공합니다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from apps.api.contracts.workflow_models import measure_latency as measure_latency_impl
from apps.retrieval.rag_runtime_observability import get_code_fingerprint_fields

# RAG 전략 위반 및 계약 실패 상세 파싱을 위한 정규식
_CONTRACT_REASON_PATTERN = re.compile(
    r"reason=(?P<contract_fail_reason>[a-z_]+),\s*"
    r"reranked=(?P<reranked_count>\d+),\s*"
    r"min_reranked=(?P<min_reranked>\d+)"
    r"(?:,\s*empty_result_policy=(?P<empty_result_policy>[a-z_]+))?"
)


def sha256_file(path: Path) -> str:
    """
    파일 내용의 SHA-256 해시를 계산합니다.
    코드 지문(Fingerprint) 생성 시 파일 변경 여부를 확인하기 위해 사용합니다.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


from loguru import logger

def setup_file_logging(*, log_path: str = "logs/app.log"):
    """
    Loguru 로거에 파일 출력을 추가하여 로그를 기록합니다.
    - rotation: 50MB 단위로 새 파일 생성
    - retention: 최대 10개의 로그 파일 유지
    """
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    logger.add(
        log_path,
        rotation="50 MB",
        retention=10,
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO",
    )

# 로깅 초기 설정 및 의존성 부분 적용
setup_file_logging()
measure_latency = partial(measure_latency_impl, logger_obj=logger)

_SHORT_ANSWER_MAX_TOKENS_HINT = int(os.getenv("SHORT_ANSWER_MAX_TOKENS_HINT", "4096"))
_FOLLOW_UP_MAX_TOKENS_HINT = int(os.getenv("FOLLOW_UP_MAX_TOKENS_HINT", "4096"))
_APP_MAIN_FILE_PATH = Path(__file__).resolve().parent / "app_factory.py"

# 시스템 상태 확인을 위한 코드 지문 필드 구성
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
    """
    질문 분석(QA) 모드에 따라 적절한 LLM Max Token 힌트를 선택합니다.
    - JOIN 모드: 상세한 설명이 필요하므로 follow-up 힌트 사용
    - SEARCH/LOOKUP 모드: 간결한 답변을 위해 short-answer 힌트 사용
    """
    if not qa:
        return None
    if qa.mode == "JOIN":
        return follow_up_max_tokens_hint
    if qa.mode in (None, "SEARCH", "LOOKUP"):
        return short_answer_max_tokens_hint
    return None


def truncate_text(value: Optional[str], limit: int) -> str:
    """
    문자열이 지정된 길이를 초과하면 잘라내고 생략 부호(...)를 붙입니다.
    주로 로그 출력 시 너무 긴 텍스트를 제한하기 위해 사용합니다.
    """
    if not value:
        return ""
    text = str(value)
    if limit > 0 and len(text) > limit:
        return text[:limit] + "..."
    return text


def merge_log_fields(*field_sets: Any, **overrides: Any) -> Dict[str, Any]:
    """
    여러 개의 로그 필드 세트(dict 또는 객체)를 하나로 병합합니다.
    동일한 키가 있을 경우 뒤에 오는 값이 우선하며, 명시적 오버라이드 값이 최종 우선순위를 갖습니다.
    """
    merged: Dict[str, Any] = {}
    for field_set in field_sets:
        if field_set is None:
            continue
        if isinstance(field_set, dict):
            merged.update(field_set)
            continue
        items = getattr(field_set, "items", None)
        if callable(items):
            try:
                merged.update(dict(items()))
            except Exception:
                continue
    merged.update(overrides)
    return merged


def is_debug_logging_enabled() -> bool:
    """`RAG_DEBUG` 환경변수가 설정되어 있는지 확인합니다."""
    return str(os.getenv("RAG_DEBUG", "0")).strip().lower() in {"1", "true", "yes", "on"}


def log_section(title: str, content: str) -> None:
    """디버그 모드일 때만 콘솔에 강조된 섹션 로그를 출력합니다."""
    if not is_debug_logging_enabled():
        return
    header = f"\n\033[96m{'=' * 10} [{title}] {'=' * 10}\033[0m"
    footer = f"\033[96m{'=' * 30}\033[0m\n"
    logger.info("{}\n{}\n{}", header, content, footer)


def mask_query_for_log(query: str, *, max_len: int = 80) -> str:
    """
    로그에 기록할 사용자 질문을 마스킹 및 요약 처리합니다.
    개인정보 보호 및 로그 용량 최적화를 위해 앞부분만 남깁니다.
    """
    text = str(query or "").strip()
    if not text:
        return ""
    clipped = text[:max_len]
    return clipped + ("...(truncated)" if len(text) > max_len else "")


def derive_stream_error_code(meta: Dict[str, Any]) -> Optional[str]:
    """
    스트림 메타데이터를 분석하여 표준 에러 코드를 도출합니다.
    타임아웃(TTFT), 생성 중단, 빈 결과 등의 상태를 식별합니다.
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
    """
    에러 메시지(reason) 문자열에서 계약 실패 관련 상세 수치들을 파싱하여 추출합니다.
    예: 리랭크 결과 개수 부족 등의 상황을 분석하기 위해 사용합니다.
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
    """
    요청 시작 시점으로부터의 경과 시간(ms)을 계산합니다.
    """
    if request_started_at is None:
        return None
    elapsed_sec = time.perf_counter() - request_started_at
    if elapsed_sec < 0:
        return None
    return round(elapsed_sec * 1000.0, 1)


def extract_stream_chunk_text_and_field(chunk: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    LangChain 스트림 청크에서 본문 텍스트와 스트림 필드(예: reasoning)를 분리하여 추출합니다.
    """
    msg = getattr(chunk, "message", None) or chunk
    text = getattr(msg, "content", None)
    ak = getattr(msg, "additional_kwargs", {}) or {}
    return text, ak.get("stream_field")


def normalize_none_string(value: Any) -> Any:
    """
    데이터 내에 문자열 "None"이 섞여 있을 경우 이를 실제 파이썬 `None`으로 정규화합니다.
    중첩된 dict/list 내부까지 재귀적으로 처리합니다.
    """
    if isinstance(value, str) and value.strip() == "None":
        return None
    if isinstance(value, list):
        return [normalize_none_string(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_none_string(item) for key, item in value.items()}
    return value


def has_payload_index(client: Any, collection_name: str, field_name: str) -> bool:
    """
    Qdrant 컬렉션에 특정 페이로드 인덱스가 생성되어 있는지 확인합니다.
    """
    try:
        collection_info = client.get_collection(collection_name=collection_name)
    except Exception:
        return False

    payload_schema = getattr(collection_info, "payload_schema", None)
    if not isinstance(payload_schema, dict):
        return False
    return field_name in payload_schema


def _render_pretty_log(name: str, fields: Dict[str, Any]) -> Optional[str]:
    """
    운영 이벤트를 사람이 읽기 편한 콘솔 요약 문자열로 렌더링합니다.
    에이전트 의사결정, 도구 결과, 최종 결과 등을 이모지와 함께 시각화합니다.
    """
    if name == "AGENT.DECISION":
        dtype = fields.get("decision_type", "unknown")
        icon = {"call_tool": "🛠️", "direct_answer": "💬", "ask_clarification": "❓"}.get(dtype, "🤖")
        tool = f" -> [{fields.get('tool_name')}]" if dtype == "call_tool" else ""
        reason = fields.get("reasoning_summary", "")
        return f"\033[94m{icon} [Agent Decision] {dtype}{tool}\033[0m\n   \033[90mㄴ Reason: {reason}\033[0m"

    if name == "AGENT.TOOL_OBSERVATION":
        otype = fields.get("observation_type", "unknown")
        icon = {"planned_intent": "✅", "error": "❌", "contract_violation": "🛡️", "no_results": "⚠️"}.get(otype, "👁️")
        summary = fields.get("summary", "")
        return f"\033[96m{icon} [Tool Result] {otype}\033[0m\n   \033[90mㄴ {summary}\033[0m"

    if name.startswith("PLANNER.STAGE"):
        stage = name.replace("PLANNER.", "")
        conf = fields.get("confidence", 0.0)
        return f"\033[95m🧠 [{stage}] Conf: {conf:.2f}\033[0m"

    if name == "REQ.SUMMARY":
        ms = fields.get("total_ms", 0)
        return f"\033[92m✨ [Request Done] {ms}ms | Model: {fields.get('selected_model')}\033[0m"

    return None


def log_event(name: str, **fields: Any) -> None:
    """
    운영 시스템 이벤트를 로깅합니다.
    1. 모든 필드를 JSON 형태로 파일에 기록 (데이터 분석용)
    2. 주요 이벤트는 콘솔에 가독성 높은 형태로 요약 출력 (개발 확인용)
    """
    payload = {"event": name, **_CODE_FINGERPRINT_FIELDS}
    
    # 전략 일관성 모드 기본값 설정
    if fields.get("policy_mode") is None:
        payload["policy_mode"] = (
            "strict"
            if str(os.getenv("RAG_STRICT_STRATEGY_CONSISTENCY", "1")).strip().lower() in {"1", "true", "yes", "y"}
            else "compat"
        )
        
    for key, value in fields.items():
        if value is None: continue
        payload[key] = value
    
    # 1. 원본 JSON 로그 기록
    logger.info("[OPS] {}", json.dumps(payload, ensure_ascii=False, default=str))

    # 2. 콘솔 가독성을 위한 Pretty 출력
    pretty = _render_pretty_log(name, payload)
    if pretty:
        print(f"\n{pretty}\n")


def _state_log_summary_fields(state: Any, total_ms: Optional[int] = None) -> Dict[str, Any]:
    """
    워크플로우 상태(State) 객체에서 운영 통계용 요약 필드들을 추출합니다.
    질문 유형, 검색 전략, 사용된 키의 개수, 응답 모델 선택 사유 등을 방대하게 수집합니다.
    """
    # 상태에서 각종 객체 추출
    question_analysis = getattr(state, "question_analysis", None)
    agent_decision = getattr(state, "agent_decision", None)
    agent_observation = getattr(state, "agent_observation", None)
    agent_context_meta = dict(getattr(state, "agent_context_meta", {}) or {})
    strategy = getattr(state, "strategy", None)
    intent_payload = getattr(state, "intent_payload", None)
    retrieval_runtime_meta = dict(getattr(state, "retrieval_runtime_meta", {}) or {})
    context = getattr(state, "context", None) or []
    merge_debug = getattr(state, "merge_debug", None) or {}
    timings = getattr(state, "timings", None) or {}
    
    normalized_intent = getattr(intent_payload, "normalized_intent", None) if intent_payload is not None else None
    strategy_meta = dict(getattr(intent_payload, "strategy_meta", None) or {}) if intent_payload is not None else {}

    # 대상 컬렉션 확인
    target_cols = getattr(strategy, "target_collections", None)
    if target_cols is None:
        target_cols = getattr(normalized_intent, "target_cols", None) if not isinstance(normalized_intent, dict) else normalized_intent.get("target_cols")
    if target_cols is None:
        target_cols = getattr(question_analysis, "target_cols", None)

    # 기본 필드 구성
    return {
        "request_id": getattr(state, "request_id", None),
        "conversation_id": getattr(state, "conversation_id", None),
        "stage": "summary",
        
        # 1. 전략 및 모드 정보
        "mode": getattr(strategy, "mode", None) or getattr(question_analysis, "mode", None),
        "relation": getattr(strategy, "relation", None) or getattr(question_analysis, "relation", None),
        "target_cols": list(target_cols) if isinstance(target_cols, (list, tuple)) else target_cols,
        "join_key_mode": getattr(strategy, "join_key_mode", None),
        "join_key_source": getattr(strategy, "join_key_source", None),
        
        # 2. 검색/분석 상세 (Aggregation, Series, Pattern 등)
        "aggregation_kind": getattr(strategy, "aggregation_kind", None),
        "aggregation_result_count": timings.get("info.aggregation_result_count"),
        "series_kind": getattr(strategy, "series_kind", None),
        "series_bucket_count": timings.get("info.series_bucket_count"),
        "pattern_kind": getattr(strategy, "pattern_kind", None) or timings.get("info.pattern_kind"),
        
        # 3. 플래너 및 계약(Contract) 정보
        "planner_mode": getattr(question_analysis, "mode", None),
        "project_key_policy": timings.get("info.project_key_policy") or getattr(strategy, "project_key_policy", None),
        "contract_fail_reason": timings.get("info.contract_fail_reason"),
        "reranked_count": timings.get("info.reranked_count"),
        
        # 4. 질의 및 검색 결과 통계
        "raw_query": getattr(state, "question", None),
        "docs_found": len(context),
        "selected_model": merge_debug.get("selected_model"),
        "selection_reason": merge_debug.get("selection_reason"),
        "total_ms": total_ms,
        
        # 5. 에이전트(L1) 동작 정보
        "agent_decision_type": getattr(agent_decision, "decision_type", None),
        "agent_tool_name": getattr(agent_decision, "tool_name", None),
        "agent_observation_type": getattr(agent_observation, "observation_type", None),
        
        # 6. 오케스트레이터(L2) 동작 정보
        "l2_policy_name": retrieval_runtime_meta.get("policy_name"),
        "l2_execution_kind": retrieval_runtime_meta.get("execution_kind"),
        "l2_recovery_applied": retrieval_runtime_meta.get("recovery_applied"),
    }
