from __future__ import annotations

import hashlib
import logging
import logging.handlers
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from apps.core.query_intent import SUPERLATIVE_CUES

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


def has_superlative_cue(text: str) -> bool:
    """질의에 최고·최다·최신 같은 superlative cue가 있는지 검사한다.
    후속 모드 선택이나 max token hint가 짧은 답변 위주로 가야 하는지 판단할 때 쓴다.
    """
    query = (text or "").strip().lower()
    return any(cue in query for cue in SUPERLATIVE_CUES)


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

