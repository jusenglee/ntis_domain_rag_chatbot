import logging
import asyncio
import uuid
import json
import time
import os
import re
from typing import Annotated, Optional, List, Dict, Any, Literal
from contextlib import asynccontextmanager
from pathlib import Path
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
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
from storage import KVStore, MemoryKVStore, FileKVStore
from triton_llm import TritonChatModel
from openai_compat_llm import OpenAICompatChatModel
from rag_pipeline import run_rag_ab_compare
from rag_parts.pipeline_steps import NormalizedIntent, normalize_intent
from rag_parts.planner_contract import StrategyViolation
from rag_parts.query_intent import classify_query as classify_query_intent, _cheap_precheck
from schemas import IntentPayloadV2
from settings import (
    REDIS_URL,
    REDIS_TTL,
    MAX_TOP_K_SIZE,
    MAX_DOC_SENTENCES,
    MAX_DOC_TOKENS, DEFAULT_MODEL_NAME,
)

from rag_mapper.rag_mapper import RagMapper, MappingError

# --- Logging Setup ---
def log_section(title, content):
    header = f"\n\033[96m{'='*10} [{title}] {'='*10}\033[0m"
    footer = f"\033[96m{'='*30}\033[0m\n"
    logger.info(f"{header}\n{content}\n{footer}")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Chatbot_Server")

def setup_file_logging(log_path="logs/server3.log"):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

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

    # 중복 핸들러 방지
    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)

setup_file_logging()

templates = Jinja2Templates(directory="templates")

# --- Configuration ---
kv_store: Optional[KVStore] = None
MAX_HISTORY_TURNS = 10
HISTORY_PREVIEW_LIMIT = 100
SHORT_ANSWER_MAX_TOKENS_HINT = int(os.getenv("SHORT_ANSWER_MAX_TOKENS_HINT", "1024"))
FOLLOW_UP_MAX_TOKENS_HINT = int(os.getenv("FOLLOW_UP_MAX_TOKENS_HINT", "2048"))
MAX_FIELD_SENTENCES = int(os.getenv("MAX_FIELD_SENTENCES", "3"))
MAX_FIELD_TOKENS = int(os.getenv("MAX_FIELD_TOKENS", "120"))
PLANNER_SCHEMA_VERSION = "v2"
PLANNER_V2_RETRY_ATTEMPTS = int(os.getenv("PLANNER_V2_RETRY_ATTEMPTS", "2"))
PLANNER_V2_RETRY_BACKOFF_SEC = float(os.getenv("PLANNER_V2_RETRY_BACKOFF_SEC", "0.35"))
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
        if isinstance(relation, str):
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

        # --- target_cols: list[str] 보장 ---
        tc = d.get("target_cols")
        if isinstance(tc, str):
            d["target_cols"] = [tc]
        elif isinstance(tc, (tuple, set)):
            d["target_cols"] = [str(x) for x in tc]
        elif not isinstance(tc, list):
            d["target_cols"] = []

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
        """
        if self.mode != "JOIN":
            # 파싱 단계에서는 실패시키지 않고 정규화만 한다.
            try:
                object.__setattr__(self, "join_key_mode", None)
            except Exception:
                # Pydantic config가 frozen인 경우 등
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
    return PLANNER_V2_RETRY_BACKOFF_SEC * (2 ** max(0, attempt_no - 1))


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

    # 각 모델별 답변 저장
    answer_gemma: Optional[str] = None
    answer_solar: Optional[str] = None

    # 메타데이터
    conversation_id: str = ""

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

    latencies: Annotated[Dict[str, float], merge_latencies] = Field(default_factory=dict)

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

# --- Node 1: Load Memory ---
@measure_latency("load_memory")
async def node_load_memory(state: AgentState) -> Dict[str, Any]:
    """Redis에서 대화 이력 및 이전 컨텍스트 로드"""
    cid = state.conversation_id
    loaded_history, ctx_list, fallback_context = await load_conversation_memory(cid)
    current_full_history = loaded_history + [state.messages[-1]]

    log_section("LOAD MEMORY",
                f"coq: {cid}{state.messages[-1].content}\nHistory: {len(loaded_history)} turns\nPrev Context: {len(ctx_list)} docs")
    return {
        "question": state.messages[-1].content,
        "chat_history": current_full_history,
        "prev_context": ctx_list,
        "fallback_context": fallback_context
    }

# --- Node 2: Rule-based Precheck ---
@measure_latency("rule_precheck")
async def node_rule_precheck(state: AgentState) -> Dict[str, Any]:
    """규칙 기반 빠른 판단"""
    user_msg = state.messages[-1].content.strip().lower()

    greetings = ["안녕", "hello", "hi", "헬로", "반가워", "ㅎㅇ"]
    if any(g in user_msg for g in greetings) and len(user_msg) < 10:
        return {
            "rule_decision": RuleDecision(
                action="direct_answer",
                direct_response="안녕하세요! 무엇을 도와드릴까요?",
                reason="Simple greeting detected"
            )
        }

    if len(user_msg) < 5:
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
    if state.question_analysis:
        return {"question_analysis": state.question_analysis}

    result = await _run_question_analysis(
        question=state.messages[-1].content,
        conversation_id=state.conversation_id,
        chat_history=state.chat_history,
        prev_context=state.prev_context,
    )
    return {"question_analysis": result}


async def _run_question_analysis(
        *,
        question: str,
        conversation_id: str,
        chat_history: List[BaseMessage],
        prev_context: List[Dict[str, Any]],
        researchers: Optional[List[Any]] = None,
) -> QuestionAnalysis:
    llm = OpenAICompatChatModel(model_name="/model", base_url=os.getenv("SOLAR_VLLM_BASE_URL", "http://vllm_solar:8001/v1"), api_key=os.getenv("SOLAR_VLLM_API_KEY", "EMPTY"))  # Solar(vLLM)
    parser = PydanticOutputParser(pydantic_object=QuestionAnalysis)

    history = chat_history[-6:]
    history_str = "\n".join([f"{type(m).__name__}: {m.content}" for m in history])

    prev_context_str = refine_documents_rule_based(
        prev_context,
        researchers=researchers,
        organizations=None,
        org_filters=None,
        ids_map=None,
    )

    system_prompt = f"""
        당신은 NTIS R&D 데이터 검색전략 플래너(LLM Planner)입니다.
        당신의 임무는 사용자 질의마다 단 하나의 최종 전략(Strategy JSON)을 확정하는 것입니다.
        실행 레이어(retrieval/filters/rerank/controller)는 당신의 전략을 변경/재해석하지 않고 그대로 실행합니다.
        
        ====================
        [도메인/데이터 전제(필수)]
        ====================
        - 데이터는 과제(project)와 성과(perf)로 구성됩니다.
        - 모든 문서에는 참여인력(prtcp_mp)과 참여기관(prtcp_org) 객체가 포함됩니다.
        - 키 정의:
          * PJT_ID: 과제 고유번호(단일 시행 인스턴스)
          * PJT_NO: 동일과제 그룹 ID(연도 다른 시행들을 묶음)
        - 기관 조건은 의미가 3종으로 나뉘며 filters에서 반드시 구분합니다:
          1) 수행기관(메인) = org_nm
          2) 참여기관(공동) = prtcp_org[].org_nm
          3) 참여인력 소속기관(affiliation) = prtcp_mp[].blng_org_nm
        
        ====================
        [불변 계약(매우 중요)]
        ====================
        1) 당신은 mode/head/relation/target_cols/ids_map/filters/limit/retrieval_query를 '단 하나'로 확정합니다.
        2) 실행 레이어는 재결정 금지(허용: filters를 Qdrant filter로 '컴파일'만).
        3) SEARCH는 누락 방지, LOOKUP/JOIN은 정확도/재현성 최우선입니다.
        
        ====================
        [출력 강제 규칙]
        ====================
        1) 반드시 JSON 객체만 출력합니다. (설명/마크다운/코드블럭 금지)
        2) enum 값은 아래 정의된 값만 사용합니다. 철자/대소문자 정확히.
        3) strategy_version은 항상 "{PLANNER_SCHEMA_VERSION}"로 고정합니다.
        4) 아래 키를 반드시 모두 포함합니다:
           strategy_version, mode, head, action, relation, join_key_mode, target_cols, ids_map, filters, limit, retrieval_query, confidence
        5) 값이 없으면 타입에 맞춰 빈 dict/[]/null 을 사용합니다.
        6) 모르는 값은 추측하지 말고 반드시 빈 dict/[]/null 로 둡니다.
        7) 문자열 "None" 금지. 반드시 null 또는 [] 를 사용합니다.
        8) 다중 후보/복수 전략 출력 금지. 오직 1개의 Strategy만 출력.
        
        ====================
        [Mode 정의]
        ====================
        - SEARCH: 탐색형(누락 방지 최우선). server-side must 필터로 후보를 먼저 자르지 않습니다.
        - LOOKUP: 정확형(필터/ID 기반). server-side 하드필터로 정답집합 근처를 강제합니다.
        - JOIN: 2-hop 관계형(project↔perf). Hop1에서 키를 확보하고 Hop2에서 하드필터로 강제합니다.
        
        ====================
        [Mode 결정 규칙(우선순위)]
        ====================
        A) "이 과제의 성과/논문/특허" 또는 "이 성과가 나온 과제" 등 project↔perf relation이 명확하면 => mode="JOIN"
           - relation="project_perf" 또는 relation="perf_project"를 명시합니다.
        B) action이 list/detail/stats/download 성격(목록/상세/통계/다운로드)이거나,
           단순 ID 조회/목록/통계 요청이면 => mode="LOOKUP"
        C) ids_map에 값이 하나라도 있고, A에 해당하지 않으면 => mode="LOOKUP"
        D) 위에 해당하지 않는 토픽/키워드 탐색이면 => mode="SEARCH"

        예시:
        - "1711015550 과제의 논문/특허" => mode="JOIN" (project↔perf relation 명확)
        - "1711015550 과제 상세" => mode="LOOKUP" (단순 ID 상세 조회)
        
        추가 원칙(중요):
        - 사람/기관→과제/성과 관계 질의는, 모든 문서에 prtcp_mp/prtcp_org가 있으므로 기본적으로 JOIN이 아니라 LOOKUP(하드 게이트)로 해결합니다.
        - people/org 식별 Hop1(2-hop)은 기본 비활성입니다. (동명이인/식별자 요구 등 예외에서만 사용)
        
        ====================
        [head 결정 규칙]
        ====================
        - head는 "사용자가 최종적으로 얻고 싶은 결과 엔티티"입니다.
          * 과제 목록/상세/통계/다운로드 => head="project"
          * 성과 목록/상세/통계/다운로드 => head="perf"
          * 시스템 QnA => head="support"
        - 사람/기관이 질의에 포함되어도, 목적이 과제/성과면 head를 people/org로 두지 않습니다.
        - 예외: 연구자/기관 자체 식별/프로필/코드가 목적이면 head="people" 또는 head="org" 가능.
        
        ====================
        [action enum]
        ====================
        action은 아래 중 하나:
        - "topic" | "list" | "detail" | "stats" | "download"
        
        ====================
        [relation enum]
        ====================
        relation은 아래 중 하나 또는 null:
        - "project_perf" | "perf_project" | null
        
        ====================
        [target_cols 규칙]
        ====================
        target_cols는 실행할 컬렉션 리스트입니다.
        - "ntis_project_v1" / "ntis_perf_v1" 중 선택
        - LOOKUP/JOIN은 필요한 컬렉션만 최소로 선택합니다.
          * "신동구 참여과제" => ["ntis_project_v1"]
          * "OO기관 성과" => ["ntis_perf_v1"]
          * project↔perf JOIN => ["ntis_project_v1","ntis_perf_v1"]
        - SEARCH는 기본적으로 두 컬렉션 모두 가능하나, head가 명확하면 1개만 선택 가능합니다.
        
        ====================
        [ids_map 규칙]
        ====================
        ids_map은 dict이며 값은 문자열 배열입니다. 허용 키만 사용:
        - pjt_id, pjt_no
        - doi, issn, eissn, pissn
        - patent_reg_no, patent_app_no
        - paper_id, perf_id, rst_id
        - person_no(참여인력 hm_id), org_id, org_code, biz_no
        
        추출하지 못하면 빈 dict.
        [JOIN 추가 불변 규칙]
        - mode="JOIN"은 ids_map에 pjt_id 또는 pjt_no가 존재할 때만 허용한다.
        - 사람/기관 이름만 있는 경우 JOIN 금지. 반드시 mode="LOOKUP"으로 처리한다.
        
        [ID 필드 규칙]
        - ids_map.person_no는 참여인력 ID(hm_id)일 때만 사용한다.
        - 한글 이름(예: 김재수)은 ids_map에 넣지 말고 filters.participant_researcher_name에만 넣는다.
        
        ====================
        [filters 규칙(계약)]
        ====================
        filters는 dict입니다. 필요한 키만 포함합니다.
        허용 키:
        - year_from, year_to
        - title_terms (배열)
        - keywords (배열)
        - tag_filters (배열: IRD_NAI_PJT_INFO, IRD_NAI_RI_PAPER, IRD_NAI_RI_IPR, IRD_NAI_RI_SW, IRD_NAI_RI_RSCH_RPT, IRD_NAI_RI_FCLT_EQUIP, IRD_NAI_RI_TECH_INFO 등)
        - perf_types (배열)
        
        사람/기관 관계 필터(의미 구분 필수):
        - participant_researcher_name (배열) : prtcp_mp[].hm_nm
        - participant_researcher_id (배열)   : prtcp_mp[].hm_id 또는 person_no
        - lead_org_name (배열)               : org_nm (수행기관)
        - participant_org_name (배열)        : prtcp_org[].org_nm (참여기관)
        - people_affiliation_org_name (배열) : prtcp_mp[].blng_org_nm (사람 소속기관)
        - org_role (문자열, 선택): "lead" | "participant" | null
        
        사람/기관 필터 강도 규칙(중요):
        - ID가 있으면 must 수준(LOOKUP 하드필터)로 가정
        - 이름만 있으면 must가 아니라 should+min_should=1 수준의 하드 게이트로 가정(정규화/변형 포함)
        - 동명이인/동명기관 가능성이 높으면 confidence를 낮춥니다.
        
        ====================
        [SEARCH의 서버필터 원칙]
        ====================
        - SEARCH에서는 server-side must 필터 금지(또는 최소화)
        - 허용: must_not로 명백히 다른 도메인 제외 정도
        - 사람/기관/tag은 rerank 보너스/게이트로 처리
        
        ====================
        [LOOKUP의 서버필터 우선순위]
        ====================
        LOOKUP 하드필터 우선순위:
        1) PJT_ID == x
        2) PJT_NO == x
        3) PJT_NO == x AND stan_yr == y
        4) 성과 식별자(doi/issn/patent_no/perf_id 등) 키 must
        
        ====================
        [JOIN(2-hop) 정책: 그룹 vs 인스턴스]
        ====================
        - JOIN은 project↔perf relation이 명확할 때만 사용합니다.
        - relation이 명확하면 mode="JOIN"을 우선 적용하고, 단순 ID 조회/목록/통계는 mode="LOOKUP"을 사용합니다.
        - mode="JOIN"이면 join_key_mode는 필수이며 "instance" | "group" 중 하나여야 합니다.
        - mode!="JOIN"이면 join_key_mode는 null 이어야 합니다.
        - JOIN에서 ids_map 키는 XOR 규칙을 반드시 지킵니다(동시 존재 금지):
          * join_key_mode="instance" => ids_map.pjt_id만 허용
          * join_key_mode="group" => ids_map.pjt_no만 허용
        
        1) relation="project_perf"
          - join_key_mode="instance": Hop2(perf)에서 pjt_id == PJT_ID must
          - join_key_mode="group": Hop2(perf)에서 pjt_no == PJT_NO must
        
        2) relation="perf_project"
          - join_key_mode="instance": Hop2(project)에서 pjt_id == PJT_ID must
          - join_key_mode="group": Hop2(project)에서 pjt_no == PJT_NO must
        
        ====================
        [retrieval_query 규칙]
        ====================
        retrieval_query는 검색 최적화용 짧은 쿼리입니다.
        - 최대 120자, 핵심 개념 5개 이내
        - 사람/기관명이 있으면 반드시 포함
        - 불필요한 기능어(목록/조회/알려줘/무엇/어떤 등)는 제거
        
        ====================
        [limit 규칙]
        ====================
        - limit는 1~{MAX_TOP_K_SIZE} 범위 정수
        - 사용자가 상위 N개 명시 시 반영(단 MAX 초과 금지)
        - 불명확하면 20
        
        ====================
        [필수 출력 JSON 스키마]
        ====================
        반드시 아래 키를 모두 포함한 JSON 객체만 출력:
        - strategy_version: "{PLANNER_SCHEMA_VERSION}"
        - mode: "SEARCH" | "LOOKUP" | "JOIN"
        - head: "project" | "perf" | "people" | "org" | "support"
        - action: "topic" | "list" | "detail" | "stats" | "download"
        - relation: "project_perf" | "perf_project" | null
        - join_key_mode: "instance" | "group" | null
        - target_cols: string 배열
        - ids_map: dict
        - filters: dict
        - limit: int
        - retrieval_query: string
        - confidence: float (0.0~1.0)
    
        {{format_instructions}}
    """


    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "[대화 이력]\n{history}\n\n[이전 정보]\n{prev_context}\n\n[현재 질문]\n{question}")
    ])

    chain = prompt | llm | sanitize_llm_json | parser
    max_attempts = max(1, PLANNER_V2_RETRY_ATTEMPTS)
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            result: QuestionAnalysis = await chain.ainvoke({
                "format_instructions": parser.get_format_instructions(),
                "history": history_str or "없음",
                "prev_context": prev_context_str or "없음",
                "question": question
            })
            normalized_payload = _normalize_none_string(result.model_dump())
            _validate_question_analysis_required_keys(normalized_payload)
            result = QuestionAnalysis.model_validate(normalized_payload)
            result.limit = min(result.limit, MAX_TOP_K_SIZE)

            logger.info(
                "[PLANNER.V2] event=analysis_succeeded conversation_id=%s attempt=%s retries=%s fallback=%s",
                conversation_id,
                attempt,
                attempt - 1,
                0,
            )
            log_section(
                "QUESTION ANALYSIS",
                f"coq: {conversation_id}{question}\n"
                f"StrategyVersion: {result.strategy_version}\n"
                f"Mode: {result.mode}\n"
                f"Head: {result.head}\n"
                f"Relation: {result.relation}\n"
                f"JoinKeyMode: {result.join_key_mode}\n"
                f"Action: {result.action}\n"
                f"TargetCols: {result.target_cols}\n"
                f"IdsMap: {result.ids_map}\n"
                f"Filters: {result.filters}\n"
                f"Limit: {result.limit}\n"
                f"Query: {result.retrieval_query}\n"
                f"Confidence: {result.confidence:.2f}\n"
                f"planner_failed=0\n"
                f"planner_retry_count={attempt - 1}\n"
                f"planner_fallback=0"
            )
            return result

        except (ValidationError, LLMJSONExtractionError, PlannerV2ParseError, ValueError) as e:
            last_error = e
            should_retry = attempt < max_attempts
            backoff_seconds = _planner_v2_backoff_seconds(attempt) if should_retry else 0.0
            logger.warning(
                "[PLANNER.V2] event=parse_failed conversation_id=%s attempt=%s max_attempts=%s retry=%s backoff_sec=%.3f fallback=%s error_type=%s error=%s",
                conversation_id,
                attempt,
                max_attempts,
                int(should_retry),
                backoff_seconds,
                int(not should_retry),
                type(e).__name__,
                e,
            )
            if should_retry:
                await asyncio.sleep(backoff_seconds)
                continue

    logger.error(
        "[PLANNER.V2] event=parse_final_failed conversation_id=%s retries=%s error_type=%s error=%s",
        conversation_id,
        max_attempts - 1,
        type(last_error).__name__ if last_error else "unknown",
        last_error,
    )
    raise StrategyViolation(
        error_code="PLANNER_PARSE_FINAL_FAILED",
        reason=str(last_error or "planner parse failed"),
    )


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
        log_section("KNOWLEDGE SUFFICIENCY",
                    f"coq: {state.conversation_id}{state.question}\n"
                    f"Requires New: {result.requires_new_knowledge}\n"
                    f"Search Intent: {result.search_intent}\n"
                    f"Query: {result.retrieval_query}\n"
                    f"Confidence: {result.confidence:.2f}")
        return {"knowledge_sufficiency": result}

    if action in search_required_actions:
        result = KnowledgeSufficiency(
            requires_new_knowledge="high",
            search_intent=f"query_intent action={action} 검색이 필요함",
            retrieval_query=retrieval_query,
            confidence=1.0,
        )
        log_section("KNOWLEDGE SUFFICIENCY",
                    f"coq: {state.conversation_id}{state.question}\n"
                    f"Requires New: {result.requires_new_knowledge}\n"
                    f"Search Intent: {result.search_intent}\n"
                    f"Query: {result.retrieval_query}\n"
                    f"Confidence: {result.confidence:.2f}")
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

        log_section("KNOWLEDGE SUFFICIENCY",
                    f"coq: {state.conversation_id}{state.question}\n"
                    f"Requires New: {result.requires_new_knowledge}\n"
                    f"Search Intent: {result.search_intent}\n"
                    f"Query: {result.retrieval_query}\n"
                    f"Confidence: {result.confidence:.2f}")

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

            rag_data = {
                "title": _resolve_title_from_payload(hit_data),
                "source_index" : idx,
                "source_type": "hit",
                "tag" : hit_data.get("tag"),
                "meta_basic" : hit_data.get("meta_basic", {}),
                "meta_detail" : hit_data.get("meta_detail", {}),
                "prtcp_mp" : hit_data.get("prtcp_mp", [])
            }

            if hit_data.get("tag") is not None:
                documents.append(rag_data)

        return {
            "documents": documents,
            "fallback_context": fallback_context.strip() or None,
        }


def _is_hit_source(doc: Dict[str, Any]) -> bool:
    return doc.get("source_type", "hit") == "hit"


def _filter_hit_documents(docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [doc for doc in docs if isinstance(doc, dict) and _is_hit_source(doc)]

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

        log_section("RAG SEARCH",
                    f"coq: {state.conversation_id}{state.question}\n"
                    f"Query: {search_query}\n"
                    f"Found: {len(docs)} docs\n")
        log_section("-------------------RAG SEARCH----------------", f"Found: {len(docs)} docs\n\n")

        return {"context": docs, "fallback_context": fallback_context}

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


import aiofiles



def _build_llm(model_name: str):
    if model_name == "solar_vllm_0":
        return OpenAICompatChatModel(
            model_name=os.getenv("SOLAR_VLLM_MODEL", "/model"),
            base_url=os.getenv("SOLAR_VLLM_BASE_URL", "http://vllm_solar:8001/v1"),
            api_key=os.getenv("SOLAR_VLLM_API_KEY", "EMPTY"),
        )
    return TritonChatModel(model_name=model_name)

async def load_system_prompt(path: Path) -> str:
    async with aiofiles.open(path, encoding="utf-8") as f:
        return await f.read()

async def _generate_answer(state: AgentState, model_name: str, final_field: str) -> Dict[str, Any]:
    llm = _build_llm(model_name)

    ks = state.knowledge_sufficiency
    qa = state.question_analysis




    # ✅ 1) 기본은 "현재 검색 컨텍스트" 사용
    docs_for_ctx = _filter_hit_documents(state.context) or _filter_hit_documents(state.prev_context)
    fallback_context = state.fallback_context if state.context else None
    is_detail = False

    # ✅ 2) JOIN이면 detail 우선
    if qa and qa.mode == "JOIN":
        is_detail = True

    researcher_hints = _build_researcher_hints_from_question_analysis(qa)

    if docs_for_ctx:
        context_text = refine_documents_rule_based(
            docs_for_ctx,
            is_detail,
            researchers=researcher_hints,
            org_filters=(qa.filters if qa else None),
            ids_map=(qa.ids_map if qa else None),
            relax_limits=True,
        )
    elif fallback_context:
        context_text = f"[참고 문맥(근거 아님)]\n{fallback_context}"
    else:
        context_text = "없음"
    # log_section("context_text - 페이로드 평탄화 후 데이터",
    #             f"title: {context_text}")
    SYSTEM_PROMPT_PATH = Path("prompts/ntis_chatbot.md")
    system_prompt = await load_system_prompt(SYSTEM_PROMPT_PATH)

    human_prompt = (
        f"[제공된 정보]\n{context_text}\n\n"
        f"[원본 질문]\n{state.messages[-1].content}"
    )

    log_section(
        f"FINAL PROMPT ({model_name})",
        f"[SYSTEM]\n{system_prompt}\n\n[HUMAN]\n{human_prompt}",
    )

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
    max_tokens_hint = _select_max_tokens_hint(qa)
    response = await llm.ainvoke(messages, max_tokens_hint=max_tokens_hint)
    final_answer = response.content.replace("<eos>", "").strip()

    log_section(f"GENERATE ANSWER ({model_name})",
                f"Level: {ks.requires_new_knowledge if ks else 'unknown'}\n"
                f"ctx_chars={len(context_text)}\n"
                f"{final_answer[:100]}")
    return {final_field: final_answer}


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

    ks = state.knowledge_sufficiency
    strategy = ks.requires_new_knowledge if ks else "unknown"
    gemma_preview = _truncate_text(state.answer_gemma, HISTORY_PREVIEW_LIMIT)
    solar_preview = _truncate_text(state.answer_solar, HISTORY_PREVIEW_LIMIT)

    # messages에는 gemma 답변을 기본으로 추가
    log_section("MERGE ANSWERS",
                f"coq: {state.conversation_id}{state.question}\n"
                f"Strategy: {strategy}\n"
                f"Gemma: {gemma_preview}\n"
                f"solar: {solar_preview}")

    return {
        "messages": [AIMessage(content=state.answer_solar)],
        "answer_gemma": state.answer_gemma,
        "answer_solar": state.answer_solar,
        "context" : state.context,
        "fallback_context": state.fallback_context
    }

# --- Node 10: Save History ---
@measure_latency("save_history")
async def node_save_history(state: AgentState) -> Dict[str, Any]:
    """Redis에 대화 저장"""

    cid = state.conversation_id

    new_turn = state.messages[-2:]  # [Human, AI]
    full_history = state.chat_history + new_turn
    trimmed_history = full_history[-MAX_HISTORY_TURNS:]

    serialized_hist = _serialize_history(trimmed_history)

    if kv_store:
        await kv_store.set(
            f"conversation:{cid}:history",
            json.dumps(serialized_hist, ensure_ascii=False),
            ex=REDIS_TTL,
        )

    if state.context:
        await kv_store.set(
            f"conversation:{cid}:last_context",
            json.dumps(state.context, ensure_ascii=False),
            ex=REDIS_TTL,
        )

    if state.fallback_context:
        await kv_store.set(
            f"conversation:{cid}:last_fallback_context",
            state.fallback_context,
            ex=REDIS_TTL,
        )

    total_time = sum(state.latencies.values())
    latency_report = "\n".join([f"  {k}: {v}s" for k, v in state.latencies.items()])
    log_section("PERFORMANCE REPORT",
                f"coq: {state.conversation_id}{state.question}\nTotal: {total_time:.3f}s\n{latency_report}")

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
) -> tuple[IntentPayloadV2, Optional[QuestionAnalysis]]:
    precheck = _cheap_precheck(question)
    question_analysis = None
    planner_failed = 0
    if not precheck:
        question_analysis = await _run_question_analysis(
            question=question,
            conversation_id=conversation_id,
            chat_history=chat_history,
            prev_context=prev_context,
        )
        planner_failed = int(float(getattr(question_analysis, "confidence", 0.0) or 0.0) <= 0.0)

    kws: List[str] = []
    hint_people_terms: List[str] = []
    hint_org_terms: List[str] = []
    hint_lead_org_terms: List[str] = []
    hint_participant_org_terms: List[str] = []
    hint_people_affiliation_org_terms: List[str] = []
    hint_title_terms: List[str] = []
    hint_org_role = None

    if question_analysis and isinstance(question_analysis.filters, dict):
        filters = dict(question_analysis.filters or {})

        raw_keywords = filters.get("keywords")
        if isinstance(raw_keywords, (list, tuple, set)):
            kws = [str(term).strip() for term in raw_keywords if str(term).strip()]
        elif isinstance(raw_keywords, str) and raw_keywords.strip():
            kws = [raw_keywords.strip()]

        title_hint = filters.get("title_terms") or filters.get("title") or filters.get("name")
        if title_hint:
            title_terms = _normalize_hint_terms(title_hint)
            hint_title_terms = _normalize_hint_terms([*hint_title_terms, *title_terms])
            kws = list(dict.fromkeys([*kws, *title_terms]))

        hint_org_role = filters.get("org_role")

        people_terms_hint = _normalize_hint_terms([
            *(_normalize_hint_terms(filters.get("participant_researcher_name"))),
            *(_normalize_hint_terms(filters.get("researcher_name") or filters.get("people_name"))),
        ])
        if people_terms_hint:
            hint_people_terms = _normalize_hint_terms([*hint_people_terms, *people_terms_hint])

        lead_org_terms_hint = _normalize_hint_terms(
            filters.get("lead_org_name") or filters.get("performing_org_name")
        )
        participant_org_terms_hint = _normalize_hint_terms(filters.get("participant_org_name"))
        people_affiliation_org_terms_hint = _normalize_hint_terms(filters.get("people_affiliation_org_name"))
        generic_org_terms_hint = _normalize_hint_terms(filters.get("org_name") or filters.get("org"))

        if lead_org_terms_hint:
            hint_lead_org_terms = _normalize_hint_terms([*hint_lead_org_terms, *lead_org_terms_hint])
        if participant_org_terms_hint:
            hint_participant_org_terms = _normalize_hint_terms(
                [*hint_participant_org_terms, *participant_org_terms_hint]
            )
        if people_affiliation_org_terms_hint:
            hint_people_affiliation_org_terms = _normalize_hint_terms(
                [*hint_people_affiliation_org_terms, *people_affiliation_org_terms_hint]
            )

        org_terms_hint = _normalize_hint_terms([
            *generic_org_terms_hint,
            *lead_org_terms_hint,
            *participant_org_terms_hint,
            *people_affiliation_org_terms_hint,
        ])
        if org_terms_hint:
            hint_org_terms = _normalize_hint_terms([*hint_org_terms, *org_terms_hint])

    planner_hint = {
        "people_terms": hint_people_terms,
        "org_terms": hint_org_terms,
        "title_terms": hint_title_terms,
        "org_role": hint_org_role,
        "lead_org_terms": hint_lead_org_terms,
        "participant_org_terms": hint_participant_org_terms,
        "people_affiliation_org_terms": hint_people_affiliation_org_terms,
    }

    raw_intent = classify_query_intent(
        question,
        kws,
        hint=planner_hint,
    )

    normalized_intent = normalize_intent(
        raw_intent,
        query=question,
        keywords=kws,
        hint_people_terms=hint_people_terms,
        hint_org_terms=hint_org_terms,
        hint_org_role=hint_org_role,
        hint_lead_org_terms=hint_lead_org_terms,
        hint_participant_org_terms=hint_participant_org_terms,
        hint_people_affiliation_org_terms=hint_people_affiliation_org_terms,
    )
    normalized_intent, planner_applied = apply_planner_v2(normalized_intent, question_analysis)

    # 불변 Strategy 원칙: QA는 planner 입력 힌트로만 사용하고,
    # normalize_intent 이후 실행 레이어에서 intent를 재작성하지 않는다.

    logger.info(
        "[INTENT_PAYLOAD_V2] event=build conversation_id=%s planner_applied=%s planner_failed=%s schema_fields=%s",
        conversation_id,
        int(planner_applied),
        int(planner_failed),
        ["normalized_intent"],
    )

    return IntentPayloadV2(normalized_intent=normalized_intent), question_analysis




def _build_planner_override_request(analysis: QuestionAnalysis, intent: Any) -> Optional[Dict[str, Any]]:
    requested_mode = str(getattr(analysis, "mode", "") or "").strip().lower()
    current_action = str(getattr(intent, "action", "") or "").strip().lower()
    if not requested_mode or not current_action:
        return None

    lookup_actions = {"list", "detail", "stats", "download", "id_exact", "id_fuzzy", "relation"}
    if requested_mode == "lookup" and current_action not in lookup_actions:
        return {
            "requested_mode": requested_mode,
            "current_action": current_action,
        }
    return None


def apply_planner_v2(intent: Any, qa: Optional[QuestionAnalysis]) -> tuple[Any, bool]:
    if qa is None:
        return intent, False
    confidence = float(getattr(qa, "confidence", 0.0) or 0.0)
    if confidence < 0.2:
        return intent, False

    relation_map = {
        "project_perf": ("project", "perf"),
        "perf_project": ("perf", "project"),
    }
    relation = relation_map.get(getattr(qa, "relation", None), getattr(intent, "relation", None))

    patched = replace(
        intent,
        base_route=str(getattr(qa, "head", getattr(intent, "base_route", "project")) or getattr(intent, "base_route", "project")).strip().lower(),
        action=str(getattr(qa, "action", getattr(intent, "action", "topic")) or getattr(intent, "action", "topic")).strip().lower(),
        relation=relation,
        join_key_mode=getattr(qa, "join_key_mode", None),
        ids_map=dict(getattr(qa, "ids_map", {}) or {}),
        planner_limit=int(getattr(qa, "limit", 20) or 20),
        retrieval_query=getattr(qa, "retrieval_query", None),
        planner_confidence=confidence,
    )
    return patched, True

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


def _build_researcher_hints_from_question_analysis(qa: Optional["QuestionAnalysis"]) -> list[dict[str, str]]:
    """최종 프롬프트 직전 researcher 객체 매칭에 사용할 힌트를 구성한다."""
    if not qa:
        return []

    filters = dict(getattr(qa, "filters", {}) or {})
    ids_map = dict(getattr(qa, "ids_map", {}) or {})

    names = _normalize_hint_terms([
        *(_normalize_hint_terms(filters.get("participant_researcher_name"))),
        *(_normalize_hint_terms(filters.get("researcher_name") or filters.get("people_name"))),
    ])
    affiliations = _normalize_hint_terms(filters.get("people_affiliation_org_name"))
    ids = _normalize_hint_terms(ids_map.get("person_no") or ids_map.get("hm_id"))

    if not names and not ids:
        return []

    default_affiliation = affiliations[0] if len(affiliations) == 1 else ""
    hints: list[dict[str, str]] = []

    for idx, name in enumerate(names):
        affiliation = affiliations[idx] if idx < len(affiliations) else default_affiliation
        researcher_id = ids[idx] if idx < len(ids) else ""
        hints.append(
            {
                "name": str(name).strip(),
                "affiliation": str(affiliation).strip(),
                "researcher_id": str(researcher_id).strip(),
            }
        )

    if not hints and ids:
        for researcher_id in ids:
            hints.append({"name": "", "affiliation": default_affiliation, "researcher_id": str(researcher_id).strip()})

    return hints


# --- Graph Construction ---
def build_advanced_workflow():
    workflow = StateGraph(AgentState)

    # Add Nodes
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

        if ks.requires_new_knowledge == "low" and state.prev_context:
            return ["generate_answer_solar", "generate_answer_gemma"]

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


    # Direct answer also goes to save
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
    *,
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
    for line in fallback_lines[:max_matches]:
        cleaned = line.lstrip("- ").strip()
        if cleaned:
            fallback_names.append(cleaned)
    if fallback_names:
        return f"- 연구자: {', '.join(fallback_names)}"

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


def summarize_documents_headlines(
    docs: List[Document],
    *,
    researchers: Optional[List[Any]] = None,
    organizations: Optional[List[Any]] = None,
    org_filters: Optional[Dict[str, Any]] = None,
    ids_map: Optional[Dict[str, Any]] = None,
    max_matches: int = 5,
) -> str:
    headlines: List[str] = []

    for doc in docs:
        mapped_doc = _safe_map_doc(doc, context="summarize_documents_headlines")
        if not mapped_doc:
            continue
        _apply_title_preference(mapped_doc)
        source_idx = doc.get("source_index")
        title = mapped_doc.get("title", "제목 없음")

        prtcp_members = mapped_doc.get("prtcp_mp", []) if isinstance(mapped_doc, dict) else []
        matched_members = _match_prtcp_members(prtcp_members, researchers, max_matches=max_matches)
        fallback_lines = RagMapper.get_researcher_info(mapped_doc)
        researcher_line = _format_researcher_line(
            matched_members,
            fallback_lines,
            max_matches=max_matches,
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

        headlines.append(
            f"## 출처 {source_idx}. {title}\n"
            f"{researcher_line}\n"
            f"{org_line}\n"
        )

    return "\n\n".join(headlines)


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
) -> str:
    context_chunks: List[str] = []
    field_max_sentences = None if relax_limits else MAX_FIELD_SENTENCES
    field_max_tokens = None if relax_limits else MAX_FIELD_TOKENS
    doc_max_sentences = None if relax_limits else MAX_DOC_SENTENCES
    doc_max_tokens = None if relax_limits else MAX_DOC_TOKENS

    for doc in docs:
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
            max_matches=max_matches,
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
        # log_section("refine_documents_rule_based - 페이로드 평탄화 메소드 내부",
        #             f"matched_members: {matched_members}\n"
        #             f"fallback_lines: {fallback_lines}\n"
        #             f"matched_orgs: {matched_orgs}")

        limited_body = _limit_text_by_sentences_and_tokens(
            refined_text,
            max_sentences=doc_max_sentences,
            max_tokens=doc_max_tokens,
        )
        body_sentences = _split_sentences(limited_body)
        body_token_counts = [len(sentence.split()) for sentence in body_sentences]
        body_token_count = sum(body_token_counts)
        extra_lines = [line for line in [researcher_line, org_line] if line]
        extra_text = "\n".join(extra_lines)
        extra_sentences = _split_sentences(extra_text)
        extra_token_count = len(extra_text.split())

        if not relax_limits:
            while body_sentences and (
                len(body_sentences) + len(extra_sentences) > MAX_DOC_SENTENCES
                or body_token_count + extra_token_count > MAX_DOC_TOKENS
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

# --- Lifespan & App Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    global kv_store

    build_rag_objects()

    backend = os.getenv("MEMORY_BACKEND", "memory").strip().lower()
    # MEMORY_BACKEND=redis|memory|file

    if backend == "redis":
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

    elif backend == "memory":
        kv_store = MemoryKVStore()
        logger.info("✅ MemoryKVStore enabled")

    elif backend == "file":
        kv_store = FileKVStore(root_dir=os.getenv("LOCAL_KV_DIR", "local_kvstore"))
        logger.info("✅ FileKVStore enabled: %s", os.getenv("LOCAL_KV_DIR", "local_kvstore"))

    else:
        kv_store = MemoryKVStore()
        logger.warning("⚠️ Unknown MEMORY_BACKEND=%s, fallback to MemoryKVStore", backend)

    try:
        workflow = build_advanced_workflow().compile()
        app.state.graph = workflow
        logger.info("✅ Advanced Dual-Model Pipeline compiled successfully")
        yield
    finally:
        if kv_store:
            await kv_store.close()

app = FastAPI(lifespan=lifespan)

# --- Endpoints ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

class QueryRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None

@app.post("/query/stream")
async def query_stream(payload: QueryRequest):
    """스트리밍 응답 엔드포인트 (두 모델 비교)"""

    question = payload.question
    conversation_id = payload.conversation_id or str(uuid.uuid4())

    graph = app.state.graph

    async def event_generator():
        yield f"data: {json.dumps({'conversationId': conversation_id})}\n\n"

        loaded_history, prev_context, _ = await load_conversation_memory(conversation_id)
        user_message = HumanMessage(content=question)
        chat_history = loaded_history + [user_message]
        intent_payload, question_analysis = await build_intent_payload(
            question,
            conversation_id,
            chat_history,
            prev_context,
        )
        inputs = {
            "conversation_id": conversation_id,
            "messages": [user_message],
            "intent_payload": intent_payload,
            "question_analysis": question_analysis,
        }

        log_section("REQUEST START", f"ID: {conversation_id}\nQ: {question}")

        documents_used = []

        try:
            async for event in graph.astream_events(inputs, version="v2"):
                kind = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")
                data = event.get("data", {})
                # Answer 스트리밍 - solar
                if kind == "on_chat_model_stream" and node == "generate_answer_solar":
                    chunk = data.get("chunk")
                    if hasattr(chunk, "content") and chunk.content:
                        yield f"data: {json.dumps({'model': 'SOLAR', 'model_key': 'solar', 'model_legacy_key': 'gpt', 'content': chunk.content}, ensure_ascii=False)}\n\n"

                # Answer 스트리밍 - Gemma
                elif kind == "on_chat_model_stream" and node == "generate_answer_gemma":
                    chunk = data.get("chunk")
                    if hasattr(chunk, "content") and chunk.content:
                        yield f"data: {json.dumps({'model': 'GEMMA', 'model_key': 'gemma', 'content': chunk.content}, ensure_ascii=False)}\n\n"

                # Direct Answer (rule-based)
                elif kind == "on_chain_end" and node == "direct_answer":
                    output = data.get("output", {})
                    if "answer_gemma" in output:
                        answer = output["answer_gemma"]
                        yield f"data: {json.dumps({'model': 'SOLAR', 'model_key': 'solar', 'model_legacy_key': 'gpt', 'content': answer}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'model': 'GEMMA', 'model_key': 'gemma', 'content': answer}, ensure_ascii=False)}\n\n"

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

            # log_section("REF PUSH", f"coq: {conversation_id}{question}\n{json.dumps(ref_docs, ensure_ascii=False, indent=2)}")

            yield f"data: {json.dumps({'reference': ref_docs}, ensure_ascii=False)}\n\n"

            # 루프 종료 후
            yield f"data: {json.dumps({'status': 'done'})}\n\n"

        except Exception as e:
            logger.error(f"Stream Error: {e}", exc_info=True)
            error_code = getattr(e, "error_code", "INTERNAL_ERROR")
            reason = getattr(e, "reason", str(e))
            yield f"data: {json.dumps({'error': str(e), 'error_code': error_code, 'reason': reason}, ensure_ascii=False)}\n\n"

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

    loaded_history, prev_context, _ = await load_conversation_memory(conversation_id)
    chat_history = loaded_history + [HumanMessage(content=question)]
    intent_payload, question_analysis = await build_intent_payload(
        question,
        conversation_id,
        chat_history,
        prev_context,
    )

    inputs = {
        "conversation_id": conversation_id,
        "messages": [HumanMessage(content=question)],
        "intent_payload": intent_payload,
        "question_analysis": question_analysis,
    }

    graph = app.state.graph

    try:
        final_state = await graph.ainvoke(inputs)

        question_analysis = final_state.get("question_analysis")
        knowledge_sufficiency = final_state.get("knowledge_sufficiency")

        answer_solar = final_state.get("answer_solar")

        return {
            "success": True,
            "conversation_id": conversation_id,
            "answer_gemma": final_state.get("answer_gemma"),
            "answer_solar": answer_solar,
            "output_message": final_state["messages"][-1].content,
            "question_analysis": question_analysis.model_dump() if question_analysis else None,
            "knowledge_sufficiency": knowledge_sufficiency.model_dump() if knowledge_sufficiency else None,
            "documents_used": len(final_state.get("context", [])),
            "latencies": final_state.get("latencies", {}),
            "total_time": sum(final_state.get("latencies", {}).values()),
            "processing_strategy": knowledge_sufficiency.requires_new_knowledge if knowledge_sufficiency else "unknown"
        }

    except Exception as e:
        logger.error(f"Debug Error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "error_code": getattr(e, "error_code", "INTERNAL_ERROR"),
            "reason": getattr(e, "reason", str(e)),
        }

@app.get("/health")
async def health_check():
    ok = False
    if kv_store:
        ok = await kv_store.ping()
    return {
        "status": "healthy",
        "memory_backend": os.getenv("MEMORY_BACKEND", "redis"),
        "kv": "connected" if ok else "disconnected",
        "graph": "compiled" if hasattr(app.state, "graph") else "not_ready"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007, access_log=False)
