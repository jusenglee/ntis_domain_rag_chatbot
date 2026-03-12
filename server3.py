import logging
import asyncio
import uuid
import json
import time
import os
import re
import hashlib
import aiofiles
from typing import Annotated, Optional, List, Dict, Any, Literal, Tuple
from contextlib import asynccontextmanager
from pathlib import Path
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
import httpx
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from dataclasses import replace

# Redis
import redis.asyncio as redis

# LangChain & LangGraph
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_core.tools import Tool
from langchain_core.output_parsers import PydanticOutputParser

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

# --- User Modules ---
from rag_store import build_rag_objects
from storage import KVStore
from triton_llm import TritonChatModel
from openai_compat_llm import OpenAICompatChatModel
from rag_pipeline import run_rag_ab_compare, set_log_context, get_code_fingerprint_fields
from retrieval import ensure_keyword_index, ensure_text_index, warmup_sparse_encoder
from rag_parts.pipeline_steps import NormalizedIntent, normalize_intent, build_changed_fields
from rag_parts.planner_contract import StrategyViolation
from rag_parts.log_keys import (
    LOG_KEY_POLICY_MODE,
    CHANGED_BY_PLANNER_MERGE,
)

from rag_parts.query_intent import (
    classify_query as classify_query_intent,
    _cheap_precheck,
    normalize_org_terms,
    SUPERLATIVE_CUES,
    extract_people_terms,
    extract_org_terms,
    extract_org_role,
    extract_years,
    extract_perf_types,
    extract_title_terms,
)
from schemas import IntentPayloadV2, PlannerStage1Decision, PlannerStage2Slots
from settings import (
    REDIS_URL,
    REDIS_TTL,
    MAX_TOP_K_SIZE,
    MAX_CONTEXT_CHARS,
    MAX_DOC_SENTENCES,
    MAX_DOC_TOKENS, DEFAULT_MODEL_NAME,
    SOLAR_VLLM_CONFIG,
)

from rag_mapper.rag_mapper import RagMapper, MappingError
from llm_streaming import run_llm_streaming
from metrics import (
    MetricSnapshot,
    collect_snapshot as collect_metrics_snapshot,
    STREAM_INTERVAL_SECONDS as METRICS_STREAM_INTERVAL_SECONDS,
    PROMETHEUS_TIMEOUT as METRICS_PROMETHEUS_TIMEOUT,
)

# --- Logging Setup ---

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

def log_section(title, content):
    if not _is_debug_logging_enabled():
        return
    header = f"\n\033[96m{'='*10} [{title}] {'='*10}\033[0m"
    footer = f"\033[96m{'='*30}\033[0m\n"
    logger.info(f"{header}\n{content}\n{footer}")


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Chatbot_Server")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_SERVER3_FILE_PATH = Path(__file__).resolve()
_CODE_FINGERPRINT_FIELDS: Dict[str, str] = {
    "server3_sha256": _sha256_file(_SERVER3_FILE_PATH),
    **get_code_fingerprint_fields(),
}


def _log_event(name: str, **fields: Any) -> None:
    payload = {"event": name, **_CODE_FINGERPRINT_FIELDS}
    if fields.get("policy_mode") is None:
        payload["policy_mode"] = "strict" if str(os.getenv("RAG_STRICT_STRATEGY_CONSISTENCY", "1")).strip().lower() in ("1", "true", "yes", "y") else "compat"
    for k, v in fields.items():
        if v is None:
            continue
        payload[k] = v
    logger.info("[OPS] %s", json.dumps(payload, ensure_ascii=False, default=str))


def _state_log_summary_fields(state: Any, total_ms: Optional[int] = None) -> Dict[str, Any]:
    question_analysis = getattr(state, "question_analysis", None)
    context = getattr(state, "context", None) or []
    merge_debug = getattr(state, "merge_debug", None) or {}
    return {
        "request_id": getattr(state, "request_id", None),
        "conversation_id": getattr(state, "conversation_id", None),
        "stage": "summary",
        "mode": getattr(question_analysis, "mode", None),
        "relation": getattr(question_analysis, "relation", None),
        "target_cols": getattr(question_analysis, "target_cols", None),
        "docs_found": len(context),
        "selected_model": merge_debug.get("selected_model"),
        "rendered_context_used": int(bool(getattr(state, "rendered_context_used", False))),
        "fallback_context_used": int(bool(getattr(state, "fallback_context_used", False))),
        "degraded": int(bool(getattr(state, "degraded", False))),
        "total_ms": total_ms,
    }

def setup_file_logging(log_path="logs/server3.log"):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    app_logger = logging.getLogger("Chatbot_Server")
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    fh = RotatingFileHandler(
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

setup_file_logging()

templates = Jinja2Templates(directory="templates")
TEMPLATE_INDEX_PATH = Path("templates/index.html")

# --- Configuration ---
kv_store: Optional[KVStore] = None
MAX_HISTORY_TURNS = 10
HISTORY_PREVIEW_LIMIT = 100
SHORT_ANSWER_MAX_TOKENS_HINT = int(os.getenv("SHORT_ANSWER_MAX_TOKENS_HINT", "4096"))
FOLLOW_UP_MAX_TOKENS_HINT = int(os.getenv("FOLLOW_UP_MAX_TOKENS_HINT", "4096"))
SOLAR_DEADLINE_MS = int(os.getenv("SOLAR_DEADLINE_MS", "9000"))
SOLAR_TTFT_DEADLINE_MS = int(os.getenv("SOLAR_TTFT_DEADLINE_MS", str(SOLAR_DEADLINE_MS)))
SOLAR_GEN_DEADLINE_MS = int(os.getenv("SOLAR_GEN_DEADLINE_MS", "15000"))
SOLAR_STREAM_MAX_CHARS = int(os.getenv("SOLAR_STREAM_MAX_CHARS", "10000"))
DUAL_MODEL_MERGE_POLICY = os.getenv("DUAL_MODEL_MERGE_POLICY", "solar_first").strip().lower()
DUAL_MODEL_FALLBACK_MESSAGE = "일시적으로 생성 결과가 비어 재시도해주세요"
SOLAR_MIN_ANSWER_CHARS = int(os.getenv("SOLAR_MIN_ANSWER_CHARS", "60"))
MAX_FIELD_SENTENCES = int(os.getenv("MAX_FIELD_SENTENCES", "3"))
MAX_FIELD_TOKENS = int(os.getenv("MAX_FIELD_TOKENS", "120"))
SOLAR_MAX_DOC_SENTENCES = int(os.getenv("SOLAR_MAX_DOC_SENTENCES", str(MAX_DOC_SENTENCES)))
SOLAR_MAX_DOC_TOKENS = int(os.getenv("SOLAR_MAX_DOC_TOKENS", str(MAX_DOC_TOKENS)))
SOLAR_MAX_CONTEXT_CHARS = int(os.getenv("SOLAR_MAX_CONTEXT_CHARS", str(MAX_CONTEXT_CHARS)))
PRIORITY_CONTEXT_FIELDS = tuple(
    field.strip()
    for field in os.getenv(
        "PRIORITY_CONTEXT_FIELDS",
        "title,title_text,title1,title2,pjt_id,pjt_no,project_id,project_no,ntis_task_id,task_id,과제명,과제번호",
    ).split(",")
    if field.strip()
)
RAG_RENDER_TEXT_FIELDS = tuple(
    field.strip()
    for field in os.getenv(
        "RAG_RENDER_TEXT_FIELDS",
        "content_text,flat_text,keyword_text,summary_text,abstract_text",
    ).split(",")
    if field.strip()
)
RAG_RENDER_TEXT_MAX_CHARS = int(os.getenv("RAG_RENDER_TEXT_MAX_CHARS", "1200"))
RAG_RENDER_TEXT_TOTAL_MAX_CHARS = int(os.getenv("RAG_RENDER_TEXT_TOTAL_MAX_CHARS", "2400"))
RAG_RENDER_SAMPLE_SIZE = int(os.getenv("RAG_RENDER_SAMPLE_SIZE", "5"))
PLANNER_SCHEMA_VERSION = "v2"
PLANNER_DISABLE_THINKING = os.getenv("PLANNER_DISABLE_THINKING", "true").strip().lower() in {"1", "true", "yes", "on"}
PLANNER_STAGEWISE_ENABLED = os.getenv("PLANNER_STAGEWISE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
PLANNER_STAGE1_PROMPT_VERSION = os.getenv("PLANNER_STAGE1_PROMPT_VERSION", "v1").strip()
PLANNER_STAGE2_PROMPT_VERSION = os.getenv("PLANNER_STAGE2_PROMPT_VERSION", "v1").strip()
PLANNER_TEMPERATURE = float(os.getenv("PLANNER_TEMPERATURE", "0.0"))
PLANNER_TOP_P = 1.0
PLANNER_MAX_TOKENS = 450
PLANNER_TIMEOUT_MS = max(1, int(os.getenv("PLANNER_TIMEOUT_MS", "4500")))
PLANNER_V2_RETRY_ATTEMPTS = int(os.getenv("PLANNER_V2_RETRY_ATTEMPTS", "1"))
PLANNER_V2_RETRY_ATTEMPTS_MAX = max(1, int(os.getenv("PLANNER_V2_RETRY_ATTEMPTS_MAX", "2")))
PLANNER_V2_RETRY_BACKOFF_SEC = float(os.getenv("PLANNER_V2_RETRY_BACKOFF_SEC", "0.35"))
PLANNER_V2_BACKOFF_CAP_SEC = max(0.0, float(os.getenv("PLANNER_V2_BACKOFF_CAP_SEC", "0.8")))
ALLOW_PARSER_STRATEGY_AUTO_CORRECTION = os.getenv("ALLOW_PARSER_STRATEGY_AUTO_CORRECTION", "0").strip().lower() in {"1", "true", "yes", "on"}
RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT = os.getenv("RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
RAG_KEY_PJT_ID = str(os.getenv("RAG_KEY_PJT_ID", "pjt_id")).strip() or "pjt_id"
RAG_KEY_PJT_NO = str(os.getenv("RAG_KEY_PJT_NO", "pjt_no")).strip() or "pjt_no"
PLANNER_STAGE2_IDS_MAP_ALLOWED_KEYS = {
    "pjt_id",
    "pjt_no",
    "doi",
    "issn",
    "eissn",
    "pissn",
    "rst_id",
    "perf_id",
    "paper_id",
    "patent_reg_no",
    "patent_app_no",
    "person_no",
    "biz_no",
    "org_code",
    "org_id",
}
PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS = {
    "pjt_id",
    "pjt_no",
    "doi",
    "issn",
    "rst_id",
    "paper_id",
}


def _has_superlative_cue(text: str) -> bool:
    query = (text or "").strip().lower()
    return any(cue in query for cue in SUPERLATIVE_CUES)

QUESTION_ANALYSIS_REQUIRED_KEYS = {
    "strategy_version",
    "mode",
    "head",
    "action",
    "relation",
    "join_key_mode",
    "target_cols",
    "ids_map",
    "filters",
    "limit",
    "retrieval_query",
    "confidence",
}


def _select_max_tokens_hint(qa: Optional["QuestionAnalysis"]) -> Optional[int]:
    if not qa:
        return None
    if qa.mode == "JOIN":
        return FOLLOW_UP_MAX_TOKENS_HINT
    if qa.mode in (None, "SEARCH", "LOOKUP"):
        return SHORT_ANSWER_MAX_TOKENS_HINT
    return None

def _truncate_text(value: Optional[str], limit: int = HISTORY_PREVIEW_LIMIT) -> str:
    if not value:
        return ""
    text = str(value)
    if limit > 0 and len(text) > limit:
        return text[:limit] + "..."
    return text


def _is_debug_logging_enabled() -> bool:
    return str(os.getenv("RAG_DEBUG", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _mask_query_for_log(query: str, *, max_len: int = 80) -> str:
    text = str(query or "").strip()
    if not text:
        return ""
    clipped = text[:max_len]
    return clipped + ("...(truncated)" if len(text) > max_len else "")

def _derive_stream_error_code(meta: Dict[str, Any]) -> Optional[str]:
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


def _compute_total_ms_from_start(request_started_at: Optional[float]) -> Optional[float]:
    """요청 시작 시각(monotonic) 기준 end-to-end 처리 시간을 계산한다."""
    if request_started_at is None:
        return None
    elapsed_sec = time.perf_counter() - request_started_at
    if elapsed_sec < 0:
        return None
    return round(elapsed_sec * 1000.0, 1)


def _safe_json_loads(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("JSON decode failed for redis payload: %s", _truncate_text(raw))
        return None

def _serialize_history(messages: List[BaseMessage]) -> List[Dict[str, str]]:
    serialized: List[Dict[str, str]] = []
    for msg in messages:
        role = "human" if isinstance(msg, HumanMessage) else "ai"
        serialized.append({"type": role, "content": msg.content})
    return serialized

def _deserialize_history(payload: Any) -> List[BaseMessage]:
    if not isinstance(payload, list):
        return []
    history: List[BaseMessage] = []
    for msg in payload:
        if not isinstance(msg, dict):
            continue
        role = msg.get("type")
        content = msg.get("content")
        if not content:
            continue
        if role == "human":
            history.append(HumanMessage(content=content))
        else:
            history.append(AIMessage(content=content))
    return history



def _extract_stream_chunk_text_and_field(chunk: Any) -> Tuple[Optional[str], Optional[str]]:
    msg = getattr(chunk, "message", None) or chunk
    text = getattr(msg, "content", None)
    ak = getattr(msg, "additional_kwargs", {}) or {}
    return text, ak.get("stream_field")

def _normalize_none_string(value: Any) -> Any:
    if isinstance(value, str) and value.strip() == "None":
        return None
    if isinstance(value, list):
        return [_normalize_none_string(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_none_string(item) for key, item in value.items()}
    return value

def _resolve_title_from_payload(payload: Dict[str, Any]) -> str:
    for key in ("title_text", "title1", "title2"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text or text == "None":
            continue
        return text
    return ""

def _apply_title_preference(mapped_doc: Dict[str, Any]) -> None:
    preferred_title = _resolve_title_from_payload(mapped_doc)
    if preferred_title:
        mapped_doc["title"] = preferred_title

Mode = Literal["SEARCH", "LOOKUP", "JOIN"]
Head = Literal["project", "perf", "people", "org", "support"]
Action = Literal["topic", "list", "detail", "stats", "download"]

# --- Pydantic Schemas for Structured Output ---
class QuestionAnalysisV2(BaseModel):
    """질문 분석 결과(v2 Planner Schema)"""
    strategy_version: str = Field(default=PLANNER_SCHEMA_VERSION, validate_default=True)
    mode: Mode
    head: Head
    action: Action
    relation: Optional[str] = None
    join_key_mode: Literal["instance", "group"] | None = None
    ids_map: dict[str, list[str]] = Field(default_factory=dict, description="ID 추출 결과")
    filters: dict[str, Any] = Field(default_factory=dict, description="필터 파라미터")
    target_cols: list[str] = Field(default_factory=list, description="실행 대상 컬렉션")
    limit: int = Field(
        MAX_TOP_K_SIZE,
        description=f"반환 문서 개수 (최대 {MAX_TOP_K_SIZE})",
        le=MAX_TOP_K_SIZE,
    )
    retrieval_query: Optional[str] = Field(default=None, description="벡터 검색용 최적화된 쿼리")
    confidence: float = Field(ge=0.0, le=1.0, description="분석 신뢰도")
    planner_source: Optional[Literal["legacy", "stagewise"]] = Field(default=None, description="planner 생성 경로 식별자")

    _ALLOWED_RELATIONS = {"project_perf", "perf_project"}
    _FORBIDDEN_PEOPLE_ORG_RELATIONS = {
        "people_project",
        "project_people",
        "people_perf",
        "perf_people",
        "org_project",
        "project_org",
        "org_perf",
        "perf_org",
        "people_org",
        "org_people",
    }

    @model_validator(mode="before")
    @classmethod
    def normalize_planner_payload(cls, data: Any) -> Any:
        """파싱 단계에서 planner 출력 형식을 최대한 수용/정규화한다.

        - 대소문자/공백/타입 흔들림(예: ids_map의 str->list[str])을 흡수
        - JOIN 계약 위반 여부는 여기서 실패시키지 않고, 실행 직전 계약(validate_planner_contract)에서 수집한다.
        """
        if not isinstance(data, dict):
            return data

        d = dict(data)

        # --- mode/head/action/relation/join_key_mode: case/whitespace normalize ---
        mode = d.get("mode")
        if isinstance(mode, str):
            d["mode"] = mode.strip().upper()

        head = d.get("head")
        if isinstance(head, str):
            head_norm = head.strip().lower()
            # 흔한 동의어를 canonical head로 정규화
            head_alias = {
                "performance": "perf",
                "perf": "perf",
                "result": "perf",
                "results": "perf",
                "paper": "perf",
                "patent": "perf",
                "researcher": "people",
                "person": "people",
                "people": "people",
                "research": "people",
                "organization": "org",
                "org": "org",
                "institution": "org",
                "company": "org",
                "qna": "support",
                "manual": "support",
                "support": "support",
                "project": "project",
            }
            d["head"] = head_alias.get(head_norm, head_norm)

        action = d.get("action")
        if isinstance(action, str):
            act_norm = action.strip().lower()
            action_alias = {
                "details": "detail",
                "detail": "detail",
                "info": "detail",
                "view": "detail",
                "show": "detail",
                "stat": "stats",
                "stats": "stats",
                "statistics": "stats",
                "export": "download",
                "download": "download",
                "list": "list",
                "topic": "topic",
            }
            d["action"] = action_alias.get(act_norm, act_norm)

        relation = d.get("relation")
        if isinstance(relation, (list, tuple)) and len(relation) == 2:
            lhs = str(relation[0]).strip().lower()
            rhs = str(relation[1]).strip().lower()
            if lhs and rhs:
                d["relation"] = f"{lhs}_{rhs}"
            else:
                d["relation"] = None
        elif isinstance(relation, str):
            rel = relation.strip().lower()
            d["relation"] = rel or None

        jkm = d.get("join_key_mode")
        if isinstance(jkm, str):
            jkm_norm = jkm.strip().lower()
            d["join_key_mode"] = jkm_norm or None

        # --- ids_map: dict[str, list[str]]로 강제 ---
        def _coerce_str_list(value: Any) -> list[str]:
            if value is None:
                return []
            if isinstance(value, (list, tuple, set)):
                seq = list(value)
            else:
                seq = [value]
            out: list[str] = []
            for x in seq:
                s = str(x).strip()
                if not s or s.lower() == "none":
                    continue
                out.append(s)
            return out

        raw_ids_map = d.get("ids_map")
        if not isinstance(raw_ids_map, dict):
            raw_ids_map = {}

        normalized_ids_map: dict[str, list[str]] = {}
        for raw_key, raw_value in raw_ids_map.items():
            if raw_key is None:
                continue
            key = str(raw_key).strip()
            if not key:
                continue

            key_low = key.lower().replace("-", "_").replace(" ", "")
            # 자주 흔들리는 키는 canonical 형태로 보정
            if key_low in {"pjt_id", "pjtid", "project_id", "projectid", "pjtId".lower()}:
                key_low = "pjt_id"
            elif key_low in {"pjt_no", "pjtno", "project_no", "projectno", "pjtNo".lower()}:
                key_low = "pjt_no"

            values = _coerce_str_list(raw_value)
            if values:
                normalized_ids_map[key_low] = values

        d["ids_map"] = normalized_ids_map

        # --- filters: dict 보장 ---
        if not isinstance(d.get("filters"), dict):
            d["filters"] = {}

        filters = dict(d.get("filters") or {})

        # --- filters alias -> canonical key 매핑 ---
        filter_alias_to_canonical = {
            "performing_org_name": "lead_org_name",
            "main_org_name": "lead_org_name",
            "lead_org": "lead_org_name",
            "participant_org": "participant_org_name",
            "co_org_name": "participant_org_name",
            "consortium_org_name": "participant_org_name",
            "participant_researcher": "participant_researcher_name",
            "participant_researcher_names": "participant_researcher_name",
            "researcher_name": "participant_researcher_name",
            "researcher": "participant_researcher_name",
            "participant_researcher_no": "participant_researcher_id",
            "participant_researcher_ids": "participant_researcher_id",
            "researcher_id": "participant_researcher_id",
            "affiliation_org_name": "people_affiliation_org_name",
            "people_affiliation_org": "people_affiliation_org_name",
            "researcher_affiliation_org_name": "people_affiliation_org_name",
        }

        def _append_unique(dst: list[str], values: list[str]) -> list[str]:
            seen = {str(v).strip() for v in dst if str(v).strip()}
            for item in values:
                norm_item = str(item).strip()
                if not norm_item or norm_item in seen:
                    continue
                dst.append(norm_item)
                seen.add(norm_item)
            return dst

        for raw_key, raw_value in list(filters.items()):
            key_norm = str(raw_key).strip().lower().replace("-", "_")
            canonical_key = filter_alias_to_canonical.get(key_norm)
            if not canonical_key:
                continue
            merged_values = _append_unique(
                _coerce_str_list(filters.get(canonical_key)),
                _coerce_str_list(raw_value),
            )
            filters[canonical_key] = merged_values

        # 질의/필터 텍스트 role hint를 기반으로 기관 슬롯을 강제 분리한다.
        role_hint_text_chunks: list[str] = []
        for payload_key in ("query", "question", "user_query", "retrieval_query"):
            payload_value = d.get(payload_key)
            if isinstance(payload_value, str) and payload_value.strip():
                role_hint_text_chunks.append(payload_value.strip())

        for fv in filters.values():
            if isinstance(fv, str) and fv.strip():
                role_hint_text_chunks.append(fv.strip())
            elif isinstance(fv, (list, tuple, set)):
                role_hint_text_chunks.extend(str(x).strip() for x in fv if str(x).strip())

        role_hint_text = " ".join(role_hint_text_chunks)
        org_terms_for_routing = _append_unique(
            [],
            _coerce_str_list(filters.get("org_name"))
            + _coerce_str_list(filters.get("organization_name"))
            + _coerce_str_list(filters.get("organization"))
            + _coerce_str_list(filters.get("org"))
            + _coerce_str_list(filters.get("lead_org_name"))
            + _coerce_str_list(filters.get("participant_org_name"))
            + _coerce_str_list(filters.get("people_affiliation_org_name")),
        )

        if org_terms_for_routing:
            has_lead_hint = any(token in role_hint_text for token in ("수행", "주관"))
            has_participant_hint = any(token in role_hint_text for token in ("참여", "공동", "컨소시엄"))
            has_affiliation_hint = "소속" in role_hint_text

            if has_lead_hint:
                filters["lead_org_name"] = _append_unique(
                    _coerce_str_list(filters.get("lead_org_name")),
                    org_terms_for_routing,
                )
            if has_participant_hint:
                filters["participant_org_name"] = _append_unique(
                    _coerce_str_list(filters.get("participant_org_name")),
                    org_terms_for_routing,
                )
            if has_affiliation_hint:
                filters["people_affiliation_org_name"] = _append_unique(
                    _coerce_str_list(filters.get("people_affiliation_org_name")),
                    org_terms_for_routing,
                )

            # 역할 힌트가 명확하면 해당 슬롯만 유지해 기관 역할 혼용을 줄인다.
            role_hint_count = int(has_lead_hint) + int(has_participant_hint) + int(has_affiliation_hint)
            if role_hint_count == 1:
                if has_lead_hint:
                    filters["participant_org_name"] = []
                    filters["people_affiliation_org_name"] = []
                elif has_participant_hint:
                    filters["lead_org_name"] = []
                    filters["people_affiliation_org_name"] = []
                elif has_affiliation_hint:
                    filters["lead_org_name"] = []
                    filters["participant_org_name"] = []

        d["filters"] = filters

        # --- target_cols: list[str] 보장 ---
        tc = d.get("target_cols")
        if isinstance(tc, str):
            d["target_cols"] = [tc]
        elif isinstance(tc, (tuple, set)):
            d["target_cols"] = [str(x) for x in tc]
        elif not isinstance(tc, list):
            d["target_cols"] = []

        # 전략 필드(mode/relation/join_key_mode/target_cols/base_route/action) 자동 보정은
        # 기본 비활성화이며, 명시 플래그로만 허용한다.
        # 단, 위에서 수행한 정규화(ids_map/filters canonicalize 및 명시 규칙 기반 mode 보정)는
        # 파서 내 deterministic 정규화로 간주하며 AUTO_CORRECTION 플래그의 휴리스틱 보정 범위와 분리한다.
        if ALLOW_PARSER_STRATEGY_AUTO_CORRECTION:
            mode_norm = str(d.get("mode") or "").strip().upper()
            head_norm = str(d.get("head") or "").strip().lower()
            filters = d.get("filters") if isinstance(d.get("filters"), dict) else {}
            has_people_org_filters = any(
                bool(filters.get(k))
                for k in (
                    "participant_researcher_name",
                    "participant_researcher_id",
                    "lead_org_name",
                    "participant_org_name",
                    "people_affiliation_org_name",
                    "org_name",
                )
            )
            if mode_norm == "LOOKUP" and (head_norm == "project" or (head_norm in ("people", "org") and has_people_org_filters)):
                d["target_cols"] = ["ntis_project_v1"]

        # --- limit/confidence coercion (파싱 실패 방지) ---
        if "limit" in d and not isinstance(d.get("limit"), int):
            try:
                d["limit"] = int(float(str(d.get("limit"))))
            except Exception:
                pass

        if "confidence" in d and not isinstance(d.get("confidence"), (int, float)):
            try:
                d["confidence"] = float(str(d.get("confidence")))
            except Exception:
                pass

        rq = d.get("retrieval_query")
        if rq is not None and not isinstance(rq, str):
            d["retrieval_query"] = str(rq)

        return d

    @field_validator("strategy_version")
    @classmethod
    def validate_strategy_version(cls, value: str) -> str:
        if value != PLANNER_SCHEMA_VERSION:
            raise ValueError(f"strategy_version must be {PLANNER_SCHEMA_VERSION!r}")
        return value

    @model_validator(mode="after")
    def validate_join_contract(self) -> "QuestionAnalysisV2":
        """파싱 단계 JOIN validator 완화.

        JOIN 계약 위반은 여기서 파싱 실패로 만들지 않는다.
        (실행 직전 validate_planner_contract에서 수집/차단)

        단, 모드가 JOIN이 아닐 때 join_key_mode가 들어오면 실행 혼선을 막기 위해 null로 정규화한다.
        또한 사람/기관 이름 기반 질의는 SEARCH 오염 방지를 위해 LOOKUP 우선으로 정규화한다.
        """
        relation_norm = str(self.relation or "").strip().lower()
        if relation_norm:
            if relation_norm in self._FORBIDDEN_PEOPLE_ORG_RELATIONS:
                raise ValueError(f"PLANNER_RELATION_FORBIDDEN_PEOPLE_ORG:{relation_norm}")
            if relation_norm not in self._ALLOWED_RELATIONS:
                raise ValueError(f"PLANNER_RELATION_INVALID:{relation_norm}")

        # deterministic rescue: 항상 실행되어 SEARCH 오염을 줄인다.
        if self.mode != "JOIN":
            try:
                object.__setattr__(self, "join_key_mode", None)
            except Exception:
                pass

        if self.mode == "SEARCH":
            filters = dict(self.filters or {})
            name_lookup_keys = (
                "participant_researcher_name",
                "participant_researcher_names",
                "participant_researcher",
                "participant_researchers",
                "researcher_name",
                "researcher_names",
                "researcher",
                "people_name",
                "lead_org_name",
                "participant_org_name",
                "people_affiliation_org_name",
                "org_name",
                "org",
            )

            def _has_non_empty(v: Any) -> bool:
                if v is None:
                    return False
                if isinstance(v, str):
                    return bool(v.strip())
                if isinstance(v, (list, tuple, set)):
                    return any(str(x).strip() for x in v if x is not None)
                return bool(str(v).strip())

            has_name_lookup_signal = any(_has_non_empty(filters.get(k)) for k in name_lookup_keys)
            if has_name_lookup_signal:
                try:
                    object.__setattr__(self, "mode", "LOOKUP")
                except Exception:
                    pass

        # heuristic correction: 플래그가 켜졌을 때만 실행
        if not ALLOW_PARSER_STRATEGY_AUTO_CORRECTION:
            return self

        if self.mode == "JOIN":
            ids_map = dict(self.ids_map or {})
            pjt_ids = [str(v).strip() for v in ids_map.get("pjt_id", []) if str(v).strip()]
            pjt_nos = [str(v).strip() for v in ids_map.get("pjt_no", []) if str(v).strip()]
            if self.join_key_mode == "group" and (not pjt_nos) and pjt_ids:
                logger.warning(
                    "[PLANNER] JOIN join_key_mode auto-correction: group->instance (reason=missing_pjt_no has_pjt_id=1)"
                )
                try:
                    object.__setattr__(self, "join_key_mode", "instance")
                except Exception:
                    pass

        return self


QuestionAnalysis = QuestionAnalysisV2

class PlannerV2ParseError(ValueError):
    """QuestionAnalysisV2 파싱/검증 실패."""


def _validate_question_analysis_required_keys(payload: Dict[str, Any]) -> None:
    missing_keys = sorted(QUESTION_ANALYSIS_REQUIRED_KEYS - set(payload.keys()))
    if missing_keys:
        raise PlannerV2ParseError(f"missing required keys: {missing_keys}")


def _planner_v2_backoff_seconds(attempt_no: int) -> float:
    backoff = PLANNER_V2_RETRY_BACKOFF_SEC * (2 ** max(0, attempt_no - 1))
    return min(backoff, PLANNER_V2_BACKOFF_CAP_SEC)


def merge_bool_flag(existing: bool, new: bool) -> bool:
    """병렬 업데이트되는 bool state를 OR로 병합한다."""
    return bool(existing) or bool(new)


class KnowledgeSufficiency(BaseModel):
    """지식 충분성 판단 결과"""
    requires_new_knowledge: Literal["low", "medium", "high"] = Field(
        description="새로운 검색 필요도: low(이전 지식으로 충분), medium(보강 필요), high(새로운 검색 필수)"
    )
    search_intent: str = Field(description="검색이 필요한 경우, 검색 의도 설명")
    retrieval_query: str = Field(description="벡터 검색용 최적화된 쿼리")
    confidence: float = Field(ge=0.0, le=1.0, description="판단 신뢰도")

class RuleDecision(BaseModel):
    """Rule-based 판단 결과"""
    action: Literal["direct_answer", "skip", "proceed"] = Field(description="처리 방식")
    direct_response: Optional[str] = Field(default=None, description="즉시 답변 (있을 경우)")
    reason: str = Field(description="판단 근거")

# --- Agent State ---
class AgentState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: Annotated[List[BaseMessage], add_messages]

    # Redis 데이터
    chat_history: List[BaseMessage] = Field(default_factory=list)
    prev_context: List[Dict] = Field(default_factory=list)

    # 처리 데이터
    context: List[Dict] = Field(default_factory=list)
    fallback_context: Optional[str] = None
    rendered_context_used: Annotated[bool, merge_bool_flag] = False
    rendered_context_used_gemma: bool = False
    rendered_context_used_solar: bool = False
    fallback_context_used: Annotated[bool, merge_bool_flag] = False
    degraded: Annotated[bool, merge_bool_flag] = False

    # 각 모델별 답변 저장
    answer_gemma: Optional[str] = None
    answer_solar: Optional[str] = None
    answer_gemma_meta: Dict[str, Any] = Field(default_factory=dict)
    answer_solar_meta: Dict[str, Any] = Field(default_factory=dict)
    answer_solar_raw: Optional[str] = None
    merge_debug: Dict[str, Any] = Field(default_factory=dict)

    # 메타데이터
    conversation_id: str = ""
    request_id: str = ""
    request_started_at: Optional[float] = None

    question: str = ""

    rule_decision: Optional[RuleDecision] = None
    question_analysis: Optional[QuestionAnalysis] = None
    knowledge_sufficiency: Optional[KnowledgeSufficiency] = None
    intent_payload: Optional[IntentPayloadV2] = None

    def merge_latencies(existing: Dict[str, float], new: Dict[str, float]) -> Dict[str, float]:
        """병렬 노드에서 latencies가 동시에 업데이트될 때 병합"""
        result = existing.copy()
        result.update(new)
        return result

    # 단계별 진단용 지표(노드별 소요시간)이며, 요청 총 처리시간(total_ms) 계산에는 사용하지 않는다.
    latencies: Annotated[Dict[str, float], merge_latencies] = Field(default_factory=dict)

    def merge_stream_meta(existing: Dict[str, Dict[str, Any]], new: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        result = existing.copy()
        result.update(new)
        return result

    stream_meta: Annotated[Dict[str, Dict[str, Any]], merge_stream_meta] = Field(default_factory=dict)

# --- Utility: Latency Decorator ---
def measure_latency(node_name: str):
    """노드 실행 시간 측정 데코레이터"""
    def decorator(func):
        async def wrapper(state: AgentState, *args, **kwargs):
            start = time.perf_counter()
            result = await func(state, *args, **kwargs)
            elapsed = time.perf_counter() - start

            if isinstance(result, dict):
                latencies = result.get("latencies", state.latencies.copy())
                latencies[node_name] = round(elapsed, 3)
                result["latencies"] = latencies

            logger.info(f"⏱️ {node_name}: {elapsed:.3f}s")
            return result
        return wrapper
    return decorator



def _format_coq(conversation_id: str, question: str) -> str:
    return f"coq: {conversation_id} | q: {question}"

# --- Node 1: Load Memory ---
@measure_latency("load_memory")
async def node_load_memory(state: AgentState) -> Dict[str, Any]:
    """Redis에서 대화 이력 및 이전 컨텍스트 로드"""
    cid = state.conversation_id
    loaded_history, ctx_list, fallback_context = await load_conversation_memory(cid)
    current_full_history = loaded_history + [state.messages[-1]]

    _log_event("LOAD.MEMORY", request_id=state.request_id, conversation_id=cid, stage="load_memory", history_turns=len(loaded_history), prev_context_docs=len(ctx_list))
    return {
        "question": state.messages[-1].content,
        "chat_history": current_full_history,
        "prev_context": ctx_list,
        "fallback_context": fallback_context
    }

# --- Node 2: Rule-based Precheck ---
def _is_short_query_exception(raw_query: str) -> bool:
    """짧은 질의라도 약어/코드/ID 패턴이면 검색 플로우를 허용한다."""
    text = str(raw_query or "").strip()
    if not text:
        return False

    compact = re.sub(r"[\s\-_/]", "", text)
    if re.fullmatch(r"\d{6,}", compact):
        return True
    if re.fullmatch(r"[A-Z]{3,}", compact):
        return True
    return False


@measure_latency("rule_precheck")
async def node_rule_precheck(state: AgentState) -> Dict[str, Any]:
    """규칙 기반 빠른 판단"""
    raw_user_msg = state.messages[-1].content.strip()
    user_msg = raw_user_msg.lower()

    greetings = ["안녕", "hello", "hi", "헬로", "반가워", "ㅎㅇ"]
    if any(g in user_msg for g in greetings) and len(user_msg) < 10:
        return {
            "rule_decision": RuleDecision(
                action="direct_answer",
                direct_response="안녕하세요! 무엇을 도와드릴까요?",
                reason="Simple greeting detected"
            )
        }

    if len(raw_user_msg) < 5 and not _is_short_query_exception(raw_user_msg):
        return {
            "rule_decision": RuleDecision(
                action="direct_answer",
                direct_response="질문을 좀 더 구체적으로 작성해주세요!",
                reason="Query too short"
            )
        }

    return {
        "rule_decision": RuleDecision(
            action="proceed",
            reason="Standard query - proceed to analysis"
        )
    }

# --- Node 3: Analyze Question ---
@measure_latency("analyze_question")
async def node_analyze_question(state: AgentState) -> Dict[str, Any]:
    """질문 분석: 카테고리, 후속 질문 유형, 이력 요약"""
    if state.question_analysis and state.intent_payload:
        return {
            "question_analysis": state.question_analysis,
            "intent_payload": state.intent_payload,
        }

    intent_payload, question_analysis = await build_intent_payload(
        question=state.messages[-1].content,
        conversation_id=state.conversation_id,
        chat_history=state.chat_history,
        prev_context=state.prev_context,
        request_id=getattr(state, "request_id", None),
    )
    return {
        "question_analysis": question_analysis,
        "intent_payload": intent_payload,
    }


async def _run_question_analysis(
        *,
        question: str,
        conversation_id: str,
        chat_history: List[BaseMessage],
        prev_context: List[Dict[str, Any]],
        request_id: Optional[str] = None,
        researchers: Optional[List[Any]] = None,
        normalized_intent_base: Optional[NormalizedIntent] = None,
) -> QuestionAnalysis:
    if PLANNER_STAGEWISE_ENABLED:
        normalized_intent = normalized_intent_base or normalize_intent(
            classify_query_intent(question, [], hint={}),
            query=question,
            keywords=[],
            allow_strategy_fallback=False,
        )
        stage1 = await _run_planner_stage1(
            question=question,
            conversation_id=conversation_id,
            request_id=request_id,
            chat_history=chat_history,
            prev_context=prev_context,
            normalized_intent=normalized_intent,
        )
        locked_strategy = _determine_locked_strategy(
            question=question,
            stage1=stage1,
            normalized_intent=normalized_intent,
            prev_context=prev_context,
        )
        stage2 = await _run_planner_stage2(
            question=question,
            conversation_id=conversation_id,
            request_id=request_id,
            chat_history=chat_history,
            prev_context=prev_context,
            normalized_intent=normalized_intent,
            locked_strategy=locked_strategy,
        )
        locked_strategy = _re_gate_locked_strategy(
            request_id=request_id,
            conversation_id=conversation_id,
            stage1=stage1,
            stage2=stage2,
            locked_strategy=locked_strategy,
        )
        return _assemble_question_analysis(
            question=question,
            conversation_id=conversation_id,
            request_id=request_id,
            stage1=stage1,
            stage2=stage2,
            locked_strategy=locked_strategy,
        )

    return await _run_question_analysis_legacy(
        question=question,
        conversation_id=conversation_id,
        chat_history=chat_history,
        prev_context=prev_context,
        request_id=request_id,
        researchers=researchers,
    )


async def _run_question_analysis_legacy(
        *,
        question: str,
        conversation_id: str,
        chat_history: List[BaseMessage],
        prev_context: List[Dict[str, Any]],
        request_id: Optional[str] = None,
        researchers: Optional[List[Any]] = None,
) -> QuestionAnalysis:
    llm = _build_llm("solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=QuestionAnalysis)

    history = chat_history[-6:]
    history_str = "\n".join([f"{type(m).__name__}: {m.content}" for m in history])
    prev_context_raw = refine_documents_rule_based(prev_context, researchers=researchers, organizations=None, org_filters=None, ids_map=None)
    prev_context_str = "\n".join([line.strip() for line in (prev_context_raw or '').splitlines() if line.strip()][:8])

    prompt_path = Path("prompts/planner_legacy_v2.md")
    system_prompt = await load_prompt_file(prompt_path)
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", """[format instructions]\n{format_instructions}\n\n[대화 이력]\n{history}\n\n[이전 검색문맥 요약]\n{prev_context}\n\n[질문]\n{question}"""),
    ])
    planner_llm = llm.bind(reasoning_effort="low", include_reasoning=False, disable_thinking=PLANNER_DISABLE_THINKING, temperature=PLANNER_TEMPERATURE, top_p=PLANNER_TOP_P, max_tokens=PLANNER_MAX_TOKENS)
    chain = prompt | planner_llm | sanitize_llm_json | parser
    result: QuestionAnalysis = await chain.ainvoke({
        "format_instructions": parser.get_format_instructions(),
        "history": history_str or "없음",
        "prev_context": prev_context_str or "없음",
        "question": question,
    })
    normalized_payload = _normalize_none_string(result.model_dump())
    normalized_payload.setdefault("planner_source", "legacy")
    _validate_question_analysis_required_keys(normalized_payload)
    result = QuestionAnalysis.model_validate(normalized_payload)
    _log_event(
        "PLANNER.PIPELINE",
        request_id=request_id,
        conversation_id=conversation_id,
        step="parse",
        status="success",
        stage="planner_legacy",
        mode=result.mode,
        head=result.head,
        action=result.action,
        relation=result.relation,
        join_key_mode=result.join_key_mode,
        planner_stagewise_enabled=int(PLANNER_STAGEWISE_ENABLED),
        planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
        planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
    )
    return result


def _intent_snapshot(normalized_intent: NormalizedIntent) -> dict[str, Any]:
    return {
        "action": getattr(normalized_intent, "action", None),
        "base_route": getattr(normalized_intent, "base_route", None),
        "is_id_query": bool(getattr(normalized_intent, "is_id_query", False)),
        "people_terms": list(getattr(normalized_intent, "people_terms", []) or []),
        "org_terms": list(getattr(normalized_intent, "org_terms", []) or []),
        "perf_types": list(getattr(normalized_intent, "perf_types", []) or []),
        "years": list(getattr(normalized_intent, "years", []) or []),
    }


async def _run_planner_stage1(*, question: str, conversation_id: str, request_id: Optional[str], chat_history: list[BaseMessage], prev_context: list[dict[str, Any]], normalized_intent: NormalizedIntent) -> PlannerStage1Decision:
    llm = _build_llm("solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=PlannerStage1Decision)
    history_str = "\n".join([f"{type(m).__name__}: {m.content}" for m in chat_history[-4:]])
    prev_lines = [json.dumps(item, ensure_ascii=False)[:180] for item in (prev_context or [])[:6]]
    system_prompt = await load_prompt_file(Path(f"prompts/planner_stage1_{PLANNER_STAGE1_PROMPT_VERSION}.md"))
    prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("human", "{format_instructions}\n<user_query>{question}</user_query>\n<history>{history}</history>\n<prev_context>{prev_context}</prev_context>\n<intent_snapshot>{intent_snapshot}</intent_snapshot>")])
    planner_llm = llm.bind(reasoning_effort="low", include_reasoning=False, disable_thinking=PLANNER_DISABLE_THINKING, temperature=PLANNER_TEMPERATURE, top_p=1.0, max_tokens=250)
    chain = prompt | planner_llm | sanitize_llm_json | parser
    stage1: PlannerStage1Decision = await chain.ainvoke({
        "format_instructions": parser.get_format_instructions(),
        "question": question,
        "history": history_str or "없음",
        "prev_context": "\n".join(prev_lines) or "없음",
        "intent_snapshot": json.dumps(_intent_snapshot(normalized_intent), ensure_ascii=False),
    })
    _log_event("PLANNER.STAGE1", request_id=request_id, conversation_id=conversation_id, action=stage1.action, head=stage1.head, relation_candidate=stage1.relation_candidate, referential_followup=int(stage1.referential_followup), confidence=round(stage1.confidence, 3), planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION)
    return stage1


def _extract_single_project_seed(prev_context: list[dict[str, Any]]) -> dict[str, list[str]]:
    pjt_ids, pjt_nos = set(), set()
    for doc in prev_context or []:
        payload = doc if isinstance(doc, dict) else {}
        for key in ("pjt_id", "project_id"):
            val = str(payload.get(key) or "").strip()
            if val:
                pjt_ids.add(val)
        for key in ("pjt_no", "project_no"):
            val = str(payload.get(key) or "").strip()
            if val:
                pjt_nos.add(val)
    if len(pjt_ids) == 1:
        return {"pjt_id": [next(iter(pjt_ids))]}
    if len(pjt_nos) == 1:
        return {"pjt_no": [next(iter(pjt_nos))]}
    return {}


def _has_join_seed_id(ids_map: dict[str, list[str]]) -> bool:
    keys = {"pjt_id", "pjt_no", "doi", "issn", "eissn", "pissn", "perf_id", "rst_id", "paper_id", "patent_reg_no", "patent_app_no"}
    return any(bool(ids_map.get(k)) for k in keys)


def _collect_regate_seed_map(ids_map: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, values in (ids_map or {}).items():
        if key not in PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS and not key.startswith("patent_"):
            continue
        normalized = sorted({str(v).strip() for v in (values or []) if str(v).strip()})
        if normalized:
            out[key] = normalized
    return out


def _has_new_regate_seed(*, base_seed_map: dict[str, list[str]], stage2_seed_map: dict[str, list[str]]) -> bool:
    for key, values in stage2_seed_map.items():
        base_values = set(base_seed_map.get(key) or [])
        if any(value not in base_values for value in values):
            return True
    return False


def _determine_locked_strategy(*, question: str, stage1: PlannerStage1Decision, normalized_intent: NormalizedIntent, prev_context: list[dict[str, Any]]) -> dict[str, Any]:
    base_ids_map = dict(getattr(normalized_intent, "ids_map", {}) or {})
    prev_context_seed = _extract_single_project_seed(prev_context)
    gate_seed_map = _collect_regate_seed_map({**base_ids_map, **prev_context_seed})
    if stage1.action == "topic":
        mode = "SEARCH"
    else:
        mode = "LOOKUP"
    relation = None
    join_key_mode = None
    if stage1.relation_candidate is not None:
        has_seed = _has_join_seed_id(base_ids_map) or bool(prev_context_seed)
        if has_seed and stage1.action in {"list", "detail", "stats", "download"}:
            mode = "JOIN"
            relation = stage1.relation_candidate
            if base_ids_map.get("pjt_no") or prev_context_seed.get("pjt_no"):
                join_key_mode = "group"
            else:
                join_key_mode = "instance"

    if stage1.head == "support":
        target_cols = ["ntis_supports_v1"]
    elif mode == "JOIN":
        target_cols = ["ntis_project_v1", "ntis_perf_v1"]
    elif stage1.head == "perf":
        target_cols = ["ntis_perf_v1"]
    else:
        target_cols = ["ntis_project_v1"]

    locked = {"mode": mode, "head": stage1.head, "action": stage1.action, "relation": relation, "join_key_mode": join_key_mode, "target_cols": target_cols, "prev_context_seed": prev_context_seed, "gate_seed_map": gate_seed_map}
    _log_event("PLANNER.GATE", stage1_action=stage1.action, stage1_head=stage1.head, stage1_relation_candidate=stage1.relation_candidate, gate_mode=mode, gate_relation=relation, gate_join_key_mode=join_key_mode, gate_target_cols=target_cols, used_prev_context_seed=int(bool(prev_context_seed)))
    return locked


def _re_gate_locked_strategy(*, request_id: Optional[str], conversation_id: str, stage1: PlannerStage1Decision, stage2: PlannerStage2Slots, locked_strategy: dict[str, Any]) -> dict[str, Any]:
    stage2_seed_map = _collect_regate_seed_map(stage2.ids_map)
    base_seed_map = dict(locked_strategy.get("gate_seed_map") or {})
    can_regate = bool(stage1.relation_candidate) and locked_strategy.get("mode") in {"SEARCH", "LOOKUP"} and _has_new_regate_seed(base_seed_map=base_seed_map, stage2_seed_map=stage2_seed_map)

    updated = dict(locked_strategy)
    if can_regate:
        mode = "SEARCH" if stage1.action == "topic" else "LOOKUP"
        relation = None
        join_key_mode = None
        merged_seed_map = {**base_seed_map}
        for key, values in stage2_seed_map.items():
            merged = set(merged_seed_map.get(key) or [])
            merged.update(values)
            merged_seed_map[key] = sorted(merged)

        if _has_join_seed_id(merged_seed_map) and stage1.action in {"list", "detail", "stats", "download"}:
            mode = "JOIN"
            relation = stage1.relation_candidate
            join_key_mode = "group" if merged_seed_map.get("pjt_no") else "instance"

        if stage1.head == "support":
            target_cols = ["ntis_supports_v1"]
        elif mode == "JOIN":
            target_cols = ["ntis_project_v1", "ntis_perf_v1"]
        elif stage1.head == "perf":
            target_cols = ["ntis_perf_v1"]
        else:
            target_cols = ["ntis_project_v1"]

        updated.update({"mode": mode, "relation": relation, "join_key_mode": join_key_mode, "target_cols": target_cols, "gate_seed_map": merged_seed_map})

    changed = any(updated.get(field) != locked_strategy.get(field) for field in ("mode", "relation", "join_key_mode", "target_cols"))
    _log_event(
        "PLANNER.REGATE",
        request_id=request_id,
        conversation_id=conversation_id,
        regate_eligible=int(can_regate),
        regate_changed=int(changed),
        before_mode=locked_strategy.get("mode"),
        after_mode=updated.get("mode"),
        before_relation=locked_strategy.get("relation"),
        after_relation=updated.get("relation"),
        before_join_key_mode=locked_strategy.get("join_key_mode"),
        after_join_key_mode=updated.get("join_key_mode"),
        before_target_cols=locked_strategy.get("target_cols"),
        after_target_cols=updated.get("target_cols"),
    )
    return updated


async def _run_planner_stage2(*, question: str, conversation_id: str, request_id: Optional[str], chat_history: list[BaseMessage], prev_context: list[dict[str, Any]], normalized_intent: NormalizedIntent, locked_strategy: dict[str, Any]) -> PlannerStage2Slots:
    llm = _build_llm("solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=PlannerStage2Slots)
    system_prompt = await load_prompt_file(Path(f"prompts/planner_stage2_{PLANNER_STAGE2_PROMPT_VERSION}.md"))
    prompt = ChatPromptTemplate.from_messages([("system", system_prompt), ("human", "{format_instructions}\n<locked_strategy>{locked_strategy}</locked_strategy>\n<user_query>{question}</user_query>")])
    planner_llm = llm.bind(reasoning_effort="low", include_reasoning=False, disable_thinking=PLANNER_DISABLE_THINKING, temperature=PLANNER_TEMPERATURE, top_p=1.0, max_tokens=300)
    chain = prompt | planner_llm | sanitize_llm_json | parser
    slots: PlannerStage2Slots = await chain.ainvoke({"format_instructions": parser.get_format_instructions(), "question": question, "locked_strategy": json.dumps(locked_strategy, ensure_ascii=False)})
    _log_event("PLANNER.STAGE2", request_id=request_id, conversation_id=conversation_id, confidence=round(slots.confidence, 3), planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION)
    return slots


def _sanitize_ids_map_semantics(ids_map: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    cleaned: dict[str, list[str]] = {}
    invalid: list[dict[str, Any]] = []
    patterns = {
        "pjt_id": re.compile(r"^\d{8,12}$"),
        "doi": re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.I),
        "issn": re.compile(r"^\d{4}-\d{3}[\dXx]$"),
        "eissn": re.compile(r"^\d{4}-\d{3}[\dXx]$"),
        "pissn": re.compile(r"^\d{4}-\d{3}[\dXx]$"),
        "patent_reg_no": re.compile(r"^[A-Za-z0-9\-]{6,}$"),
        "patent_app_no": re.compile(r"^[A-Za-z0-9\-]{6,}$"),
        "rst_id": re.compile(r"^(?:RPT|RST)-?[A-Za-z0-9\-]{2,}$", re.I),
        "perf_id": re.compile(r"^(?:PERF|PFM)-?[A-Za-z0-9\-]{2,}$", re.I),
        "paper_id": re.compile(r"^(?:PAP|PAPER)-?[A-Za-z0-9\-]{2,}$", re.I),
        "person_no": re.compile(r"^\d{6,12}$"),
        "biz_no": re.compile(r"^\d{3}-?\d{2}-?\d{5}$"),
        "org_code": re.compile(r"^[A-Z][A-Z0-9_\-]{2,15}$"),
        "org_id": re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{2,31}$"),
    }
    hangul_only = re.compile(r"^[가-힣\s]+$")
    for key, values in (ids_map or {}).items():
        if key not in PLANNER_STAGE2_IDS_MAP_ALLOWED_KEYS:
            for raw in values or []:
                value = str(raw).strip()
                if value:
                    invalid.append({"key": key, "value": value})
            continue
        out=[]
        for raw in values or []:
            value=str(raw).strip()
            if not value:
                continue
            ok=True
            if key == "pjt_no":
                ok = not bool(hangul_only.match(value))
            elif key in patterns:
                ok = bool(patterns[key].match(value))
            if not ok:
                invalid.append({"key": key, "value": value})
                continue
            out.append(value)
        if out:
            cleaned[key]=out
    return cleaned, invalid


def _assemble_question_analysis(*, question: str, conversation_id: str, request_id: Optional[str], stage1: PlannerStage1Decision, stage2: PlannerStage2Slots, locked_strategy: dict[str, Any]) -> QuestionAnalysis:
    ids_map, invalids = _sanitize_ids_map_semantics(stage2.ids_map)
    for item in invalids:
        _log_event("PLANNER.IDS_MAP.INVALID_VALUE", request_id=request_id, conversation_id=conversation_id, key=item["key"], value=item["value"])
    payload = {
        "strategy_version": PLANNER_SCHEMA_VERSION,
        "mode": locked_strategy["mode"],
        "head": locked_strategy["head"],
        "action": locked_strategy["action"],
        "relation": locked_strategy["relation"],
        "join_key_mode": locked_strategy["join_key_mode"],
        "target_cols": locked_strategy["target_cols"],
        "ids_map": ids_map,
        "filters": stage2.filters,
        "limit": min(stage2.limit, MAX_TOP_K_SIZE),
        "retrieval_query": stage2.retrieval_query or question,
        "confidence": min(stage1.confidence, stage2.confidence),
        "planner_source": "stagewise",
    }
    qa = QuestionAnalysis.model_validate(payload)
    _log_event("PLANNER.ASSEMBLE", request_id=request_id, conversation_id=conversation_id, mode=qa.mode, action=qa.action, relation=qa.relation, join_key_mode=qa.join_key_mode, target_cols=qa.target_cols, planner_stagewise_enabled=int(PLANNER_STAGEWISE_ENABLED), planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION, planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION)
    return qa


# --- Node 4: Knowledge Sufficiency Judge ---
@measure_latency("knowledge_sufficiency")
async def node_knowledge_sufficiency(state: AgentState) -> Dict[str, Any]:
    """지식 충분성 판단: 새로운 검색 필요 여부"""

    history = state.chat_history[-6:]
    history_str = "\n".join([f"{type(m).__name__}: {m.content}" for m in history])

    qa = state.question_analysis

    retrieval_query = (qa.retrieval_query if qa else None) or state.messages[-1].content

    query_intent = None
    if state.intent_payload:
        query_intent = getattr(state.intent_payload, "normalized_intent", None)
    action = None
    if query_intent:
        if isinstance(query_intent, dict):
            action = query_intent.get("action")
        else:
            action = getattr(query_intent, "action", None)

    search_required_actions = {
        "list",
        "detail",
        "relation",
        "stats",
        "id_exact",
        "id_fuzzy",
        "topic",
        "content",
    }

    if qa and not state.prev_context:
        result = KnowledgeSufficiency(
            requires_new_knowledge="high",
            search_intent="이전 문맥이 없어 새로운 검색이 필요함",
            retrieval_query=retrieval_query,
            confidence=1.0,
        )
        _log_event("KS.RESULT",
                   request_id=state.request_id, conversation_id=state.conversation_id, stage="knowledge_sufficiency", requires_new_knowledge=result.requires_new_knowledge, retrieval_query=result.retrieval_query, confidence=round(float(result.confidence), 2))
        return {"knowledge_sufficiency": result}

    if action in search_required_actions:
        result = KnowledgeSufficiency(
            requires_new_knowledge="high",
            search_intent=f"query_intent action={action} 검색이 필요함",
            retrieval_query=retrieval_query,
            confidence=1.0,
        )
        _log_event("KS.RESULT",
                   request_id=state.request_id, conversation_id=state.conversation_id, stage="knowledge_sufficiency", requires_new_knowledge=result.requires_new_knowledge, retrieval_query=result.retrieval_query, confidence=round(float(result.confidence), 2))
        return {"knowledge_sufficiency": result}

    llm = TritonChatModel(model_name="gemma_triton_0")
    parser = PydanticOutputParser(pydantic_object=KnowledgeSufficiency)

    prev_context_str = None


    if qa and qa.mode == "JOIN":
        prev_context_str = refine_documents_rule_based(
            state.prev_context,
            True,
            org_filters=(qa.filters if qa else None),
            ids_map=(qa.ids_map if qa else None),
        )
    else:
        prev_context_str = refine_documents_rule_based(
            state.prev_context,
            org_filters=(qa.filters if qa else None),
            ids_map=(qa.ids_map if qa else None),
        )


    system_prompt = (
        "당신은 지식 충분성 판단 전문가입니다.\n"
        "이 시스템에서 사용되는 용어는 모두 국가 연구개발(R&D) 행정 및 제도 맥락으로 해석합니다.\n"
        "[대화 이력]과 [참고 문서]를 기반으로, [현재 질문]에 답하기 위해 새로운 검색이 필요한지 판단하세요.\n\n"
        "판단 기준:\n"
        "1. requires_new_knowledge:\n"
        "   - low: [참고 문서] 만으로 충분히 답변 가능\n"
        "   - medium: [참고 문서]로 일부 답변 가능하나, 보강 필요\n"
        "   - high: [참고 문서]로 답변 불가하거나 새로운 정보 요청\n\n"
        "2. search_intent: 검색이 필요한 경우, 무엇을 찾아야 하는지 설명\n"
        "3. retrieval_query:\n"
        "   - search_intent 기반 벡터 검색에 최적화된 짧은 쿼리\n"
        "   - 키워드 또는 짧은 구 형태\n"
        "   - 핵심 개념 5개 이내\n"
        "   - 최대 120자 이내\n"
        "4. confidence: 판단 신뢰도 (0.0~1.0)\n\n"
        "{format_instructions}"
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human",
         "[대화 이력]\n{history}\n\n"
         "[참고 문서]\n{prev_context}\n\n"
         "[현재 질문]\n{question}")
    ])

    try:
        chain = prompt | llm | sanitize_llm_json | parser
        result: KnowledgeSufficiency = await chain.ainvoke({
            "format_instructions": parser.get_format_instructions(),
            "history": history_str or '없음',
            "prev_context": prev_context_str or "없음",
            "question": state.messages[-1].content
        })

        _log_event("KS.RESULT",
                   request_id=state.request_id, conversation_id=state.conversation_id, stage="knowledge_sufficiency", requires_new_knowledge=result.requires_new_knowledge, retrieval_query=result.retrieval_query, confidence=round(float(result.confidence), 2))

        return {"knowledge_sufficiency": result}

    except Exception as e:
        logger.error(f"Knowledge Sufficiency Error: {e}")
        return {
            "knowledge_sufficiency": KnowledgeSufficiency(
                requires_new_knowledge="high",
                search_intent="일반 검색",
                retrieval_query=state.messages[-1].content,
                confidence=0.5
            )
        }


# --- Node 6: RAG Search (Parallel) ---
class CustomRAGRetriever(BaseModel):
    """RAG Pipeline을 Tool로 래핑"""
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
    )

    model_name: str = "gemma_triton_0"
    top_k: int = 5

    intent_payload: Optional[IntentPayloadV2] = None

    @staticmethod
    def _infer_tag_from_hit_data(hit_data: Dict[str, Any], intent_payload: Optional[IntentPayloadV2] = None) -> Optional[str]:
        tag = hit_data.get("tag")
        if tag:
            return str(tag)

        collection = str(hit_data.get("_collection") or "").strip().lower()
        if collection.startswith("ntis_project"):
            return "IRD_NAI_PJT_INFO"
        if collection.startswith("ntis_perf"):
            # 성과 컬렉션에서 태그가 누락된 경우 기본 성과 스키마로 보정
            return "IRD_NAI_RI_PAPER"

        normalized_intent = getattr(intent_payload, "normalized_intent", None)
        target_cols = getattr(normalized_intent, "target_cols", None) if normalized_intent else None
        if isinstance(target_cols, list):
            lowered = [str(c).strip().lower() for c in target_cols]
            if any(c.startswith("ntis_project") for c in lowered):
                return "IRD_NAI_PJT_INFO"
            if any(c.startswith("ntis_perf") for c in lowered):
                return "IRD_NAI_RI_PAPER"

        return None

    @staticmethod
    def _has_minimum_document_fields(hit_data: Dict[str, Any]) -> bool:
        candidates = [
            hit_data.get("doc_id"),
            hit_data.get("title"),
            hit_data.get("title_text"),
            hit_data.get("title1"),
            hit_data.get("content"),
            hit_data.get("meta_basic"),
            hit_data.get("meta_detail"),
            hit_data.get("prtcp_mp"),
            hit_data.get("prtcp_org"),
        ]
        for val in candidates:
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            return True
        return False

    @staticmethod
    def _build_rag_intent_payload(intent_payload: Optional[IntentPayloadV2]) -> Optional[Dict[str, Any]]:
        """RAG intent_payload.v2 송신 계약: normalized_intent 단일 필드만 전달."""
        if intent_payload is None:
            return None
        normalized_intent = getattr(intent_payload, "normalized_intent", None)
        if not isinstance(normalized_intent, NormalizedIntent):
            return None
        return {"normalized_intent": normalized_intent}

    def retrieve(self, query: str) -> Dict[str, Any]:
        """동기 검색 함수"""
        res_map = run_rag_ab_compare(
            query=query,
            model_name=self.model_name,
            intent_payload=self._build_rag_intent_payload(self.intent_payload),
        )
        res_m = res_map.get("M") or res_map.get("A") or next(iter(res_map.values()))

        hits = getattr(res_m, "reranked_hits", []) or []
        fallback_context = getattr(res_m, "context", "")
        aggregation = getattr(res_m, "aggregation", None) or {}

        rank_items = aggregation.get("rank_items") if isinstance(aggregation, dict) else None
        if isinstance(rank_items, list) and rank_items:
            agg_metric = str(aggregation.get("metric") or "project_participation_count")
            agg_candidate_docs = int(aggregation.get("candidate_docs") or 0)
            agg_window_years = aggregation.get("window_years") or {}
            documents = []
            for idx, item in enumerate(rank_items[:self.top_k], start=1):
                documents.append({
                    "title": f"{idx}. {item.get('hm_nm') or item.get('hm_id') or item.get('person_key')}",
                    "source_index": idx,
                    "source_type": "aggregation",
                    "metric": agg_metric,
                    "window_years": agg_window_years,
                    "candidate_docs": agg_candidate_docs,
                    "rank_item": dict(item),
                })
            return {
                "documents": documents,
                "fallback_context": fallback_context.strip() or None,
            }

        if not hits:
            return {
                "documents": [],
                "fallback_context": fallback_context.strip() or None,
            }

        documents = []
        for idx, hit in enumerate(hits[:self.top_k], start=1):
            if hasattr(hit, "payload"):
                hit_data = hit.payload
            elif isinstance(hit, dict):
                hit_data = hit
            else:
                hit_data = getattr(hit, "__dict__", {})

            inferred_tag = self._infer_tag_from_hit_data(hit_data, self.intent_payload)
            rag_data = {
                "title": _resolve_title_from_payload(hit_data),
                "source_index" : idx,
                "source_type": "hit",
                "tag" : inferred_tag,
                "doc_id": hit_data.get("doc_id"),
                "_collection": hit_data.get("_collection"),
                "meta_basic" : hit_data.get("meta_basic", {}),
                "meta_detail" : hit_data.get("meta_detail", {}),
                "prtcp_mp" : hit_data.get("prtcp_mp", []),
                "prtcp_org": hit_data.get("prtcp_org", []) or [],
            }

            if inferred_tag is not None or self._has_minimum_document_fields(hit_data):
                documents.append(rag_data)

        return {
            "documents": documents,
            "fallback_context": fallback_context.strip() or None,
        }


def _is_hit_source(doc: Dict[str, Any]) -> bool:
    return doc.get("source_type", "hit") == "hit"

def _friendly_strategy_violation_message(
        *,
        error_code: str,
        reason: str,
        question_analysis: Optional[QuestionAnalysis],
) -> str:
    mode = str(getattr(question_analysis, "mode", "") or "").strip().upper()
    action = str(getattr(question_analysis, "action", "") or "").strip().lower()
    ids_map = getattr(question_analysis, "ids_map", None) or {}
    has_explicit_id = isinstance(ids_map, dict) and any(bool(v) for v in ids_map.values())

    if error_code == "RAG_EMPTY_RESULT_CONTRACT" and mode == "LOOKUP" and (action == "detail" or has_explicit_id):
        return "요청하신 식별자(ID)에 해당하는 상세 정보를 찾지 못했습니다. ID를 다시 확인해 주세요."
    return "요청을 처리하는 중 검색 전략 계약 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

def _resolve_rag_queries(
        state: AgentState,
        qa: Optional[QuestionAnalysis],
        ks: Optional[KnowledgeSufficiency],
) -> tuple[str, str, str, float]:
    raw_query = state.question
    hint_query = (ks.retrieval_query if ks else None) or (qa.retrieval_query if qa else None) or raw_query
    qa_confidence = float(qa.confidence) if qa else None
    ks_confidence = float(ks.confidence) if ks else None
    confidence = qa_confidence if qa_confidence is not None else (ks_confidence if ks_confidence is not None else 0.0)
    min_confidence = float(os.getenv("RAG_HINT_MIN_CONF", "0.55"))
    search_query = hint_query if confidence >= min_confidence else raw_query
    return raw_query, hint_query, search_query, confidence


@measure_latency("rag_search")
async def node_rag_search(state: AgentState) -> Dict[str, Any]:
    """RAG 검색 수행 (병렬 실행)"""

    ks = state.knowledge_sufficiency
    qa = state.question_analysis

    try:
        raw_query, hint_query, search_query, confidence = _resolve_rag_queries(state, qa, ks)
        search_num = (qa.limit if qa else None) or MAX_TOP_K_SIZE
        search_num = min(int(search_num), MAX_TOP_K_SIZE)

        retriever = CustomRAGRetriever(
            top_k=search_num,
            model_name="gemma_triton_0",
            intent_payload=state.intent_payload,
        )
        # NOTE: search_num(top_k)은 intent_payload.normalized_intent.planner_limit으로
        # rag_pipeline에서 반영되므로, top_k > preset.max_ctx_items여도 hydrate 상한은 유지된다.

        rag_tool = Tool(
            name="RAG_Search",
            description="NTIS/IRIS 데이터베이스 검색",
            func=retriever.retrieve
        )

        retrieve_result = await asyncio.to_thread(rag_tool.func, search_query)
        docs = retrieve_result.get("documents", []) if isinstance(retrieve_result, dict) else []
        fallback_context = retrieve_result.get("fallback_context") if isinstance(retrieve_result, dict) else None

        doc_previews = []
        for i, doc in enumerate(docs, 1):
            doc_previews.append(
                json.dumps(doc, ensure_ascii=False, indent=2)
            )

        fallback_context_available = bool(isinstance(fallback_context, str) and fallback_context.strip())
        _log_event("RAG.RESULT", request_id=state.request_id, conversation_id=state.conversation_id, stage="rag_search", docs_found=len(docs), query_len=len(str(search_query or "")), fallback_context_available=int(fallback_context_available))

        return {
            "context": docs,
            "fallback_context": fallback_context,
        }

    except StrategyViolation:
        raise
    except Exception as e:
        logger.error(f"❌ RAG Error: {e}")
        return {"context": []}


# --- Node 7: Refine Answer - Gemma ---
@measure_latency("generate_answer_gemma")
async def node_generate_answer_gemma(state: AgentState) -> Dict[str, Any]:
    """RAG 결과로 Fast Answer 보강 - Gemma"""
    return await _generate_answer(state, "gemma_triton_0", "answer_gemma")

# --- Node 7-2: Refine Answer - solar ---
@measure_latency("generate_answer_solar")
async def node_generate_answer_solar(state: AgentState) -> Dict[str, Any]:
    """RAG 결과로 Fast Answer 보강 - solar"""
    return await _generate_answer(state, "solar_vllm_0", "answer_solar")


_LLM_CACHE: Dict[str, Any] = {}


def _build_llm(model_name: str):
    cached = _LLM_CACHE.get(model_name)
    if cached is not None:
        return cached

    if model_name == "solar_vllm_0":
        llm = OpenAICompatChatModel(
            model_name=SOLAR_VLLM_CONFIG.model_name,
            base_url=SOLAR_VLLM_CONFIG.base_url,
            api_key=SOLAR_VLLM_CONFIG.api_key,
            timeout=SOLAR_VLLM_CONFIG.timeout,
        )
    else:
        llm = TritonChatModel(model_name=model_name)

    _LLM_CACHE[model_name] = llm
    return llm

_PROMPT_CACHE: dict[tuple[str, float], str] = {}


async def load_prompt_file(path: Path) -> str:
    stat = path.stat()
    cache_key = (str(path), stat.st_mtime)
    cached = _PROMPT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    async with aiofiles.open(path, encoding="utf-8") as f:
        content = await f.read()
    _PROMPT_CACHE.clear()
    _PROMPT_CACHE[cache_key] = content
    return content


async def load_system_prompt(path: Path) -> str:
    return await load_prompt_file(path)


async def _generate_answer(state: AgentState, model_name: str, final_field: str) -> Dict[str, Any]:
    llm = _build_llm(model_name=model_name)

    ks = getattr(state, "knowledge_sufficiency", None)
    qa = getattr(state, "question_analysis", None)

    docs_for_ctx = getattr(state, "context", None) or getattr(state, "prev_context", None) or []
    is_detail = False

    if (qa and qa.mode == "JOIN") or (qa and qa.action == "detail"):
        is_detail = True

    is_solar = model_name == "solar_vllm_0"
    context_text = (
        refine_documents_rule_based(
            docs_for_ctx,
            is_detail,
            org_filters=(qa.filters if qa else None),
            ids_map=(qa.ids_map if qa else None),
            relax_limits=False if is_solar else True,
            max_doc_sentences=SOLAR_MAX_DOC_SENTENCES if is_solar else None,
            max_doc_tokens=SOLAR_MAX_DOC_TOKENS if is_solar else None,
        )
        if docs_for_ctx
        else "없음"
    )
    rendered_context_used = bool(docs_for_ctx) and context_text != "없음"
    rendered_context_key = f"rendered_context_used_{final_field.replace('answer_', '')}"
    if is_solar and SOLAR_MAX_CONTEXT_CHARS > 0:
        context_text = context_text[:SOLAR_MAX_CONTEXT_CHARS]

    context_sentences = len(_split_sentences(context_text)) if context_text and context_text != "없음" else 0
    context_tokens_est = len(context_text.split()) if context_text and context_text != "없음" else 0
    SYSTEM_PROMPT_PATH = Path("prompts/ntis_chatbot.md")
    system_prompt = await load_system_prompt(SYSTEM_PROMPT_PATH)

    messages_state = getattr(state, "messages", None) or []
    last_message = messages_state[-1] if messages_state else HumanMessage(content="")
    human_prompt = (
        f"[제공된 정보]\n{context_text}\n\n"
        f"[원본 질문]\n{getattr(last_message, 'content', '')}"
    )
    log_section("제공 정보", context_text)

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
    max_tokens_hint = _select_max_tokens_hint(qa)
    final_answer, stream_metrics = await run_llm_streaming(
        llm,
        messages,
        max_tokens_hint=max_tokens_hint,
        request_id=getattr(state, "request_id", None),
        ttft_deadline_ms=SOLAR_TTFT_DEADLINE_MS if model_name == "solar_vllm_0" else None,
        gen_deadline_ms=SOLAR_GEN_DEADLINE_MS if model_name == "solar_vllm_0" else None,
        max_chars=SOLAR_STREAM_MAX_CHARS if model_name == "solar_vllm_0" else None,
    )

    ttft_any_ms = stream_metrics.get("ttft_any_ms")
    ttft_content_ms = stream_metrics.get("ttft_content_ms")
    reasoning_chars = int(stream_metrics.get("reasoning_chars") or 0)
    content_chars = int(stream_metrics.get("content_chars") or 0)

    logger.info(
        "[stream_metrics] request_id=%s model=%s ttft_any_ms=%s ttft_content_ms=%s reasoning_chars=%s content_chars=%s",
        getattr(state, "request_id", None),
        model_name,
        ttft_any_ms,
        ttft_content_ms,
        reasoning_chars,
        content_chars,
    )

    if model_name == "solar_vllm_0":
        content_delay_ms: Optional[float] = None
        if ttft_any_ms is not None and ttft_content_ms is not None:
            content_delay_ms = round(ttft_content_ms - ttft_any_ms, 1)

        if ttft_any_ms is None:
            logger.warning(
                "[solar_stream_guard] request_id=%s category=stream_not_started_or_stalled ttft_any_ms=%s ttft_content_ms=%s ttft_deadline_exceeded=%s deadline_exceeded=%s",
                getattr(state, "request_id", None),
                ttft_any_ms,
                ttft_content_ms,
                bool(stream_metrics.get("ttft_deadline_exceeded")),
                bool(stream_metrics.get("deadline_exceeded")),
            )
        elif ttft_content_ms is None or (content_delay_ms is not None and content_delay_ms >= 500):
            logger.warning(
                "[solar_stream_guard] request_id=%s category=content_delayed ttft_any_ms=%s ttft_content_ms=%s content_delay_ms=%s reasoning_chars=%s content_chars=%s",
                getattr(state, "request_id", None),
                ttft_any_ms,
                ttft_content_ms,
                content_delay_ms,
                reasoning_chars,
                content_chars,
            )
        elif stream_metrics.get("gen_deadline_exceeded"):
            logger.warning(
                "[solar_stream_guard] request_id=%s category=gen_deadline_exceeded gen_deadline_ms=%s truncated_chars=%s emitted_chars=%s",
                getattr(state, "request_id", None),
                SOLAR_GEN_DEADLINE_MS,
                len(final_answer),
                stream_metrics.get("emitted_chars"),
            )
            if stream_metrics.get("short_output_guard_triggered"):
                logger.warning(
                    "[solar_stream_guard] request_id=%s short_output_guard_triggered min_chars=%s emitted_chars=%s",
                    getattr(state, "request_id", None),
                    stream_metrics.get("short_output_guard_min_chars"),
                    stream_metrics.get("emitted_chars"),
                )
        elif stream_metrics.get("char_limited"):
            logger.warning(
                "[solar_stream_guard] request_id=%s category=char_limited max_chars=%s truncated_chars=%s",
                getattr(state, "request_id", None),
                SOLAR_STREAM_MAX_CHARS,
                len(final_answer),
            )

    _log_event("LLM.GENERATE", request_id=getattr(state, "request_id", None), conversation_id=getattr(state, "conversation_id", None), stage="generate_answer", model=model_name, ks_level=(getattr(ks, "requires_new_knowledge", None) if ks else "unknown"), ctx_chars=len(context_text), ctx_sentences=context_sentences, ctx_tokens_est=context_tokens_est, emitted_chars=len(final_answer or ""))
    return {
        final_field: final_answer,
        f"{final_field}_meta": stream_metrics,
        rendered_context_key: rendered_context_used,
        # legacy compatibility
        "stream_meta": {final_field: stream_metrics},
    }


# --- Node 8: Direct Answer (Rule-based) ---
async def node_direct_answer(state: AgentState) -> Dict[str, Any]:
    """규칙 기반 즉시 답변"""
    response_text = state.rule_decision.direct_response
    return {
        "answer_gemma": response_text,
        "answer_solar": response_text,
        "messages": [AIMessage(content=response_text)]
    }

# --- Node 9: Merge Answers ---
@measure_latency("merge_answers")
async def node_merge_answers(state: AgentState) -> Dict[str, Any]:

    has_docs_context = bool(getattr(state, "context", None) or getattr(state, "prev_context", None))
    has_fallback_context = bool(getattr(state, "fallback_context", None))
    rendered_context_used = bool(getattr(state, "rendered_context_used_gemma", False)) or bool(getattr(state, "rendered_context_used_solar", False))
    fallback_context_used = (not has_docs_context) and has_fallback_context
    degraded = bool(getattr(state, "degraded", False))

    ks = getattr(state, "knowledge_sufficiency", None)
    strategy = getattr(ks, "requires_new_knowledge", None) if ks else "unknown"
    answer_gemma = (getattr(state, "answer_gemma", "") or "").strip()
    answer_solar_raw = getattr(state, "answer_solar", None) or ""
    answer_solar = answer_solar_raw.strip()
    solar_meta = getattr(state, "answer_solar_meta", None) or {}

    solar_fail_reasons: List[str] = []
    solar_warning_reasons: List[str] = []
    # 1) deadline_exceeded 세분화 (stream_content_emitted_chunks 중심)
    deadline_exceeded = bool(
        solar_meta.get("deadline_exceeded")
        or solar_meta.get("ttft_deadline_exceeded")
        or solar_meta.get("gen_deadline_exceeded")
    )
    ttft_any_ms = solar_meta.get("ttft_any_ms")
    ttft_content_ms = solar_meta.get("ttft_content_ms")
    content_chars = int(solar_meta.get("content_chars") or 0)
    stream_content_emitted_chunks = int(solar_meta.get("stream_content_emitted_chunks") or 0)
    if deadline_exceeded:
        if stream_content_emitted_chunks == 0:
            solar_fail_reasons.append("deadline_without_stream_content")
        if ttft_any_ms is None:
            solar_fail_reasons.append("deadline_stream_not_started_or_stalled")
        elif ttft_content_ms is None and content_chars == 0:
            solar_fail_reasons.append("deadline_content_not_started")
        elif ttft_content_ms is None:
            solar_warning_reasons.append("deadline_content_delayed")
        else:
            solar_warning_reasons.append("deadline_with_partial_or_delayed_content")

    # 2) char_limited
    if bool(solar_meta.get("char_limited")):
        solar_fail_reasons.append("char_limited")

    # 3) 안내 문구 포함 여부
    guidance_markers = [DUAL_MODEL_FALLBACK_MESSAGE]
    for marker in guidance_markers:
        marker_text = (marker or "").strip()
        if marker_text and marker_text in answer_solar:
            solar_fail_reasons.append("contains_fallback_notice")
            break

    # 4) 최소 길이 정책 미달
    if len(answer_solar) < SOLAR_MIN_ANSWER_CHARS:
        solar_fail_reasons.append(f"too_short<{SOLAR_MIN_ANSWER_CHARS}")

    solar_failed = len(solar_fail_reasons) > 0
    selected_model = "gemma" if solar_failed else "solar"
    selected_answer = answer_gemma if solar_failed else answer_solar

    if not selected_answer:
        selected_model = "gemma" if answer_gemma else "solar"
        selected_answer = answer_gemma or answer_solar or DUAL_MODEL_FALLBACK_MESSAGE

    degraded = bool(getattr(state, "degraded", False)) or (selected_answer == DUAL_MODEL_FALLBACK_MESSAGE)

    merge_debug = {
        "policy": DUAL_MODEL_MERGE_POLICY,
        "selected_model": selected_model,
        "solar_failed": solar_failed,
        "solar_fail_reasons": solar_fail_reasons,
        "solar_warning_reasons": solar_warning_reasons,
        "solar_meta": solar_meta,
        "solar_answer_chars": len(answer_solar),
        "gemma_answer_chars": len(answer_gemma),
        "min_chars_threshold": SOLAR_MIN_ANSWER_CHARS,
    }

    _log_event("LLM.RESULT", request_id=getattr(state, "request_id", None), conversation_id=getattr(state, "conversation_id", None), stage="merge_answers", selected_model=selected_model, solar_failed=int(solar_failed), solar_fail_reasons=solar_fail_reasons, gemma_answer_chars=len(answer_gemma), solar_answer_chars=len(answer_solar))

    logger.info(
        "[merge_selection] request_id=%s selected_model=%s solar_fail_reasons=%s",
        getattr(state, "request_id", None),
        selected_model,
        solar_fail_reasons,
    )

    return {
        "messages": [AIMessage(content=selected_answer)],
        "answer_gemma": answer_gemma,
        "answer_solar": answer_solar,
        "answer_solar_raw": answer_solar_raw,
        "merge_debug": merge_debug,
        "context" : getattr(state, "context", None) or [],
        "fallback_context": getattr(state, "fallback_context", None),
        "rendered_context_used": rendered_context_used,
        "fallback_context_used": fallback_context_used,
        "degraded": degraded,
    }

# --- Node 10: Save History ---
@measure_latency("save_history")
async def node_save_history(state: AgentState) -> Dict[str, Any]:
    """Redis에 대화 저장"""

    cid = getattr(state, "conversation_id", "")

    # state.chat_history는 loaded_history + [current_human] 형태라 AI만 추가 저장한다.
    ai_turn = (getattr(state, "messages", None) or [])[-1:]  # [AI]
    full_history = (getattr(state, "chat_history", None) or []) + ai_turn
    trimmed_history = full_history[-MAX_HISTORY_TURNS:]

    serialized_hist = _serialize_history(trimmed_history)

    if kv_store:
        await kv_store.set(
            f"conversation:{cid}:history",
            json.dumps(serialized_hist, ensure_ascii=False),
            ex=REDIS_TTL,
        )

        context = getattr(state, "context", None)
        if context:
            await kv_store.set(
                f"conversation:{cid}:last_context",
                json.dumps(context, ensure_ascii=False),
                ex=REDIS_TTL,
            )

        fallback_context = getattr(state, "fallback_context", None)
        if fallback_context:
            await kv_store.set(
                f"conversation:{cid}:last_fallback_context",
                fallback_context,
                ex=REDIS_TTL,
            )
    else:
        logger.debug("[memory] kv_store unavailable: skip history/context save (cid=%s)", cid)

    total_ms = _compute_total_ms_from_start(getattr(state, "request_started_at", None))
    summary_fields = _state_log_summary_fields(state, total_ms=total_ms)
    _log_event("REQ.SUMMARY", **summary_fields)
    _log_event("REQ.END", conversation_id=getattr(state, "conversation_id", None), request_id=getattr(state, "request_id", None), stage="request_end", total_ms=total_ms, selected_model=(getattr(state, "merge_debug", None) or {}).get("selected_model"))

    return {}


def node_join_analysis(state: AgentState):
    """분석 노드 완료 대기"""
    # 여기서 generate_answer 로 가면 context는 prev_context를 사용한것.
    return { "context" : state.prev_context }


def node_join_answers(state: AgentState):
    """Refined Answer 노드들 완료 대기"""
    return {}

async def load_conversation_memory(conversation_id: str) -> tuple[List[BaseMessage], List[Dict[str, Any]], Optional[str]]:
    loaded_history: List[BaseMessage] = []
    ctx_list: List[Dict[str, Any]] = []
    fallback_context: Optional[str] = None

    raw_hist = await kv_store.get(f"conversation:{conversation_id}:history") if kv_store else None
    hist_list = _safe_json_loads(raw_hist)
    loaded_history = _deserialize_history(hist_list)

    raw_ctx = await kv_store.get(f"conversation:{conversation_id}:last_context") if kv_store else None
    ctx_payload = _safe_json_loads(raw_ctx)
    if isinstance(ctx_payload, list):
        ctx_list = ctx_payload

    raw_fallback_ctx = await kv_store.get(f"conversation:{conversation_id}:last_fallback_context") if kv_store else None
    if isinstance(raw_fallback_ctx, str) and raw_fallback_ctx.strip():
        fallback_context = raw_fallback_ctx.strip()

    return loaded_history, ctx_list, fallback_context

async def build_intent_payload(
        question: str,
        conversation_id: str,
        chat_history: List[BaseMessage],
        prev_context: List[Dict[str, Any]],
        request_id: Optional[str] = None,
) -> tuple[IntentPayloadV2, Optional[QuestionAnalysis]]:
    precheck = _cheap_precheck(question)

    explicit_only_hint = {
        "wants_rank": _has_superlative_cue(question),
        "people_terms": extract_people_terms(question, []),
        "org_terms": normalize_org_terms(extract_org_terms(question, [])),
        "org_role": extract_org_role(question),
        "lead_org_terms": [],
        "participant_org_terms": [],
        "people_affiliation_org_terms": [],
        "years": extract_years(question),
        "perf_types": extract_perf_types(question, []),
        "title_terms": extract_title_terms(question, []),
    }
    if explicit_only_hint["org_role"] in ("lead", "performer", "performing"):
        explicit_only_hint["lead_org_terms"] = list(explicit_only_hint["org_terms"])
    elif explicit_only_hint["org_role"] == "participant":
        explicit_only_hint["participant_org_terms"] = list(explicit_only_hint["org_terms"])
    elif explicit_only_hint["org_role"] == "affiliation":
        explicit_only_hint["people_affiliation_org_terms"] = list(explicit_only_hint["org_terms"])
    kws: List[str] = []
    raw_intent = classify_query_intent(question, kws, hint=explicit_only_hint)
    normalized_intent_base = normalize_intent(
        raw_intent,
        query=question,
        keywords=kws,
        allow_strategy_fallback=False,
        hint_people_terms=list(explicit_only_hint.get("people_terms", [])),
        hint_org_terms=list(explicit_only_hint.get("org_terms", [])),
        hint_org_role=explicit_only_hint.get("org_role"),
        hint_lead_org_terms=list(explicit_only_hint.get("lead_org_terms", [])),
        hint_participant_org_terms=list(explicit_only_hint.get("participant_org_terms", [])),
        hint_people_affiliation_org_terms=list(explicit_only_hint.get("people_affiliation_org_terms", [])),
        hint_years=list(explicit_only_hint.get("years", [])),
        hint_perf_types=list(explicit_only_hint.get("perf_types", [])),
        hint_title_terms=list(explicit_only_hint.get("title_terms", [])),
    )

    question_analysis = None
    planner_failed = 0
    if not precheck:
        question_analysis = await _run_question_analysis(
            question=question,
            conversation_id=conversation_id,
            chat_history=chat_history,
            prev_context=prev_context,
            request_id=request_id,
            normalized_intent_base=normalized_intent_base,
        )
        planner_failed = int(float(getattr(question_analysis, "confidence", 0.0) or 0.0) <= 0.0)

    normalized_intent, planner_applied = apply_planner_v2(
        normalized_intent_base,
        question_analysis,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    _log_event(
        "PLANNER.PIPELINE",
        request_id=request_id,
        conversation_id=conversation_id,
        step="intent_build",
        status="success",
        planner_applied=int(planner_applied),
        planner_failed=int(planner_failed),
        planner_stagewise_enabled=int(PLANNER_STAGEWISE_ENABLED),
        planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
        planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
        schema_fields=["normalized_intent"],
    )
    return IntentPayloadV2(normalized_intent=normalized_intent), question_analysis

def merge_planner_hints(intent: Any, qa: Optional[QuestionAnalysis]) -> Any:
    """비전략 필드(year/perf_types/org_terms/title/keywords 등)만 planner 힌트로 병합한다."""
    # 왜: mode/relation/join_key_mode 같은 전략 축은 응답 경로 자체를 바꾸므로,
    #     여기서는 검색 조건/필터 같은 비전략 값만 보강해 "의도 해석의 미세 보정" 역할에 한정한다.
    #     이렇게 분리해야 이후 apply_planner_strategy에서 전략 일관성 검증을 단일 지점에서 수행할 수 있다.
    if qa is None:
        return intent

    confidence = float(getattr(qa, "confidence", 0.0) or 0.0)
    if confidence < 0.2:
        return intent

    filters = dict(getattr(qa, "filters", {}) or {})
    lead_org_terms = normalize_org_terms(filters.get("lead_org_name") or filters.get("performing_org_name"))
    participant_org_terms = normalize_org_terms(filters.get("participant_org_name"))
    people_affiliation_org_terms = normalize_org_terms(filters.get("people_affiliation_org_name"))
    org_terms = normalize_org_terms([
        *lead_org_terms,
        *participant_org_terms,
        *people_affiliation_org_terms,
        *(filters.get("org_name") or [] if isinstance(filters.get("org_name"), list) else [filters.get("org_name")] if filters.get("org_name") else []),
    ])

    planner_year_from = str(filters.get("year_from") or "").strip() or None
    planner_year_to = str(filters.get("year_to") or "").strip() or None
    planner_years = _normalize_hint_terms(filters.get("years"))
    if not planner_year_from and planner_years:
        planner_year_from = planner_years[0]
    if not planner_year_to and planner_years:
        planner_year_to = planner_years[-1]

    planner_perf_types = _normalize_hint_terms(filters.get("perf_types") or filters.get("performance_types"))
    planner_title_terms = _normalize_hint_terms(filters.get("title_terms") or filters.get("title") or filters.get("name"))
    planner_keywords = _normalize_hint_terms(filters.get("keywords"))
    planner_people_terms = _collect_researcher_name_terms(filters)
    planner_org_role = str(filters.get("org_role") or getattr(intent, "org_role", "") or "").strip().lower() or None

    if planner_org_role == "affiliation" and (people_affiliation_org_terms or org_terms) and not planner_people_terms:
        planner_people_terms = []

    return replace(
        intent,
        planner_limit=int(getattr(qa, "limit", 20) or 20),
        retrieval_query=getattr(qa, "retrieval_query", None),
        planner_confidence=confidence,
        org_role=planner_org_role,
        org_terms=org_terms or list(getattr(intent, "org_terms", []) or []),
        people_terms=planner_people_terms if planner_people_terms else list(getattr(intent, "people_terms", []) or []),
        lead_org_terms=lead_org_terms or list(getattr(intent, "lead_org_terms", []) or []),
        participant_org_terms=participant_org_terms or list(getattr(intent, "participant_org_terms", []) or []),
        people_affiliation_org_terms=people_affiliation_org_terms or list(getattr(intent, "people_affiliation_org_terms", []) or []),
        year_from=planner_year_from or getattr(intent, "year_from", None),
        year_to=planner_year_to or getattr(intent, "year_to", None),
        years=planner_years or list(getattr(intent, "years", []) or []),
        perf_types=planner_perf_types or list(getattr(intent, "perf_types", []) or []),
        keywords=planner_keywords or list(getattr(intent, "keywords", []) or []),
        title=planner_title_terms or list(getattr(intent, "title", []) or []),
    )


def apply_planner_strategy(
    intent: Any,
    qa: Optional[QuestionAnalysis],
    *,
    request_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> tuple[Any, bool]:
    """전략 필드(mode/relation/join_key_mode/target_cols/base_route/action)를 단일 지점에서만 반영한다."""
    # 왜: 전략 필드는 워크플로 라우팅/조회 방식(RAG vs direct, JOIN 여부)을 직접 결정하므로
    #     여러 단계에서 중복 수정되면 원인 추적이 어려워진다. 이 함수에 책임을 모아 일관성을 강제한다.
    if qa is None:
        return intent, False

    action_mode_map = {
        "topic": "search",
        "list": "lookup",
        "detail": "lookup",
        "stats": "lookup",
        "download": "lookup",
        "id_exact": "lookup",
        "id_fuzzy": "lookup",
        "join": "join",
    }

    tracked_fields = ("mode", "base_route", "action", "relation", "join_key_mode", "target_cols", "ids_map")
    strategy_fields = ("mode", "base_route", "action", "relation", "join_key_mode", "target_cols")
    filter_fields = ("ids_map",)
    before_snapshot = {k: getattr(intent, k, None) for k in tracked_fields}

    confidence = float(getattr(qa, "confidence", 0.0) or 0.0)
    if confidence < 0.2:
        return intent, False

    strict_strategy_consistency = str(os.getenv("RAG_STRICT_STRATEGY_CONSISTENCY", "1")).strip().lower() in ("1", "true", "yes", "y")
    planner_source = str(getattr(qa, "planner_source", "") or "").strip().lower() or None
    planner_action = str(getattr(qa, "action", "") or "").strip().lower()
    planner_mode = str(getattr(qa, "mode", getattr(intent, "mode", "")) or getattr(intent, "mode", "")).strip().lower() or None
    expected_mode = action_mode_map.get(planner_action)
    relation_raw = getattr(qa, "relation", None)
    has_join_relation = False
    if isinstance(relation_raw, (tuple, list)):
        has_join_relation = len(relation_raw) >= 2 and bool(str(relation_raw[0]).strip()) and bool(str(relation_raw[1]).strip())
    else:
        has_join_relation = bool(str(relation_raw or "").strip())

    if expected_mode and planner_mode and planner_mode != expected_mode:
        if not (planner_mode == "join" and has_join_relation):
            # 왜: action이 암시하는 모드와 planner 모드가 어긋나면 잘못된 파이프라인(예: JOIN인데 SEARCH 경로)으로
            #     진입할 수 있다. strict는 즉시 실패로 계약 위반을 드러내고,
            #     compat는 서비스 연속성을 위해 expected_mode로 교정 후 진행한다.
            mismatch_reason = (
                f"planner action/mode mismatch(action={planner_action}, mode={planner_mode}, expected_mode={expected_mode})"
            )
            mismatch_fields = {
                "request_id": request_id,
                "conversation_id": conversation_id,
                "planner_action": planner_action,
                "original_mode": planner_mode,
                "corrected_mode": expected_mode,
                "error_code": "PLANNER_ACTION_MODE_MISMATCH",
                "reason": mismatch_reason,
            }
            if strict_strategy_consistency:
                if planner_source == "stagewise":
                    _log_event(
                        "RAG.STRATEGY.ACTION_MODE_MISMATCH_STAGEWISE",
                        **mismatch_fields,
                        handling="non_fatal_keep_assembled_strategy",
                    )
                else:
                    _log_event(
                        "RAG.STRATEGY.ACTION_MODE_MISMATCH",
                        **mismatch_fields,
                    )
                    raise StrategyViolation(
                        error_code="PLANNER_ACTION_MODE_MISMATCH",
                        reason=mismatch_reason,
                    )
            if planner_source == "stagewise":
                _log_event(
                    "RAG.STRATEGY.ACTION_MODE_CORRECTED",
                    **mismatch_fields,
                    handling="kept_assembled_strategy",
                )
            else:
                _log_event(
                    "RAG.STRATEGY.ACTION_MODE_CORRECTED",
                    **mismatch_fields,
                )
                planner_mode = expected_mode

    relation_map = {
        "project_perf": ("project", "perf"),
        "perf_project": ("perf", "project"),
    }
    relation = relation_map.get(getattr(qa, "relation", None), getattr(intent, "relation", None))

    def _merge_ids_map(base_ids: Any, planner_ids: Any) -> dict[str, list[str]]:
        merged: dict[str, list[str]] = {}

        def _ingest(source: Any, *, overwrite: bool = False) -> None:
            if not isinstance(source, dict):
                return
            for key, raw_values in source.items():
                values = _normalize_hint_terms(raw_values)
                if not values:
                    continue
                if overwrite or key not in merged:
                    merged[key] = list(values)
                else:
                    merged[key] = _normalize_hint_terms([*merged[key], *values])

        _ingest(base_ids)
        _ingest(planner_ids, overwrite=True)
        return merged

    planner_target_cols = _normalize_hint_terms(getattr(qa, "target_cols", None))
    planner_wants_rank = bool(getattr(qa, "wants_rank", False))
    if not planner_wants_rank:
        planner_wants_rank = str(getattr(qa, "action", "") or "").strip().lower() in {"rank", "stats"}
    planner_head = str(getattr(qa, "head", getattr(intent, "base_route", "project")) or getattr(intent, "base_route", "project")).strip().lower()
    planner_wants_rank = planner_wants_rank and planner_head in {"people", "org"}

    # 왜: JOIN 모드는 relation(무엇과 무엇을 결합하는지), join_key_mode(어떤 키로 결합하는지),
    #     mode 자체가 동시에 맞아야 의미가 완성된다. 셋 중 하나라도 비면 잘못된 결합/빈 결과를 만들 수 있어
    #     아래 검증으로 조기 차단한다.
    if planner_mode == "join":
        join_relation = relation if isinstance(relation, (list, tuple)) else None
        has_relation = bool(join_relation and len(join_relation) >= 2 and all(str(v or "").strip() for v in join_relation[:2]))
        join_key_mode = str(getattr(qa, "join_key_mode", "") or "").strip().lower()
        if not has_relation or not join_key_mode:
            reason = (
                "planner join strategy requires non-empty relation and join_key_mode"
                f"(mode={planner_mode}, relation={relation}, join_key_mode={join_key_mode or None})"
            )
            if strict_strategy_consistency:
                _log_event(
                    "RAG.STRATEGY.JOIN_FIELDS_MISSING",
                    request_id=request_id,
                    conversation_id=conversation_id,
                    error_code="PLANNER_JOIN_FIELDS_MISSING",
                    reason=reason,
                    policy_mode="strict",
                )
                raise StrategyViolation(error_code="PLANNER_JOIN_FIELDS_MISSING", reason=reason)
            _log_event(
                "RAG.STRATEGY.JOIN_FALLBACK",
                request_id=request_id,
                conversation_id=conversation_id,
                error_code="PLANNER_JOIN_FIELDS_MISSING",
                reason=reason,
                policy_mode="compat",
            )
            planner_mode = str(getattr(intent, "mode", "search") or "search").strip().lower()
            relation = getattr(intent, "relation", None)

    patched = replace(
        intent,
        base_route=planner_head,
        action=("stats" if planner_wants_rank else str(getattr(qa, "action", getattr(intent, "action", "topic")) or getattr(intent, "action", "topic")).strip().lower()),
        mode=planner_mode,
        relation=relation,
        join_key_mode=getattr(qa, "join_key_mode", None),
        target_cols=planner_target_cols or list(getattr(intent, "target_cols", []) or []),
        ids_map=_merge_ids_map(getattr(intent, "ids_map", {}) or {}, getattr(qa, "ids_map", {}) or {}),
        wants_rank=planner_wants_rank or bool(getattr(intent, "wants_rank", False)),
    )

    after_snapshot = {k: getattr(patched, k, None) for k in tracked_fields}
    changed_strategy_fields = build_changed_fields(
        before_snapshot,
        after_snapshot,
        strategy_fields,
        changed_by=CHANGED_BY_PLANNER_MERGE,
    )
    changed_filter_fields = build_changed_fields(
        before_snapshot,
        after_snapshot,
        filter_fields,
        changed_by=CHANGED_BY_PLANNER_MERGE,
    )
    diff = {**changed_strategy_fields, **changed_filter_fields}
    _log_event(
        "PLANNER.PIPELINE",
        request_id=request_id,
        conversation_id=conversation_id,
        step="intent_merge",
        status="success",
        applied=int(bool(diff)),
        confidence=round(confidence, 3),
        strategy_mutation_stage="planner_merge",
        changed_by=CHANGED_BY_PLANNER_MERGE,
        changed_strategy_fields=changed_strategy_fields,
        changed_filter_fields=changed_filter_fields,
    )

    return patched, True


def apply_planner_v2(
    intent: Any,
    qa: Optional[QuestionAnalysis],
    *,
    request_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> tuple[Any, bool]:
    """planner 적용 엔트리포인트. 비전략 병합 후 전략 필드를 단일 지점에서 적용한다."""
    # 왜: 1단계(merge_planner_hints)에서 필터/보조 신호를 흡수하고,
    #     2단계(apply_planner_strategy)에서만 전략 축을 확정해야 strict/compat 정책을 일관되게 적용할 수 있다.
    hinted_intent = merge_planner_hints(intent, qa)
    planner_source = str(getattr(qa, "planner_source", "") or "").strip().lower() if qa else ""
    if qa is not None and planner_source == "stagewise":
        relation_map = {
            "project_perf": ("project", "perf"),
            "perf_project": ("perf", "project"),
        }

        def _merge_ids_map(base_ids: Any, planner_ids: Any) -> dict[str, list[str]]:
            merged: dict[str, list[str]] = {}

            def _ingest(source: Any, *, overwrite: bool = False) -> None:
                if not isinstance(source, dict):
                    return
                for key, raw_values in source.items():
                    values = _normalize_hint_terms(raw_values)
                    if not values:
                        continue
                    if overwrite or key not in merged:
                        merged[key] = list(values)
                    else:
                        merged[key] = _normalize_hint_terms([*merged[key], *values])

            _ingest(base_ids)
            _ingest(planner_ids, overwrite=True)
            return merged

        stagewise_patched = replace(
            hinted_intent,
            base_route=str(getattr(qa, "head", getattr(hinted_intent, "base_route", "project")) or getattr(hinted_intent, "base_route", "project")).strip().lower(),
            action=str(getattr(qa, "action", getattr(hinted_intent, "action", "topic")) or getattr(hinted_intent, "action", "topic")).strip().lower(),
            mode=str(getattr(qa, "mode", getattr(hinted_intent, "mode", "search")) or getattr(hinted_intent, "mode", "search")).strip().lower(),
            relation=relation_map.get(getattr(qa, "relation", None), getattr(hinted_intent, "relation", None)),
            join_key_mode=getattr(qa, "join_key_mode", None),
            target_cols=_normalize_hint_terms(getattr(qa, "target_cols", None)) or list(getattr(hinted_intent, "target_cols", []) or []),
            ids_map=_merge_ids_map(getattr(hinted_intent, "ids_map", {}) or {}, getattr(qa, "ids_map", {}) or {}),
        )
        return stagewise_patched, bool(stagewise_patched != intent)

    return apply_planner_strategy(
        hinted_intent,
        qa,
        request_id=request_id,
        conversation_id=conversation_id,
    )

def _collect_researcher_name_terms(filters: Dict[str, Any]) -> list[str]:
    """planner filters에서 연구자 이름 힌트를 폭넓게 수집한다."""
    if not isinstance(filters, dict):
        return []

    researcher_keys = (
        "participant_researcher_name",
        "participant_researcher_names",
        "participant_researcher",
        "participant_researchers",
        "researcher_name",
        "researcher_names",
        "researcher",
        "people_name",
    )

    terms: list[str] = []
    for key in researcher_keys:
        terms.extend(_normalize_hint_terms(filters.get(key)))
    return _normalize_hint_terms(terms)

def _normalize_hint_terms(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if not s or s.lower() in ("none", "null") or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out

# --- Graph Construction ---
def build_advanced_workflow():
    workflow = StateGraph(AgentState)

    # Add Nodes
    # 왜 이 순서인가:
    # 1) load_memory: 과거 대화/컨텍스트를 먼저 불러야 이후 규칙/분석이 맥락 기반으로 동작한다.
    # 2) rule_precheck: 비용 큰 분석/RAG 이전에 차단·직답 규칙을 먼저 적용해 지연과 비용을 줄인다.
    # 3) analyze_question: 룰을 통과한 질의만 의도/전략 분석을 수행한다.
    # 4) judge_knowledge_sufficiency: 기존 컨텍스트로 답할 수 있는지 평가해 불필요한 검색을 피한다.
    # 5) join_analysis 이후 RAG/직답(기존지식 활용) 경로를 최종 분기한다.
    workflow.add_node("load_memory", node_load_memory)
    workflow.add_node("rule_precheck", node_rule_precheck)
    workflow.add_node("analyze_question", node_analyze_question)
    workflow.add_node("judge_knowledge_sufficiency", node_knowledge_sufficiency)
    workflow.add_node("join_analysis", node_join_analysis)

    # 두 모델 각각의 Fast Answer 노드

    workflow.add_node("rag_search", node_rag_search)

    # 두 모델 각각의 Refined Answer 노드
    workflow.add_node("generate_answer_gemma", node_generate_answer_gemma)
    workflow.add_node("generate_answer_solar", node_generate_answer_solar)
    workflow.add_node("join_answers", node_join_answers)

    workflow.add_node("direct_answer", node_direct_answer)
    workflow.add_node("merge_answers", node_merge_answers)
    workflow.add_node("save_history", node_save_history)

    # Entry Point
    workflow.set_entry_point("load_memory")

    # Flow
    workflow.add_edge("load_memory", "rule_precheck")

    def route_after_rule(state: AgentState):
        # 왜: rule_precheck에서 정책상 즉시 응답 가능한 질의는
        #      analyze_question/RAG를 생략해 빠르고 예측 가능한 응답을 준다.
        if state.rule_decision and state.rule_decision.action == "direct_answer":
            return "direct_answer"
        return "analyze_question"

    workflow.add_conditional_edges(
        "rule_precheck",
        route_after_rule, {
            "direct_answer": "direct_answer",
            "analyze_question": "analyze_question"
        }
    )

    workflow.add_edge("analyze_question", "judge_knowledge_sufficiency")
    workflow.add_edge("judge_knowledge_sufficiency", "join_analysis")

    def route_after_join_analysis(state: AgentState):
        ks = state.knowledge_sufficiency

        # 왜: requires_new_knowledge가 low이고 prev_context가 있으면
        #      이미 확보된 근거로 두 모델 생성만 수행해 응답 시간을 단축한다.
        if ks.requires_new_knowledge == "low" and state.prev_context:
            return ["generate_answer_solar", "generate_answer_gemma"]

        # 왜: 지식이 부족하거나 이전 컨텍스트가 없으면 hallucination 위험이 높아
        #      반드시 rag_search를 거쳐 최신/근거 문서를 확보한다.
        return "rag_search"

    workflow.add_conditional_edges(
        "join_analysis",
        route_after_join_analysis,
        {
            "generate_answer_solar": "generate_answer_solar",
            "generate_answer_gemma": "generate_answer_gemma",
            "rag_search": "rag_search"
        }
    )


    # RAG 검색 완료 후 두 모델로 Refine
    workflow.add_edge("rag_search", "generate_answer_gemma")
    workflow.add_edge("rag_search", "generate_answer_solar")

    # Refined Answer 완료 후 join
    workflow.add_edge("generate_answer_gemma", "join_answers")
    workflow.add_edge("generate_answer_solar", "join_answers")

    # Refined answers join도 merge로
    workflow.add_edge("join_answers", "merge_answers")


    # 왜 direct_answer와 merge_answers를 모두 save_history로 모으는가:
    # - 대화 히스토리 저장 경로를 단일화해 후속 turn 메모리 일관성을 유지한다.
    # - 응답 생성 경로가 달라도 관측(로그/지연/메타) 수집 지점을 하나로 고정해 운영 추적을 단순화한다.
    workflow.add_edge("direct_answer", "save_history")
    workflow.add_edge("merge_answers", "save_history")

    # End
    workflow.add_edge("save_history", END)

    return workflow


def _normalize_researcher_token(value: Optional[str]) -> str:
    if not value:
        return ""
    normalized = re.sub(r"[^0-9a-zA-Z가-힣]", "", str(value)).lower()
    return normalized


def _extract_researcher_fields(researcher: Any) -> tuple[str, str, str]:
    if isinstance(researcher, dict):
        name = researcher.get("name")
        affiliation = researcher.get("affiliation")
        researcher_id = researcher.get("researcher_id")
    else:
        name = getattr(researcher, "name", None)
        affiliation = getattr(researcher, "affiliation", None)
        researcher_id = getattr(researcher, "researcher_id", None)
    return (
        str(name).strip() if name else "",
        str(affiliation).strip() if affiliation else "",
        str(researcher_id).strip() if researcher_id else "",
    )


def _extract_org_fields(org: Any) -> tuple[str, str, str]:
    if isinstance(org, dict):
        name = org.get("org_nm") or org.get("org_name") or org.get("name")
        org_id = (
                org.get("org_id")
                or org.get("org_cd")
                or org.get("org_code")
                or org.get("org_no")
        )
        role = org.get("org_slct_nm") or org.get("role") or org.get("org_role")
    else:
        name = getattr(org, "org_nm", None) or getattr(org, "name", None)
        org_id = (
                getattr(org, "org_id", None)
                or getattr(org, "org_cd", None)
                or getattr(org, "org_code", None)
                or getattr(org, "org_no", None)
        )
        role = getattr(org, "org_slct_nm", None) or getattr(org, "role", None)
    return (
        str(name).strip() if name else "",
        str(org_id).strip() if org_id else "",
        str(role).strip() if role else "",
    )


def _collect_org_hints(
        organizations: Optional[List[Any]],
        filters: Optional[Dict[str, Any]],
        ids_map: Optional[Dict[str, Any]],
) -> tuple[list[str], list[str], Optional[str]]:
    org_terms: list[str] = []
    org_ids: list[str] = []
    role_hint: Optional[str] = None

    for org in organizations or []:
        if isinstance(org, dict):
            name = org.get("name") or org.get("org_nm") or org.get("org_name")
            org_id = org.get("org_id") or org.get("org_cd") or org.get("org_code")
            if name:
                org_terms.append(str(name).strip())
            if org_id:
                org_ids.append(str(org_id).strip())
        else:
            org_terms.append(str(org).strip())

    if isinstance(filters, dict):
        org_terms += _normalize_hint_terms(filters.get("org_name") or filters.get("org"))
        org_ids += _normalize_hint_terms(filters.get("org_id"))
        role_hint = filters.get("org_role") or role_hint

    if isinstance(ids_map, dict):
        org_ids += _normalize_hint_terms(ids_map.get("org_id"))

    org_terms = _normalize_hint_terms(org_terms)
    org_ids = _normalize_hint_terms(org_ids)
    return org_terms, org_ids, str(role_hint).strip() if role_hint else None


def _match_prtcp_orgs(
        prtcp_orgs: List[Dict[str, Any]],
        org_terms: list[str],
        org_ids: list[str],
        role_hint: Optional[str],
        *,
        max_matches: int = 5,
) -> List[Dict[str, Any]]:
    if not prtcp_orgs or (not org_terms and not org_ids):
        return []

    org_terms_exact = {term.strip() for term in org_terms if term.strip()}
    org_terms_norm = {_normalize_researcher_token(term) for term in org_terms_exact}
    org_ids_set = {str(org_id).strip() for org_id in org_ids if str(org_id).strip()}
    role_hint_norm = _normalize_researcher_token(role_hint) if role_hint else ""

    candidates: list[dict[str, Any]] = []
    for org in prtcp_orgs:
        org_nm, org_id, role = _extract_org_fields(org)
        org_nm_norm = _normalize_researcher_token(org_nm)
        role_norm = _normalize_researcher_token(role)

        score = 0.0
        match_type = None
        if org_id and org_id in org_ids_set:
            score = 3.0
            match_type = "id_exact"
        if org_nm and org_nm in org_terms_exact and score < 2.5:
            score = 2.5
            match_type = "name_exact"
        if org_nm_norm and org_nm_norm in org_terms_norm and score < 2.0:
            score = 2.0
            match_type = "name_norm"

        if score <= 0:
            continue

        role_confirmed = match_type in {"id_exact", "name_exact"}
        if role_hint_norm and role_norm and role_norm == role_hint_norm:
            role_confirmed = True
            score += 0.1

        candidates.append(
            {
                "org_nm": org_nm or "기관미상",
                "org_id": org_id,
                "org_slct_nm": role,
                "match_type": match_type,
                "role_confirmed": role_confirmed,
                "score": score,
            }
        )

    if not candidates:
        return []

    candidates.sort(key=lambda item: item.get("score", 0), reverse=True)
    seen_keys: set[tuple[str, str]] = set()
    matches: list[dict[str, Any]] = []
    for item in candidates:
        key = (
            str(item.get("org_id") or ""),
            _normalize_researcher_token(item.get("org_nm")),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        matches.append(item)
        if len(matches) >= max_matches:
            break
    return matches


def _format_org_entry(org_nm: str, role: str, role_confirmed: bool) -> str:
    if role:
        if role_confirmed:
            return f"{org_nm}({role})"
        return f"{org_nm}(참여 당시 기관: {role})"
    return org_nm


def _match_prtcp_members(
        prtcp_members: List[Dict[str, Any]],
        researchers: Optional[List[Any]],
        *,
        max_matches: int = 5,
) -> List[Dict[str, Any]]:
    if not prtcp_members or not researchers:
        return []

    matches: List[Dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    for researcher in researchers:
        name, affiliation, researcher_id = _extract_researcher_fields(researcher)
        name_norm = _normalize_researcher_token(name)
        affiliation_norm = _normalize_researcher_token(affiliation)
        best_member = None
        best_score = 0.0

        for member in prtcp_members:
            hm_id = str(member.get("hm_id") or "").strip()
            hm_nm = str(member.get("hm_nm") or "").strip()
            org_nm = str(member.get("blng_org_nm") or "").strip()

            score = 0.0
            match_type = ""
            if researcher_id and hm_id and researcher_id == hm_id:
                score = 3.0
                match_type = "id_exact"
            else:
                hm_nm_norm = _normalize_researcher_token(hm_nm)
                if name_norm and hm_nm_norm and name_norm == hm_nm_norm:
                    score = 2.0
                    match_type = "name_exact"
                    if affiliation_norm:
                        org_norm = _normalize_researcher_token(org_nm)
                        if org_norm and org_norm == affiliation_norm:
                            score = 2.5
                            match_type = "name_affiliation_exact"
                elif name_norm and hm_nm_norm and (name_norm in hm_nm_norm or hm_nm_norm in name_norm):
                    score = 1.2
                    match_type = "name_partial"
                    if affiliation_norm:
                        org_norm = _normalize_researcher_token(org_nm)
                        if org_norm and (affiliation_norm in org_norm or org_norm in affiliation_norm):
                            score = 1.6
                            match_type = "name_affiliation_partial"

            if score > best_score:
                best_score = score
                best_member = dict(member)
                if match_type:
                    best_member["match_type"] = match_type
                best_member["match_score"] = score

        if best_member and best_score > 0:
            dedup_key = (
                str(best_member.get("hm_id") or _normalize_researcher_token(best_member.get("hm_nm"))),
                _normalize_researcher_token(best_member.get("blng_org_nm")),
            )
            if dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                matches.append(best_member)
                if len(matches) >= max_matches:
                    break

    return matches


def _format_researcher_line(
        matched_members: List[Dict[str, Any]],
        fallback_lines: List[str],
        is_detail: bool = False,
        max_matches: int = 5,
) -> str:
    if matched_members:
        names = []
        for member in matched_members[:max_matches]:
            name = str(member.get("hm_nm") or "이름미상").strip()
            org = str(member.get("blng_org_nm") or "소속미상").strip()
            match_type = str(member.get("match_type") or "").strip()
            if match_type:
                names.append(f"{name}({org}, {match_type})")
            else:
                names.append(f"{name}({org})")
        return f"- 연구자(매칭): {', '.join(names)}"

    fallback_names = []
    if is_detail:
        for line in fallback_lines:
            cleaned = line.lstrip("- ").strip()
            if cleaned:
                fallback_names.append(cleaned)
        if fallback_names:
            return f"- 연구자: {', '.join(fallback_names)}"
    else:
        for line in fallback_lines[:max_matches]:
            cleaned = line.lstrip("- ").strip()
            if cleaned:
                fallback_names.append(cleaned)
        if fallback_names:
            return f"- 연구자: {', '.join(fallback_names)} 등 생략"

    return "- 연구자: 정보 없음"


def _format_org_line(
        matched_orgs: List[Dict[str, Any]],
        prtcp_orgs: List[Dict[str, Any]],
        *,
        max_matches: int = 5,
) -> str:
    if matched_orgs:
        entries = []
        for org in matched_orgs[:max_matches]:
            org_nm = str(org.get("org_nm") or "기관미상").strip()
            role = str(org.get("org_slct_nm") or "").strip()
            role_confirmed = bool(org.get("role_confirmed"))
            entries.append(_format_org_entry(org_nm, role, role_confirmed))
        return f"- 참여기관(매칭): {', '.join(entries)}"

    if prtcp_orgs:
        entries = []
        for org in prtcp_orgs[:max_matches]:
            org_nm, _, role = _extract_org_fields(org)
            org_nm = org_nm or "기관미상"
            entries.append(_format_org_entry(org_nm, role, False))
        return f"- 참여기관: {', '.join(entries)}"

    return "- 참여기관: 정보 없음"


def _safe_map_doc(doc: Document, *, context: str) -> Optional[Dict[str, Any]]:
    try:
        return RagMapper.map(doc)
    except MappingError as exc:
        source_idx = doc.get("source_index")
        logger.warning(
            "문서 매핑 실패(%s): source_index=%s, error=%s",
            context,
            source_idx,
            exc,
        )
        return None


def _collect_priority_field_lines(mapped_doc: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    seen: set[str] = set()

    for field in PRIORITY_CONTEXT_FIELDS:
        value = mapped_doc.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() == "none":
            continue

        normalized = f"{field}:{text}".lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        lines.append(f"- {field}: {text}")

    return lines


def refine_documents_rule_based(
        docs: List[Document],
        is_detail: bool = False,
        *,
        researchers: Optional[List[Any]] = None,
        organizations: Optional[List[Any]] = None,
        org_filters: Optional[Dict[str, Any]] = None,
        ids_map: Optional[Dict[str, Any]] = None,
        max_matches: int = 5,
        relax_limits: bool = False,
        max_doc_sentences: Optional[int] = None,
        max_doc_tokens: Optional[int] = None,
) -> str:
    context_chunks: List[str] = []
    field_max_sentences = None if relax_limits else MAX_FIELD_SENTENCES
    field_max_tokens = None if relax_limits else MAX_FIELD_TOKENS
    if relax_limits:
        doc_max_sentences = None
        doc_max_tokens = None
    else:
        doc_max_sentences = max_doc_sentences if max_doc_sentences is not None else MAX_DOC_SENTENCES
        doc_max_tokens = max_doc_tokens if max_doc_tokens is not None else MAX_DOC_TOKENS

    for doc in docs:
        if str(doc.get("source_type", "")).strip().lower() == "aggregation":
            rank_item = doc.get("rank_item") or {}
            metric = str(doc.get("metric") or "project_participation_count")
            metric_value = rank_item.get(metric, rank_item.get("score", 0))
            perf_count = rank_item.get("performance_count", 0)
            candidate_docs = int(doc.get("candidate_docs") or 0)
            window_years = doc.get("window_years") or {}
            year_from = (window_years.get("from") if isinstance(window_years, dict) else None) or "-"
            year_to = (window_years.get("to") if isinstance(window_years, dict) else None) or "-"
            person_name = rank_item.get("hm_nm") or rank_item.get("hm_id") or rank_item.get("person_key") or "unknown"
            context_chunks.append(
                f"## 출처 {doc.get('source_index')}. {person_name}\n"
                f"- {metric}: {metric_value}\n"
                f"- performance_count: {perf_count}\n"
                f"- candidate_docs: {candidate_docs}\n"
                f"- window_years: {year_from} ~ {year_to}\n"
            )
            continue

        mapped_doc = _safe_map_doc(doc, context="refine_documents_rule_based")
        if not mapped_doc:
            continue
        _apply_title_preference(mapped_doc)

        source_idx = doc.get("source_index")

        title = mapped_doc.get("title", "제목 없음")
        # log_section("refine_documents_rule_based - 페이로드 평탄화 메소드 내부",
        #             f"mapped_doc: {mapped_doc}")
        #
        # log_section("refine_documents_rule_based - 페이로드 평탄화 메소드 내부",
        #             f"title: {title}")

        meta_basic = mapped_doc.get("meta_basic", {})
        meta_basic_text = format_metadata(
            meta_basic,
            max_sentences=field_max_sentences,
            max_tokens=field_max_tokens,
        )
        # log_section(
        #     "refine_documents_rule_based - meta_basic 필드 출력 확인",
        #     f"meta_basic keys: {list(meta_basic.keys())}\n"
        #     f"formatted:\n{meta_basic_text}",
        # )

        meta_detail_text = ""
        if is_detail:
            meta_detail_text = format_metadata(
                mapped_doc.get("meta_detail", {}),
                max_sentences=field_max_sentences,
                max_tokens=field_max_tokens,
            )

        refined_parts = [text for text in [meta_basic_text, meta_detail_text] if text]
        refined_text = "\n".join(refined_parts)


        prtcp_members = mapped_doc.get("prtcp_mp", []) if isinstance(mapped_doc, dict) else []
        matched_members = _match_prtcp_members(prtcp_members, researchers, max_matches=max_matches)
        fallback_lines = RagMapper.get_researcher_info(mapped_doc)
        researcher_line = _format_researcher_line(
            matched_members,
            fallback_lines,
            is_detail,
            max_matches=max_matches
        )

        prtcp_orgs = mapped_doc.get("prtcp_org", []) if isinstance(mapped_doc, dict) else []
        org_terms, org_ids, role_hint = _collect_org_hints(organizations, org_filters, ids_map)
        matched_orgs = _match_prtcp_orgs(
            prtcp_orgs,
            org_terms,
            org_ids,
            role_hint,
            max_matches=max_matches,
        )
        org_line = _format_org_line(
            matched_orgs,
            prtcp_orgs,
            max_matches=max_matches,
        )
        priority_lines = _collect_priority_field_lines(mapped_doc)
        # log_section("refine_documents_rule_based - 페이로드 평탄화 메소드 내부",
        #             f"matched_members: {matched_members}\n"
        #             f"fallback_lines: {fallback_lines}\n"
        #             f"matched_orgs: {matched_orgs}"
        #             f"researcher_line: {researcher_line}")

        limited_body = _limit_text_by_sentences_and_tokens(
            refined_text,
            max_sentences=doc_max_sentences,
            max_tokens=doc_max_tokens,
        )
        body_sentences = _split_sentences(limited_body)
        body_token_counts = [len(sentence.split()) for sentence in body_sentences]
        body_token_count = sum(body_token_counts)
        extra_lines = [line for line in priority_lines + [researcher_line, org_line] if line]
        extra_text = "\n".join(extra_lines)
        extra_sentences = _split_sentences(extra_text)
        extra_token_count = len(extra_text.split())

        if not relax_limits and doc_max_sentences is not None and doc_max_tokens is not None:
            while body_sentences and (
                    len(body_sentences) + len(extra_sentences) > doc_max_sentences
                    or body_token_count + extra_token_count > doc_max_tokens
            ):
                body_token_count -= body_token_counts.pop()
                body_sentences.pop()

        limited_body = "\n".join(body_sentences).strip()
        limited_text = "\n".join(
            [part for part in [limited_body] + extra_lines if part]
        ).strip()

        context_chunks.append(
            f"## 출처 {source_idx}. {title}\n"
            f"{limited_text}\n"
        )

    return "\n\n".join(context_chunks)


def _split_sentences(text: str) -> List[str]:
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    sentences: List[str] = []
    for line in raw_lines:
        parts = re.split(r"(?<=[.!?])\s+", line)
        cleaned = [part.strip() for part in parts if part.strip()]
        if cleaned:
            sentences.extend(cleaned)
        else:
            sentences.append(line)
    return sentences


def _limit_text_by_sentences_and_tokens(
        text: str,
        *,
        max_sentences: Optional[int],
        max_tokens: Optional[int],
) -> str:
    if not text:
        return ""
    if max_sentences is None or max_tokens is None:
        return text.strip()
    sentences = _split_sentences(text)
    limited: List[str] = []
    token_count = 0
    for sentence in sentences:
        next_tokens = len(sentence.split())
        if limited and (len(limited) >= max_sentences or token_count + next_tokens > max_tokens):
            break
        limited.append(sentence)
        token_count += next_tokens
        if len(limited) >= max_sentences:
            break
    return "\n".join(limited)



def format_metadata(
        metadata: Dict[str, Any],
        *,
        max_sentences: Optional[int] = None,
        max_tokens: Optional[int] = None,
) -> str:
    """metadata dict → bullet list 텍스트 변환"""
    lines = []

    for key, value in metadata.items():
        if value is None:
            continue

        if isinstance(value, list):
            value = ", ".join(map(str, value))
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)

        if max_sentences is not None and max_tokens is not None:
            value = _limit_text_by_sentences_and_tokens(
                str(value),
                max_sentences=max_sentences,
                max_tokens=max_tokens,
            )

        lines.append(f"- {key}: {value}")

    return "\n".join(lines) if lines else ""


class LLMJSONExtractionError(ValueError):
    """LLM 응답에서 JSON 객체/배열 추출 실패 시 발생."""


def _summarize_text(text: str, head: int = 160, tail: int = 160) -> str:
    compact = " ".join(text.split())
    if len(compact) <= head + tail + 20:
        return compact
    return f"{compact[:head]} ... {compact[-tail:]}"


def _iter_json_candidates(text: str) -> List[str]:
    candidates: List[tuple[int, str]] = []

    for match in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE):
        block = match.group(1).strip()
        if block:
            candidates.append((match.start(), block))

    def find_matching_end(start_idx: int, open_ch: str, close_ch: str) -> Optional[int]:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start_idx, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                    continue
                if ch == "\\":
                    escaped = True
                elif ch == "\"":
                    in_string = False
                continue

            if ch == "\"":
                in_string = True
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return idx
        return None

    for match in re.finditer(r"[\{\[]", text):
        start_idx = match.start()
        open_ch = text[start_idx]
        close_ch = "}" if open_ch == "{" else "]"
        end_idx = find_matching_end(start_idx, open_ch, close_ch)
        if end_idx is None:
            continue
        candidates.append((start_idx, text[start_idx:end_idx + 1].strip()))

    seen: set[str] = set()
    ordered: List[str] = []
    for _, candidate in sorted(candidates, key=lambda item: item[0]):
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


def sanitize_llm_json(msg) -> str:
    text = msg.content if hasattr(msg, "content") else str(msg)
    last_error: Optional[Exception] = None

    for candidate in _iter_json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue

        if isinstance(parsed, (dict, list)):
            return candidate

    summary = _summarize_text(text)
    error_detail = f"{type(last_error).__name__}: {last_error}" if last_error else "no_candidates"
    logger.warning(
        "JSON extraction failed: length=%s, preview=%s, error=%s",
        len(text),
        summary,
        error_detail,
    )
    raise LLMJSONExtractionError("유효한 JSON 객체/배열을 추출하지 못했습니다.")


def _has_payload_index(client: Any, collection_name: str, field_name: str) -> bool:
    try:
        collection_info = client.get_collection(collection_name=collection_name)
    except Exception:
        return False

    payload_schema = getattr(collection_info, "payload_schema", None)
    if not isinstance(payload_schema, dict):
        return False
    return field_name in payload_schema

# --- Lifespan & App Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global kv_store

    rag_resources = build_rag_objects()
    _log_event("CODE.FINGERPRINT", stage="startup")
    _log_event("APP.CONFIG", stage="startup", planner_stagewise_enabled=int(PLANNER_STAGEWISE_ENABLED), planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION, planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION)

    ensure_payload_index_on_boot = os.getenv("RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }

    if ensure_payload_index_on_boot:
        keyword_index_targets = {
            "ntis_project": ["pjt_id", "pjt_no"],
            "ntis_perf": ["pjt_id", "pjt_no"],
        }
        text_index_targets = {
            "ntis_project": ["org_nm", "prtcp_org[].org_nm", "prtcp_mp[].blng_org_nm"],
            "ntis_perf": ["org_nm", "prtcp_org[].org_nm", "prtcp_mp[].blng_org_nm"],
        }

        client = rag_resources.qdrant_client
        for collection_name, field_names in keyword_index_targets.items():
            for field_name in field_names:
                try:
                    collection_info = client.get_collection(collection_name=collection_name)
                    payload_schema = getattr(collection_info, "payload_schema", None) or {}
                    field_schema = payload_schema.get(field_name)
                    schema_type = getattr(field_schema, "data_type", None)
                    schema_type_name = str(schema_type).upper() if schema_type is not None else ""

                    if "KEYWORD" in schema_type_name:
                        logger.info("[startup][payload-index][keyword] %s.%s: skip (already exists)", collection_name, field_name)
                        continue

                    ensure_keyword_index(client, collection_name, field_name)
                    logger.info("[startup][payload-index][keyword] %s.%s: ensure called", collection_name, field_name)
                except Exception as e:
                    logger.warning("[startup][payload-index][keyword] %s.%s: warning (%s)", collection_name, field_name, e)

        for collection_name, field_names in text_index_targets.items():
            for field_name in field_names:
                try:
                    ensure_text_index(client, collection_name, field_name)
                    logger.info("[startup][payload-index][text] %s.%s: ensure called", collection_name, field_name)
                except Exception as e:
                    logger.warning("[startup][payload-index][text] %s.%s: warning (%s)", collection_name, field_name, e)
    else:
        logger.info("[startup][payload-index] skipped by RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT=%s", os.getenv("RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT"))

    sparse_warmup_on_boot = os.getenv("RAG_FASTEMBED_WARMUP_ON_BOOT", "true").strip().lower() in {"1", "true", "yes", "on"}
    if sparse_warmup_on_boot:
        warmup_sparse_encoder()
    else:
        logger.info("[startup][fastembed] skipped by RAG_FASTEMBED_WARMUP_ON_BOOT=%s", os.getenv("RAG_FASTEMBED_WARMUP_ON_BOOT"))

    try:
        r = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        await r.ping()

        # Redis를 KVStore처럼 쓰기 위한 얇은 어댑터
        class RedisKVStore(KVStore):
            def __init__(self, client):
                self.client = client

            async def get(self, key: str) -> Optional[str]:
                return await self.client.get(key)

            async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
                await self.client.set(key, value, ex=ex)

            async def ping(self) -> bool:
                try:
                    await self.client.ping()
                    return True
                except Exception:
                    return False

            async def close(self) -> None:
                await self.client.close()

        kv_store = RedisKVStore(r)
        logger.info("✅ Redis connected: %s", REDIS_URL)
    except Exception as e:
        kv_store = None
        logger.error("❌ Redis connection failed: %s", e, exc_info=True)

    metrics_timeout = httpx.Timeout(METRICS_PROMETHEUS_TIMEOUT)
    app.state.metrics_http = httpx.AsyncClient(timeout=metrics_timeout)

    try:
        workflow = build_advanced_workflow().compile()
        app.state.graph = workflow
        logger.info("✅ Advanced Dual-Model Pipeline compiled successfully")
        yield
    finally:
        await app.state.metrics_http.aclose()
        if kv_store:
            await kv_store.close()

        for llm in _LLM_CACHE.values():
            close_fn = getattr(llm, "aclose", None)
            if close_fn is None:
                continue
            try:
                await close_fn()
            except Exception as e:
                logger.warning("[shutdown] llm close failed: model=%s error=%s", getattr(llm, "model_name", "unknown"), e)
        _LLM_CACHE.clear()

app = FastAPI(lifespan=lifespan)

# --- Endpoints ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not TEMPLATE_INDEX_PATH.exists():
        return HTMLResponse(
            content="<html><body><h3>NTIS RAG Chatbot</h3><p>index template unavailable.</p></body></html>",
            status_code=200,
        )
    return templates.TemplateResponse("index.html", {"request": request})

class QueryRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None

@app.post("/query/stream")
async def query_stream(payload: QueryRequest):
    """스트리밍 응답 엔드포인트 (두 모델 비교)"""

    question = payload.question
    conversation_id = payload.conversation_id or str(uuid.uuid4())
    request_id = f"{conversation_id}-{uuid.uuid4().hex[:8]}"

    graph = app.state.graph

    async def event_generator():
        # 왜 SSE는 `data: ...\n\n` 형식을 강제하는가:
        # 브라우저 EventSource가 이 구분자로 이벤트 경계를 인식하므로,
        # 줄바꿈 두 개를 누락하면 클라이언트가 버퍼링 상태로 멈춘 것처럼 보일 수 있다.
        yield f"data: {json.dumps({'conversationId': conversation_id})}\n\n"
        _log_event("REQ.START", request_id=request_id, conversation_id=conversation_id, stage="request_start", q_len=len(question), q_preview=_mask_query_for_log(question) if _is_debug_logging_enabled() else None)
        request_started_at = time.perf_counter()

        documents_used = []
        done_meta_by_model: Dict[str, Dict[str, Any]] = {}
        question_analysis: Optional[QuestionAnalysis] = None

        try:
            set_log_context(request_id=request_id, conversation_id=conversation_id)
            user_message = HumanMessage(content=question)
            inputs = {
                "conversation_id": conversation_id,
                "request_id": request_id,
                "request_started_at": request_started_at,
                "messages": [user_message],
            }

            async for event in graph.astream_events(inputs, version="v2"):
                kind = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")
                data = event.get("data", {})
                if kind == "on_chat_model_stream" and node == "generate_answer_solar":
                    chunk = data.get("chunk")
                    chunk_text, stream_field = _extract_stream_chunk_text_and_field(chunk)
                    if stream_field == "reasoning":
                        continue
                    if chunk_text:
                        yield f"data: {json.dumps({'model' : 'UPSTAGE', 'content': chunk_text}, ensure_ascii=False)}\n\n"

                elif kind == "on_chat_model_stream" and node == "generate_answer_gemma":
                    chunk = data.get("chunk")
                    chunk_text, stream_field = _extract_stream_chunk_text_and_field(chunk)
                    if stream_field == "reasoning":
                        continue
                    if chunk_text:
                        yield f"data: {json.dumps({'model' : 'GEMMA', 'content': chunk_text}, ensure_ascii=False)}\n\n"

                elif kind == "on_chain_end" and node in {"generate_answer_solar", "generate_answer_gemma"}:
                    output = data.get("output", {})
                    model = "SOLAR" if node == "generate_answer_solar" else "GEMMA"
                    answer_key = "answer_solar" if node == "generate_answer_solar" else "answer_gemma"
                    answer_meta_key = f"{answer_key}_meta"
                    stream_meta = output.get(answer_meta_key) or (output.get("stream_meta") or {}).get(answer_key, {})
                    done_meta_by_model[model] = stream_meta or {}

                elif kind == "on_chain_end" and node == "analyze_question":
                    output = data.get("output", {})
                    question_analysis = output

                elif kind == "on_chain_end" and node == "direct_answer":
                    output = data.get("output", {})
                    if "answer_gemma" in output:
                        answer = output["answer_gemma"]
                        yield f"data: {json.dumps({'model' : 'UPSTAGE', 'content': answer}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'model' : 'GEMMA', 'content': answer}, ensure_ascii=False)}\n\n"

                elif kind == "on_chain_start" and node == "rag_search":
                    yield f"data: {json.dumps({'status': 'retrieve'}, ensure_ascii=False)}\n\n"

                elif kind == "on_chain_end" and node == "merge_answers":
                    docs = data.get("output", {}).get("context", [])
                    documents_used.extend(docs)

            ref_docs = []
            for d in documents_used:
                if not _is_hit_source(d):
                    continue
                ref_docs.append(RagMapper.get_references(d))

            yield f"data: {json.dumps({'reference': ref_docs}, ensure_ascii=False)}\n\n"

            solar_done = done_meta_by_model.get("SOLAR") or {}
            gemma_done = done_meta_by_model.get("GEMMA") or {}
            _log_event(
                "STREAM.DONE",
                request_id=request_id,
                conversation_id=conversation_id,
                stage="stream_done",
                solar_error_code=_derive_stream_error_code(solar_done),
                solar_elapsed_ms=solar_done.get("elapsed_ms"),
                solar_ttft_any_ms=solar_done.get("ttft_any_ms"),
                solar_ttft_content_ms=solar_done.get("ttft_content_ms"),
                solar_content_chars=solar_done.get("content_chars"),
                gemma_error_code=_derive_stream_error_code(gemma_done),
                gemma_elapsed_ms=gemma_done.get("elapsed_ms"),
                gemma_ttft_any_ms=gemma_done.get("ttft_any_ms"),
                gemma_ttft_content_ms=gemma_done.get("ttft_content_ms"),
                gemma_content_chars=gemma_done.get("content_chars"),
            )

            # 왜 종료 이벤트를 명시적으로 보내는가:
            # 스트림 연결은 열려있을 수 있으므로, 클라이언트는 status=done을 받아야
            # 로딩 상태를 종료하고 후처리(참고문헌 렌더링 등)를 안전하게 실행할 수 있다.
            yield f"data: {json.dumps({'status': 'done'})}\n\n"

        except Exception as e:
            logger.error(f"Stream Error: {e}", exc_info=True)
            error_code = getattr(e, "error_code", "INTERNAL_ERROR")
            reason = getattr(e, "reason", str(e))
            total_ms = _compute_total_ms_from_start(request_started_at)
            degraded = isinstance(e, StrategyViolation)
            _log_event(
                "REQ.ERROR",
                request_id=request_id,
                conversation_id=conversation_id,
                stage="stream",
                error_code=error_code,
                reason=reason,
                degraded=int(degraded),
                total_ms=total_ms,
            )
            if degraded:
                # 왜 StrategyViolation은 degraded 사용자 메시지로 변환하는가:
                # 내부 전략 계약 오류를 그대로 노출하면 사용자는 복구 행동을 알기 어렵다.
                # 친화 메시지로 바꿔 재질문/질문 단순화 같은 다음 행동을 안내한다.
                user_message = _friendly_strategy_violation_message(
                    error_code=error_code,
                    reason=reason,
                    question_analysis=question_analysis,
                )
                yield f"data: {json.dumps({'answer': user_message, 'error_code': error_code, 'reason': reason, 'degraded': True}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'status': 'done', 'degraded': True}, ensure_ascii=False)}\n\n"
                return
            yield f"data: {json.dumps({'error': str(e), 'error_code': error_code, 'reason': reason}, ensure_ascii=False)}\n\n"
        finally:
            set_log_context(request_id=None, conversation_id=None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.post("/query/debug")
async def query_debug(payload: QueryRequest):
    """디버깅용 동기 엔드포인트 (전체 State 반환)"""

    question = payload.question
    conversation_id = payload.conversation_id or str(uuid.uuid4())
    request_id = f"{conversation_id}-{uuid.uuid4().hex[:8]}"
    graph = app.state.graph

    request_started_at = time.perf_counter()
    stage = "debug_request_start"
    try:
        set_log_context(request_id=request_id, conversation_id=conversation_id)
        _log_event("REQ.START", request_id=request_id, conversation_id=conversation_id, stage=stage, q_len=len(question))
        user_message = HumanMessage(content=question)

        stage = "planner_memory_build"
        inputs = {
            "conversation_id": conversation_id,
            "request_id": request_id,
            "request_started_at": request_started_at,
            "messages": [user_message],
        }

        final_state = await graph.ainvoke(inputs)

        question_analysis = final_state.get("question_analysis") if isinstance(final_state, dict) else None
        knowledge_sufficiency = final_state.get("knowledge_sufficiency") if isinstance(final_state, dict) else None

        total_ms = _compute_total_ms_from_start(request_started_at)
        _log_event("REQ.END", request_id=request_id, conversation_id=conversation_id, stage="debug_done", total_ms=total_ms)
        messages = final_state.get("messages", []) if isinstance(final_state, dict) else []
        output_message = getattr(messages[-1], "content", "") if messages else ""

        # 왜 디버그 엔드포인트가 상태 스냅샷을 반환하는가:
        # 단일 요청에서 planner 결과/지식충분성/latency를 함께 확인해
        # "어느 노드에서 어떤 분기와 시간이 발생했는지"를 재현 가능한 형태로 점검하기 위함이다.
        return {
            "success": True,
            "conversation_id": conversation_id,
            "answer_gemma": final_state.get("answer_gemma") if isinstance(final_state, dict) else None,
            "answer_solar": final_state.get("answer_solar") if isinstance(final_state, dict) else None,
            "output_message": output_message,
            "question_analysis": question_analysis.model_dump() if question_analysis else None,
            "knowledge_sufficiency": knowledge_sufficiency.model_dump() if knowledge_sufficiency else None,
            "documents_used": len(final_state.get("context", []) if isinstance(final_state, dict) else []),
            "latencies": final_state.get("latencies", {}) if isinstance(final_state, dict) else {},
            "total_time": total_ms,
            "processing_strategy": getattr(knowledge_sufficiency, "requires_new_knowledge", None) if knowledge_sufficiency else "unknown"
        }

    except Exception as e:
        logger.error(f"Debug Error: {e}", exc_info=True)
        degraded = isinstance(e, StrategyViolation)
        _log_event(
            "REQ.ERROR",
            request_id=request_id,
            conversation_id=conversation_id,
            stage=stage,
            error_type=type(e).__name__,
            degraded=int(degraded),
            total_ms=_compute_total_ms_from_start(request_started_at),
        )
        return {
            "success": False,
            "error": str(e),
            "error_code": getattr(e, "error_code", "INTERNAL_ERROR"),
            "reason": getattr(e, "reason", str(e)),
        }
    finally:
        set_log_context(request_id=None, conversation_id=None)

@app.get("/health")
async def health_check():
    # 왜 health는 "서비스 가능 최소 조건"(메모리 연결 + 그래프 준비 상태)을 분리해 노출하는가:
    # 전체 장애와 부분 장애를 구분해 운영자가 즉시 우회/복구 판단을 하도록 돕는다.
    ok = False
    if kv_store:
        ok = await kv_store.ping()
    return {
        "status": "healthy",
        "memory_backend": "redis",
        "memory_backend_effective": type(kv_store).__name__ if kv_store else "none",
        "kv": "connected" if ok else "disconnected",
        "graph": "compiled" if hasattr(app.state, "graph") else "not_ready"
    }

@app.get("/metrics", response_model=MetricSnapshot, response_model_by_alias=True)
async def get_metrics() -> MetricSnapshot:
    """server3 앱에서 Prometheus 메트릭 스냅샷을 제공한다."""
    # 왜 pull 방식 스냅샷 API를 별도로 두는가:
    # 대시보드/알람 시스템이 단발 조회로 현재 상태를 수집하기 쉽고,
    # SSE 구독 없이도 운영 자동화 스크립트에서 재사용 가능하다.
    return await collect_metrics_snapshot(app.state.metrics_http)


@app.get("/metrics/stream")
async def stream_metrics(request: Request) -> StreamingResponse:
    """server3 앱에서 2초 주기 기본 SSE 메트릭 스트림을 제공한다."""

    async def event_generator() -> Any:
        while True:
            if await request.is_disconnected():
                # 왜 즉시 종료하는가:
                # 연결이 끊긴 클라이언트에 계속 push하면 불필요한 수집/직렬화 비용이 누적된다.
                break

            snapshot = await collect_metrics_snapshot(request.app.state.metrics_http)
            payload = snapshot.model_dump_json(by_alias=True)
            # 왜 metrics도 SSE 규약(event/data + \n\n)을 지키는가:
            # 프런트/관측 도구가 동일 파서로 일반 응답 스트림과 메트릭 스트림을 처리할 수 있다.
            yield f"event: metrics\ndata: {payload}\n\n"

            await asyncio.sleep(METRICS_STREAM_INTERVAL_SECONDS)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008, access_log=False)
