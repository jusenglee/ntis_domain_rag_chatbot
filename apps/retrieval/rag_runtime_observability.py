from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from pprint import pformat
from typing import Any, Callable, Dict, List, Optional

from apps.platform.settings import logger as default_logger

_RAG_LOG_LEVEL_ORDER = {"normal": 0, "debug": 1, "trace": 2}
_REQUEST_ID_CTX: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("rag_request_id", default=None)
_CONVERSATION_ID_CTX: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("rag_conversation_id", default=None)
_RAG_PIPELINE_FILE_PATH = Path(__file__).resolve().with_name("rag_pipeline.py")
_TIMING_DEFAULTS: Dict[str, Any] = {
    "phase.stack_init": 0.0,
    "phase.kw_det": 0.0,
    "phase.dense_search": 0.0,
    "phase.rrf_merge": 0.0,
    "phase.final_rerank": 0.0,
    "phase.build_context": 0.0,
    "phase.hydrate_full_payload": 0.0,
    "phase.hop_total": 0.0,
    "phase.total": 0.0,
    "info.ctx_budget": 0.0,
    "info.contract_fail_reason": "",
    "info.empty_result_policy": "",
    "info.reranked_count": 0.0,
    "metric.final_score_avg": 0.0,
    "metric.final_score_max": 0.0,
    "event.embed_precompute_error": 0.0,
}


@dataclass(frozen=True)
class RuntimeObservability:
    """RAG 런타임 관측 헬퍼를 묶어 주는 구성 객체다."""
    clip_text_fn: Callable[[str, int], str]
    merge_log_fields_fn: Callable[..., Dict[str, Any]]
    log_section_fn: Callable[..., None]
    log_kv_fn: Callable[..., None]
    log_top_points_fn: Callable[..., None]
    resolve_env_topn_fn: Callable[..., int]
    init_timings_fn: Callable[[], Dict[str, Any]]
    timing_put_fn: Callable[[Dict[str, Any], str, Any], None]
    record_col_timings_fn: Callable[..., None]
    clean_one_line_fn: Callable[[object, int], str]


def _rag_log_level() -> str:
    """환경변수를 보고 normal/debug/trace 로그 레벨을 결정한다."""
    level = str(os.getenv("RAG_LOG_LEVEL", "")).strip().lower()
    if level in _RAG_LOG_LEVEL_ORDER:
        return level
    if str(os.getenv("RAG_DEBUG", "0")).strip().lower() in ("1", "true", "yes", "y"):
        return "debug"
    return "normal"


def _rag_log_enabled(tier: str = "normal") -> bool:
    """현재 로그 레벨이 요구 tier를 만족하는지 판단한다."""
    current = _RAG_LOG_LEVEL_ORDER.get(_rag_log_level(), 0)
    required = _RAG_LOG_LEVEL_ORDER.get(str(tier or "normal").strip().lower(), 0)
    return current >= required


def _rag_color_on() -> bool:
    """컬러 로그 출력 여부를 환경변수로 결정한다."""
    return str(os.getenv("RAG_LOG_COLOR", "0")).strip().lower() in ("1", "true", "yes", "y")


def clip_text(value: str, max_chars: int) -> str:
    """로그용 텍스트를 최대 길이까지 잘라 안전하게 보존한다."""
    if value is None:
        return ""
    text = str(value)
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 1] + "…trunc)"
    return text


def safe_json(obj: object) -> str:
    """로그 대상을 JSON 직렬화하고 실패하면 pprint로 후퇴한다."""
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return pformat(obj, width=120, compact=True)


def sha256_file(path: Path) -> str:
    """파일 내용의 SHA-256 해시를 구한다."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_code_fingerprint_fields() -> Dict[str, str]:
    """현재 런타임이 어떤 rag_pipeline.py를 쓰는지 식별하는 해시 필드를 만든다."""
    return {"rag_pipeline_sha256": sha256_file(_RAG_PIPELINE_FILE_PATH)}


def set_log_context(*, request_id: Optional[str] = None, conversation_id: Optional[str] = None, logger_obj: Any = default_logger) -> None:
    """request_id와 conversation_id를 contextvar에 심고 시작 이벤트를 남긴다."""
    _REQUEST_ID_CTX.set(request_id)
    _CONVERSATION_ID_CTX.set(conversation_id)
    logger_obj.info(
        "[RAG] {}",
        json.dumps(
            {
                "event": "REQ.CONTEXT",
                "request_id": request_id,
                "conversation_id": conversation_id,
                **get_code_fingerprint_fields(),
            },
            ensure_ascii=False,
            default=str,
        ),
    )


def merge_log_fields(primary: Dict[str, Any], extra: Dict[str, Any], *, on_conflict: str = "suffix", suffix: str = "_extra") -> Dict[str, Any]:
    """기본 로그 필드와 추가 필드를 합치되, 충돌 시 suffix 정책을 적용한다."""
    merged = dict(primary or {})
    for key, value in (extra or {}).items():
        key = str(key)
        if key not in merged:
            merged[key] = value
        elif on_conflict == "suffix":
            merged[f"{key}{suffix}"] = value
    return merged


def log_section(title: str, content: object = None, *, level: str = "info", max_chars: int = None, tier: str = "normal", logger_obj: Any = default_logger) -> None:
    """로그 섹션 제목과 본문을 tier/level 정책에 맞게 출력한다."""
    if not _rag_log_enabled(tier):
        return
    log_fn = getattr(logger_obj, (level or "info").lower().strip(), logger_obj.info)
    max_chars = int(max_chars) if max_chars is not None else int(os.getenv("RAG_LOG_MAX_CHARS", "6000"))
    if content is None:
        body = ""
    elif isinstance(content, str):
        body = content
    else:
        body = safe_json(content)
    body = clip_text(body, max_chars)
    if str(tier or "normal").strip().lower() == "normal":
        payload = {"event": title, "data": content if content is not None else body, **get_code_fingerprint_fields()}
        request_id = _REQUEST_ID_CTX.get()
        conversation_id = _CONVERSATION_ID_CTX.get()
        if request_id:
            payload["request_id"] = request_id
        if conversation_id:
            payload["conversation_id"] = conversation_id
        log_fn("[RAG] {}", json.dumps(payload, ensure_ascii=False, default=str))
        return
    if _rag_color_on():
        header = f"\n\033[96m{'='*10} [{title}] {'='*10}\033[0m"
        footer = f"\033[96m{'='*36}\033[0m\n"
    else:
        header = f"\n{'='*10} [{title}] {'='*10}"
        footer = f"{'='*36}\n"
    log_fn(f"{header}\n{body}\n{footer}")


def log_kv(title: str, *, level: str = "info", tier: str = "normal", logger_obj: Any = default_logger, **kwargs) -> None:
    """key-value payload를 한 섹션 로그로 감싸 남긴다."""
    if not _rag_log_enabled(tier):
        return
    payload = {}
    for key, value in kwargs.items():
        payload[key] = clip_text(value, int(os.getenv("RAG_LOG_KV_STR_MAX", "240"))) if isinstance(value, str) else value
    log_section(title, payload, level=level, tier=tier, logger_obj=logger_obj)


def point_summary(point: Any, *, get_meta: Callable[[dict], dict], resolve_collection: Callable[[Any, dict], str]) -> Dict[str, Any]:
    """hit point에서 출처, doc_id, tag, score, title를 뽑아 로그용 요약을 만든다."""
    payload = getattr(point, "payload", None) or {}
    if not isinstance(payload, dict):
        payload = {}
    meta = get_meta(payload)

    def pick(*values: Any) -> str:
        """여러 후보 값 중 첫 번째 유효 문자열을 고른다."""
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    title = pick(payload.get("title_text"), payload.get("title1"), payload.get("title2"), meta.get("kor_pjt_nm"), meta.get("eng_pjt_nm"))
    score = getattr(point, "score", None)
    try:
        score = float(score) if score is not None else None
    except Exception:
        score = None
    return {
        "col": pick(resolve_collection(point, payload)),
        "doc_id": pick(payload.get("doc_id"), getattr(point, "id", None)),
        "tag": pick(payload.get("tag")),
        "score": score,
        "_rrf": payload.get("_rrf"),
        "_final_total": payload.get("_final_total"),
        "_final_rrf": payload.get("_final_rrf"),
        "_final_kw": payload.get("_final_kw"),
        "_final_f": payload.get("_final_f"),
        "title": clip_text(title, int(os.getenv("RAG_LOG_TITLE_MAX", "180"))),
    }


def log_top_points(title: str, points: List[Any], *, get_meta: Callable[[dict], dict], resolve_collection: Callable[[Any, dict], str], topn: int = None, level: str = "info", tier: str = "debug", logger_obj: Any = default_logger) -> None:
    """상위 hit들을 point_summary 형태로 변환해 로그에 남긴다."""
    if not _rag_log_enabled(tier):
        return
    topn = int(topn) if topn is not None else int(os.getenv("RAG_LOG_TOPN", "8"))
    arr = [point_summary(point, get_meta=get_meta, resolve_collection=resolve_collection) for point in (points or [])[: max(0, topn)]]
    log_section(title, arr, level=level, tier=tier, logger_obj=logger_obj)


def resolve_env_topn(primary_key: str, *, default: int, fallback_keys: Optional[List[str]] = None) -> int:
    """여러 env key 후보를 순차 탐색해 top-n 설정값을 결정한다."""
    keys = [primary_key] + list(fallback_keys or [])
    for key in keys:
        raw = os.getenv(key)
        if raw is None:
            continue
        try:
            return max(0, int(raw))
        except Exception:
            continue
    return max(0, int(default))


def init_timings() -> Dict[str, Any]:
    """기본 timing/meta 키로 채워진 초기 timings dict를 만든다."""
    return dict(_TIMING_DEFAULTS)


def timing_put(timings: Dict[str, Any], key: str, value: Any) -> None:
    """같은 timing key가 충돌하면 `.dupN` 형태로 보존하며 값을 기록한다."""
    if key in timings:
        default_val = _TIMING_DEFAULTS.get(key, None)
        if key in _TIMING_DEFAULTS and timings[key] == default_val:
            timings[key] = value
            return
        if timings[key] == value:
            return
        idx = 2
        next_key = f"{key}.dup{idx}"
        while next_key in timings:
            idx += 1
            next_key = f"{key}.dup{idx}"
        timings[next_key] = value
        return
    timings[key] = value


def record_col_timings(timings: Dict[str, Any], col: str, *, stats: Dict[str, float], local_timings: Dict[str, float]) -> None:
    """컬렉션 단위 stats와 local phase timing을 공통 timings dict에 기록한다."""
    prefix = f"col.{col}"
    for key, value in (stats or {}).items():
        timing_put(timings, f"{prefix}.stats.{key}", float(value))
    for key, value in (local_timings or {}).items():
        try:
            timing_put(timings, f"{prefix}.phase.{key}", float(value))
        except Exception:
            continue


def clean_one_line(value: object, max_len: int = 160) -> str:
    """여러 줄 텍스트를 한 줄로 정리하고 길이를 제한한다."""
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        text = text[: max_len - 1] + "..."
    return text


def build_runtime_observability(*, get_meta_fn: Callable[[dict], dict], resolve_collection_fn: Callable[[Any, dict], str], logger_obj: Any = default_logger) -> RuntimeObservability:
    """로구 함수들을 현재 logger와 callback에 바인딩한 RuntimeObservability를 조립한다."""
    return RuntimeObservability(
        clip_text_fn=clip_text,
        merge_log_fields_fn=merge_log_fields,
        log_section_fn=lambda title, content=None, level="info", max_chars=None, tier="normal": log_section(title, content, level=level, max_chars=max_chars, tier=tier, logger_obj=logger_obj),
        log_kv_fn=lambda title, level="info", tier="normal", **kwargs: log_kv(title, level=level, tier=tier, logger_obj=logger_obj, **kwargs),
        log_top_points_fn=lambda title, points, topn=None, level="info", tier="debug": log_top_points(title, points, get_meta_fn=get_meta_fn, resolve_collection_fn=resolve_collection_fn, topn=topn, level=level, tier=tier, logger_obj=logger_obj),
        resolve_env_topn_fn=resolve_env_topn,
        init_timings_fn=init_timings,
        timing_put_fn=timing_put,
        record_col_timings_fn=record_col_timings,
        clean_one_line_fn=clean_one_line,
    )
