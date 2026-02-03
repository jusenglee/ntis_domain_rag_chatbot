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
from pydantic import BaseModel, Field
from enum import Enum

# Redis
import redis.asyncio as redis

# LangChain & LangGraph
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_core.tools import Tool
from langchain_core.output_parsers import StrOutputParser, PydanticOutputParser

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages

# --- User Modules ---
from rag_store import build_rag_objects
from triton_llm import TritonChatModel
from rag_pipeline import run_rag_ab_compare
from rag_parts.pipeline_steps import normalize_intent
from rag_parts.query_intent import classify_query as classify_query_intent, _cheap_precheck
from retrieval import extract_keywords
from settings import (
    REDIS_URL,
    REDIS_TTL,
    MAX_TOP_K_SIZE,
    MAX_CONTEXT_CHARS,
    MAX_DOC_SENTENCES,
    MAX_DOC_TOKENS,
    SUMMARY_DOC_SENTENCES,
    SUMMARY_DOC_TOKENS,
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
redis_client: Optional[redis.Redis] = None
MAX_HISTORY_TURNS = 10
HISTORY_PREVIEW_LIMIT = 100
SHORT_ANSWER_MAX_TOKENS_HINT = int(os.getenv("SHORT_ANSWER_MAX_TOKENS_HINT", "1024"))
FOLLOW_UP_MAX_TOKENS_HINT = int(os.getenv("FOLLOW_UP_MAX_TOKENS_HINT", "2048"))

def _select_max_tokens_hint(qa: Optional["QuestionAnalysis"]) -> Optional[int]:
    if not qa:
        return None
    if qa.question_type == QuestionType.FOLLOW_UP:
        return FOLLOW_UP_MAX_TOKENS_HINT
    if qa.question_type == QuestionType.DEFAULT and qa.mode in (None, "SEARCH"):
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

class ContentCategory(str, Enum):

    PROJECT = "project"          # 과제/연구개발
    RESEARCHER = "researcher"    # 연구자
    PERFORMANCE = "performance"  # 성과
    QNA = "qna"                  # 질의응답/매뉴얼
    ETC = "etc"                  # 기타

class QuestionType(str, Enum):
    DEFAULT = "default"
    FOLLOW_UP = "follow_up"

class Researcher(BaseModel):
    name: str | None = None
    affiliation: str | None = None
    researcher_id: str | None = None

# --- Pydantic Schemas for Structured Output ---
class QuestionAnalysis(BaseModel):
    """질문 분석 결과"""
    category: list[ContentCategory] = Field(description="질문 카테고리")
    question_type: QuestionType = Field(description="질문유형")
    related_docs: list[int] = Field(description="follow_up 의 관련 출처 번호 리스트")
    researchers: list[Researcher] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list, description="질문에서 특정 기관이 식별되는 경우")
    mode: str | None = Field(default=None, description="SEARCH | LOOKUP | JOIN")
    head: str | None = Field(default=None, description="project | perf | people | org | support")
    relation: str | None = Field(default=None, description="project_perf | people_project 등")
    ids_map: dict[str, list[str]] = Field(default_factory=dict, description="ID 추출 결과")
    filters: dict[str, Any] = Field(default_factory=dict, description="필터 파라미터")
    limit: int = Field(
        MAX_TOP_K_SIZE,
        description=f"반환 문서 개수 (최대 {MAX_TOP_K_SIZE})",
        le=MAX_TOP_K_SIZE,
    )
    history_summary: str = Field(description="대화 이력 기반 질문 요약")
    retrieval_query: str = Field(description="벡터 검색용 최적화된 쿼리")
    confidence: float = Field(ge=0.0, le=1.0, description="분석 신뢰도")

class SearchHint(BaseModel):
    """RAG 검색 힌트"""
    coq: str = ""
    category: list[str] = Field(default_factory=list)
    researchers: list[dict[str, str | None]] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    mode: str | None = None
    head: str | None = None
    relation: str | None = None
    ids_map: dict[str, list[str]] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(
        MAX_TOP_K_SIZE,
        description=f"반환 문서 개수 (최대 {MAX_TOP_K_SIZE})",
        le=MAX_TOP_K_SIZE,
    )
    history_summary: str = ""
    retrieval_query: str = ""
    confidence: float = 0.0

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
    messages: Annotated[List[BaseMessage], add_messages]

    # Redis 데이터
    chat_history: List[BaseMessage] = Field(default_factory=list)
    prev_context: List[Dict] = Field(default_factory=list)

    # 처리 데이터
    context: List[Dict] = Field(default_factory=list)

    # 각 모델별 답변 저장
    answer_gemma: Optional[str] = None
    answer_gpt: Optional[str] = None

    # 메타데이터
    conversation_id: str = ""

    question: str = ""

    rule_decision: Optional[RuleDecision] = None
    question_analysis: Optional[QuestionAnalysis] = None
    knowledge_sufficiency: Optional[KnowledgeSufficiency] = None
    intent_payload: Optional[Dict[str, Any]] = None

    def merge_latencies(existing: Dict[str, float], new: Dict[str, float]) -> Dict[str, float]:
        """병렬 노드에서 latencies가 동시에 업데이트될 때 병합"""
        result = existing.copy()
        result.update(new)
        return result

    latencies: Annotated[Dict[str, float], merge_latencies] = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True

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
    loaded_history, ctx_list = await load_conversation_memory(cid)
    current_full_history = loaded_history + [state.messages[-1]]

    log_section("LOAD MEMORY",
                f"coq: {cid}{state.messages[-1].content}\nHistory: {len(loaded_history)} turns\nPrev Context: {len(ctx_list)} docs")
    return {
        "question": state.messages[-1].content,
        "chat_history": current_full_history,
        "prev_context": ctx_list
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
    if state.intent_payload and state.intent_payload.get("question_analysis"):
        return {"question_analysis": state.intent_payload["question_analysis"]}

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
) -> QuestionAnalysis:
    llm = TritonChatModel(model_name="gpt_oss_0")  # GPT
    parser = PydanticOutputParser(pydantic_object=QuestionAnalysis)

    history = chat_history[-6:]
    history_str = "\n".join([f"{type(m).__name__}: {m.content}" for m in history])

    prev_context_str = refine_documents_rule_based(prev_context)

    system_prompt = (
        "당신은 NTIS R&D 데이터 질의 분석 전문가입니다.\n"
        "이 시스템에서 사용되는 용어는 모두 'R&D 행정/과제/성과/연구자/기관/시스템 이용(QnA)' 맥락으로 해석합니다.\n"
        "절대 임의로 추측하지 말고, 근거가 약하면 confidence를 낮추고 필드 값을 null/[]로 둡니다.\n\n"
    
        "====================\n"
        "[출력 강제 규칙]\n"
        "====================\n"
        "1) 반드시 JSON 객체만 출력합니다. (설명/마크다운/코드블럭 금지)\n"
        "2) enum 값은 아래 정의된 값만 사용합니다. 철자/대소문자 정확히.\n"
        "3) 모호하면 문장으로 회피하지 말고(confidence 낮춤), 관련 필드는 null/[] 처리.\n"
        "4) related_docs는 FOLLOW_UP일 때만 채우고, 아니면 [] 입니다.\n"
        "5) researchers/organizations는 '특정' 대상이 식별될 때만 포함합니다. (없으면 [])\n\n"
    
        "====================\n"
        "[Category 분류 규칙]\n"
        "====================\n"
        "1) 질문이 특정 데이터 유형을 명시/강하게 암시하면 해당 category를 포함합니다.\n"
        "2) QNA는 '시스템 사용법/절차/메뉴얼/오류/이용 안내'에만 해당합니다.\n"
        "3) 복수 영역이 명확하면 category는 복수 선택 가능합니다.\n"
        "4) 사람/기관 이름이 나오더라도, 목적이 과제/성과 목록이면 PROJECT/PERFORMANCE를 반드시 포함합니다.\n\n"
    
        "====================\n"
        "[Category 정의]\n"
        "====================\n"
        "- PROJECT: 과제, 연구개발, 참여인력, 참여기관, 과제번호/기간/주관/참여기관/책임자/참여자\n"
        "- PERFORMANCE: 논문, 특허, SW, 연구보고서, 시설장비 등 성과 전반(ISSN/DOI/특허번호 포함)\n"
        "- RESEARCHER: 연구자 정보(연구자번호/이력/소속/식별)\n"
        "- QNA: 시스템 사용법, 절차, 메뉴얼, 오류\n"
        "- ETC: 그 외\n\n"
    
        "====================\n"
        "[QuestionType 정의]\n"
        "====================\n"
        "- DEFAULT\n"
        "- FOLLOW_UP: 아래 중 하나라도 만족\n"
        "  1) 질문의 핵심 대상이 이전 응답에서 정의된 특정 엔트리(특정 과제/특정 출처번호/특정 문서)에 종속\n"
        '  2) "그 과제", "해당 연구", "출처 N", "앞서 언급한" 등 지시어 포함\n'
        "  3) 단, '목록을 더 보여줘/추가로 알려줘' 같은 단순 확장 요청은 FOLLOW_UP이 아님\n\n"
    
        "====================\n"
        "[Mode / Head / Relation 결정 규칙 (최우선)]\n"
        "====================\n"
        "아래 규칙은 위에서부터 우선순위가 높습니다. 하나라도 만족하면 해당 결정을 따릅니다.\n\n"
    
        "A) FOLLOW_UP이면\n"
        "- mode=LOOKUP\n"
        "- head는 이전 문맥에서 지칭한 대상의 head를 따릅니다(불명확하면 null).\n\n"
    
        "B) 명시적 식별자(id) 질의이면 (ids_map에 값이 들어가는 경우)\n"
        "- mode=LOOKUP\n"
        "- head는 식별자가 가리키는 데이터로 결정\n"
        "  * pjt_id/과제번호 => head=project\n"
        "  * doi/issn/특허번호/성과식별자 => head=perf\n\n"
    
        "C) 관계형 질의(참여/소속/연관/목록 요청) 판단 규칙\n"
        "- 다음 중 하나라도 있으면 관계형 질의입니다:\n"
        "  * 사람/기관(고유명) + (참여/소속/연관/목록/과제/성과/논문/특허/SW/보고서 등)\n"
        "  * 'OOO의 과제', 'OOO 연구자의 논문', 'OO기관 성과' 같은 소유/관계 표현\n"
        "- 관계형 질의는 SEARCH 금지. mode는 LOOKUP 또는 JOIN만 사용.\n"
        "- head는 '질문의 출발점 엔티티'로 고정:\n"
        "  * 연구자 이름/연구자ID가 출발점이면 head=people\n"
        "  * 기관명/기관ID가 출발점이면 head=org\n"
        "  * 과제가 출발점이면 head=project\n"
        "  * 성과가 출발점이면 head=perf\n"
        "- relation은 반드시 설정:\n"
        "  * people -> project/perf: people_project 또는 people_perf\n"
        "  * org -> project/perf: org_project 또는 org_perf\n"
        "  * project <-> perf: project_perf 또는 perf_project\n"
        "- JOIN은 다음 조건에서만 사용:\n"
        "  * 사람/기관의 ID(연구자번호/기관코드 등)가 있고, 추가 조건(연도/유형 등)까지 있어 2단계가 유리할 때\n"
        "  * 그 외에는 LOOKUP\n\n"
    
        "D) 목록/통계/다운로드/필터 중심 조회이면\n"
        "- mode=LOOKUP\n\n"
    
        "E) 위 조건에 해당하지 않는 주제/개념 중심 탐색이면\n"
        "- mode=SEARCH\n"
        "- head는 가장 중심 데이터(애매하면 project)\n\n"
    
        "====================\n"
        "[Relation enum]\n"
        "====================\n"
        "relation은 아래 중 하나 또는 null:\n"
        "- project_perf, perf_project\n"
        "- people_project, project_people\n"
        "- org_project, project_org\n"
        "- people_perf, perf_people\n"
        "- org_perf, perf_org\n\n"
    
        "====================\n"
        "[ids_map 규칙]\n"
        "====================\n"
        "ids_map은 dict이며, 키는 아래 허용 키만 사용합니다. 값은 문자열 배열입니다.\n"
        "허용 키 예시: pjt_id, researcher_id, org_id, doi, issn, eissn, pissn, patent_reg_no, patent_app_no, paper_id, perf_id, rst_id\n"
        "추출하지 못하면 빈 dict로 둡니다.\n\n"
    
        "====================\n"
        "[filters 규칙]\n"
        "====================\n"
        "filters는 dict입니다. 필요한 것만 포함합니다.\n"
        "가능한 키: year_from, year_to, org_name, researcher_name, tag_filters, perf_types, keywords\n"
        "관계형 질의에서 researcher_name/org_name이 잡히면 반드시 filters에 포함합니다.\n\n"
    
        "====================\n"
        "[tag_filters 매핑 규칙]\n"
        "====================\n"
        "1) 과제/참여/기관/연구책임/참여인력 => IRD_NAI_PJT_INFO\n"
        "2) 논문/학술지/ISSN/DOI => IRD_NAI_RI_PAPER\n"
        "3) 특허/출원/등록/특허번호 => IRD_NAI_RI_IPR\n"
        "4) 소프트웨어/SW => IRD_NAI_RI_SW\n"
        "5) 연구보고서 => IRD_NAI_RI_RSCH_RPT\n"
        "6) 시설장비 => IRD_NAI_RI_FCLT_EQUIP\n"
        "7) 기술요약 => IRD_NAI_RI_TECH_INFO\n"
        "8) 모르면 tag_filters 생략 가능(단, PROJECT/PERFORMANCE가 명확하면 채우는 쪽 우선)\n\n"
    
        "====================\n"
        "[retrieval_query 생성 규칙]\n"
        "====================\n"
        "retrieval_query는 검색 최적화용 짧은 쿼리입니다.\n"
        "- 핵심 개념 5개 이내, 최대 120자\n"
        "- 불필요한 기능어 제거: '연구자', '참여한', '목록', '조회', '알려줘', '무엇', '어떤' 등은 되도록 제외\n"
        "- 사람/기관명이 있으면 반드시 포함\n"
        "- 연도 범위가 있으면 포함(예: '2018~2020')\n\n"
    
        "====================\n"
        "[limit 규칙]\n"
        "====================\n"
        f"- limit는 1~{MAX_TOP_K_SIZE} 범위 정수\n"
        "- 사용자가 '상위 N개' 등 명시하면 반영(단, MAX 초과 금지)\n"
        "- 불명확하면 20\n\n"
    
        "====================\n"
        "[history_summary 규칙]\n"
        "====================\n"
        "- 대화 이력 기반으로 질문 핵심을 1문장 요약\n"
        "- 이력이 없으면 '이전 문맥 없음'을 반영\n\n"
    
        "====================\n"
        "[출력 형식]\n"
        "====================\n"
        "아래 키를 반드시 모두 포함한 JSON 객체만 출력:\n"
        "1) category: 배열 (PROJECT|PERFORMANCE|RESEARCHER|QNA|ETC)\n"
        "2) question_type: (DEFAULT|FOLLOW_UP)\n"
        "3) related_docs: int 배열\n"
        "4) researchers: 객체 배열 (각 요소는 name, affiliation, researcher_id 키를 가짐)\n"
        "5) organizations: 객체 배열 (각 요소는 name, org_id 키를 가짐)\n"
        "6) mode: SEARCH | LOOKUP | JOIN\n"
        "7) head: project | perf | people | org | support\n"
        "8) relation: Relation enum 중 하나 또는 null\n"
        "9) ids_map: dict\n"
        "10) filters: dict\n"
        f"11) limit: int (<= {MAX_TOP_K_SIZE})\n"
        "12) history_summary: string\n"
        "13) retrieval_query: string\n"
        "14) confidence: float (0.0~1.0)\n\n"
    
        "{format_instructions}"
    )


    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "[대화 이력]\n{history}\n\n[이전 정보]\n{prev_context}\n\n[현재 질문]\n{question}")
    ])

    try:
        chain = prompt | llm | sanitize_llm_json | parser
        result: QuestionAnalysis = await chain.ainvoke({
            "format_instructions": parser.get_format_instructions(),
            "history": history_str or "없음",
            "prev_context": prev_context_str or "없음",
            "question": question
        })
        result.limit = min(result.limit, MAX_TOP_K_SIZE)

        log_section(
            "QUESTION ANALYSIS",
            f"coq: {conversation_id}{question}\n"
            f"Category: {result.category}\n"
            f"QuestionType: {result.question_type}\n"
            f"RelatedDocs: {result.related_docs}\n"
            f"Researchers: {result.researchers}\n"
            f"Organizations: {result.organizations}\n"
            f"Mode: {result.mode}\n"
            f"Head: {result.head}\n"
            f"Relation: {result.relation}\n"
            f"IdsMap: {result.ids_map}\n"
            f"Filters: {result.filters}\n"
            f"Limit: {result.limit}\n"
            f"Summary: {result.history_summary}\n"
            f"Query: {result.retrieval_query}\n"
            f"Confidence: {result.confidence:.2f}"
        )
        return result

    except Exception as e:
        logger.error(f"Question Analysis Error: {e}")
        return QuestionAnalysis(
            category=[ContentCategory.ETC],
            question_type=QuestionType.DEFAULT,
            related_docs=[],
            researchers=[],
            organizations=[],
            mode=None,
            head=None,
            relation=None,
            ids_map={},
            filters={},
            limit=20,
            history_summary=question,
            retrieval_query=question[:120],
            confidence=0.5
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
        query_intent = state.intent_payload.get("query_intent")
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

    if qa and qa.question_type == QuestionType.DEFAULT and not state.prev_context:
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

    llm = TritonChatModel(model_name="gemma_vllm_0")
    parser = PydanticOutputParser(pydantic_object=KnowledgeSufficiency)

    prev_context_str = None

    if qa.question_type == QuestionType.FOLLOW_UP:
        if len(qa.related_docs) > 0:
            related_doc_indexes = set(qa.related_docs)
            related_context = [
                doc
                for doc in state.prev_context
                if doc.get("source_index") in related_doc_indexes
            ]
            if related_context:
                prev_context_str = refine_documents_rule_based(related_context, True)
            else:
                # fallback: 전체 prev_context 사용
                related_context = state.prev_context
                prev_context_str = refine_documents_rule_based(related_context)
        else:
            # fallback: 전체 prev_context 사용
            related_context = state.prev_context
            prev_context_str = refine_documents_rule_based(related_context)



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
    model_name: str = "gemma_vllm_0"
    top_k: int = 5

    hint: Optional[SearchHint] = None
    intent_payload: Optional[Dict[str, Any]] = None

    class Config:
        arbitrary_types_allowed = True

    def retrieve(self, query: str) -> List[Dict]:
        """동기 검색 함수"""
        res_map = run_rag_ab_compare(
            query=query,
            model_name=self.model_name,
            hint=self.hint,
            intent_payload=self.intent_payload,
        )
        res_m = res_map.get("M") or res_map.get("A") or next(iter(res_map.values()))

        hits = getattr(res_m, "reranked_hits", []) or []

        if not hits:
            context_str = getattr(res_m, "context", "")
            if context_str.strip():
                return [{"title": context_str}]
            return []

        documents = []
        for idx, hit in enumerate(hits[:self.top_k], start=1):
            if hasattr(hit, "payload"):
                hit_data = hit.payload
                score = getattr(hit, "score", 0.0)
            elif isinstance(hit, dict):
                hit_data = hit
                score = hit.get("score", 0.0)
            else:
                hit_data = getattr(hit, "__dict__", {})
                score = 0.0

            rag_data = {
                "title": hit_data.get("title1") or hit_data.get("title_text"),
                "source_index" : idx,
                "tag" : hit_data.get("tag"),
                "meta_basic" : hit_data.get("meta_basic", {}),
                "meta_detail" : hit_data.get("meta_detail", {}),
                "prtcp_mp" : hit_data.get("prtcp_mp", [])
            }

            if hit_data.get("tag") is not None:
                documents.append(rag_data)

        return documents

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

        hint = SearchHint(
            coq=f"{state.conversation_id}{state.question}",
            category=[c.value if hasattr(c, "value") else str(c) for c in (qa.category or [])] if qa else [],
            researchers=[
                {"name": r.name, "researcher_id": r.researcher_id} for r in (qa.researchers or [])
            ] if qa else [],
            organizations=list(qa.organizations or []) if qa else [],
            mode=(qa.mode if qa else None),
            head=(qa.head if qa else None),
            relation=(qa.relation if qa else None),
            ids_map=dict(qa.ids_map or {}) if qa else {},
            filters=dict(qa.filters or {}) if qa else {},
            limit=search_num,
            history_summary=(qa.history_summary if qa else ""),
            retrieval_query=hint_query,
            confidence=confidence,
        )

        retriever = CustomRAGRetriever(
            top_k=search_num,
            model_name="gemma_vllm_0",
            hint=hint,
            intent_payload=state.intent_payload,
        )
        # NOTE: hint.limit=search_num is used by rag_pipeline hydrate upper bound,
        # so top_k > preset.max_ctx_items still gets fully hydrated payload.

        rag_tool = Tool(
            name="RAG_Search",
            description="NTIS/IRIS 데이터베이스 검색",
            func=retriever.retrieve
        )

        docs = await asyncio.to_thread(rag_tool.func, search_query)

        doc_previews = []
        for i, doc in enumerate(docs, 1):
            doc_previews.append(
                json.dumps(doc, ensure_ascii=False, indent=2)
            )

        log_section("RAG SEARCH",
                    f"coq: {state.conversation_id}{state.question}\n"
                    f"Query: {search_query}\n"
                    f"Found: {len(docs)} docs\n")
        return {"context": docs}

    except Exception as e:
        logger.error(f"❌ RAG Error: {e}")
        return {"context": []}


# --- Node 7: Refine Answer - Gemma ---
@measure_latency("generate_answer_gemma")
async def node_generate_answer_gemma(state: AgentState) -> Dict[str, Any]:
    """RAG 결과로 Fast Answer 보강 - Gemma"""
    return await _generate_answer(state, "gemma_vllm_0", "answer_gemma")

# --- Node 7-2: Refine Answer - GPT ---
@measure_latency("generate_answer_gpt")
async def node_generate_answer_gpt(state: AgentState) -> Dict[str, Any]:
    """RAG 결과로 Fast Answer 보강 - GPT"""
    return await _generate_answer(state, "gpt_oss_0", "answer_gpt")


import aiofiles

async def load_system_prompt(path: Path) -> str:
    async with aiofiles.open(path, encoding="utf-8") as f:
        return await f.read()

async def _generate_answer(state: AgentState, model_name: str, final_field: str) -> Dict[str, Any]:
    llm = TritonChatModel(model_name=model_name)

    ks = state.knowledge_sufficiency
    qa = state.question_analysis

    # ✅ 1) 기본은 "현재 검색 컨텍스트" 사용
    docs_for_ctx = state.context or state.prev_context or []
    is_detail = False

    # ✅ 2) FOLLOW_UP이면 필요한 것만 좁히고(detail로)
    if qa and qa.question_type == QuestionType.FOLLOW_UP:
        is_detail = True
        if qa.related_docs:
            related_context = [
                state.prev_context[i - 1]
                for i in qa.related_docs
                if 1 <= i <= len(state.prev_context)
            ]
            docs_for_ctx = related_context or docs_for_ctx

    context_text = refine_documents_rule_based(docs_for_ctx, is_detail) if docs_for_ctx else "없음"
    if len(context_text) > MAX_CONTEXT_CHARS and docs_for_ctx:
        context_text = summarize_documents_headlines(docs_for_ctx)
        context_text = _truncate_context_text(context_text, MAX_CONTEXT_CHARS)

    SYSTEM_PROMPT_PATH = Path("prompts/ntis_chatbot.md")
    system_prompt = await load_system_prompt(SYSTEM_PROMPT_PATH)

    human_prompt = (
        f"[제공된 정보]\n{context_text}\n\n"
        f"[질문 요약]\n{(qa.history_summary if qa else '')}\n\n"
        f"[원본 질문]\n{state.messages[-1].content}"
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
        "answer_gpt": response_text,
        "messages": [AIMessage(content=response_text)]
    }

# --- Node 9: Merge Answers ---
@measure_latency("merge_answers")
async def node_merge_answers(state: AgentState) -> Dict[str, Any]:

    ks = state.knowledge_sufficiency
    strategy = ks.requires_new_knowledge if ks else "unknown"
    gemma_preview = _truncate_text(state.answer_gemma, HISTORY_PREVIEW_LIMIT)
    gpt_preview = _truncate_text(state.answer_gpt, HISTORY_PREVIEW_LIMIT)

    # messages에는 gemma 답변을 기본으로 추가
    log_section("MERGE ANSWERS",
                f"coq: {state.conversation_id}{state.question}\n"
                f"Strategy: {strategy}\n"
                f"Gemma: {gemma_preview}\n"
                f"GPT: {gpt_preview}")

    return {
        "messages": [AIMessage(content=state.answer_gpt)],
        "answer_gemma": state.answer_gemma,
        "answer_gpt": state.answer_gpt,
        "context" : state.context
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

    if redis_client:
        try:
            await redis_client.set(
                f"conversation:{cid}:history",
                json.dumps(serialized_hist, ensure_ascii=False),
                ex=REDIS_TTL,
            )

            if state.context:
                await redis_client.set(
                    f"conversation:{cid}:last_context",
                    json.dumps(state.context, ensure_ascii=False),
                    ex=REDIS_TTL,
                )
        except Exception as e:
            logger.error("Redis Save Error: %s", e, exc_info=True)

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

async def load_conversation_memory(conversation_id: str) -> tuple[List[BaseMessage], List[Dict[str, Any]]]:
    loaded_history: List[BaseMessage] = []
    ctx_list: List[Dict[str, Any]] = []

    if redis_client:
        try:
            raw_hist = await redis_client.get(f"conversation:{conversation_id}:history")
            hist_list = _safe_json_loads(raw_hist)
            loaded_history = _deserialize_history(hist_list)

            raw_ctx = await redis_client.get(f"conversation:{conversation_id}:last_context")
            ctx_payload = _safe_json_loads(raw_ctx)
            if isinstance(ctx_payload, list):
                ctx_list = ctx_payload
        except Exception as e:
            logger.error("Redis Load Error: %s", e, exc_info=True)

    return loaded_history, ctx_list

async def build_intent_payload(
    question: str,
    conversation_id: str,
    chat_history: List[BaseMessage],
    prev_context: List[Dict[str, Any]],
) -> Dict[str, Any]:
    precheck = _cheap_precheck(question)
    question_analysis = None
    if not precheck:
        question_analysis = await _run_question_analysis(
            question=question,
            conversation_id=conversation_id,
            chat_history=chat_history,
            prev_context=prev_context,
        )

    kws = extract_keywords(question)
    hint_people_terms = [r.name for r in (question_analysis.researchers or []) if r.name] if question_analysis else []
    hint_org_terms = list(question_analysis.organizations or []) if question_analysis else []
    hint_org_role = None
    if question_analysis and isinstance(question_analysis.filters, dict):
        hint_org_role = question_analysis.filters.get("org_role")

    raw_intent = classify_query_intent(
        question,
        kws,
        domain_hint=question_analysis.head if question_analysis else None,
        hint=question_analysis.model_dump() if question_analysis else None,
    )

    normalized_intent = normalize_intent(
        raw_intent,
        query=question,
        keywords=kws,
        hint_people_terms=hint_people_terms,
        hint_org_terms=hint_org_terms,
        hint_org_role=hint_org_role,
    )

    _apply_question_analysis_to_intent(normalized_intent, question_analysis)

    return {
        "question_analysis": question_analysis,
        "query_intent": raw_intent,
        "normalized_intent": normalized_intent,
        "keywords": kws,
    }


def _parse_relation_hint(relation: Any) -> Optional[tuple[str, str]]:
    if not relation:
        return None
    if isinstance(relation, (list, tuple)) and len(relation) == 2:
        left, right = relation
        return (str(left).strip().lower(), str(right).strip().lower())
    text = str(relation).strip().lower()
    if not text:
        return None
    if "_" in text or ">" in text:
        text = text.replace(">", "_")
        parts = [p.strip() for p in text.split("_") if p.strip()]
        if len(parts) == 2:
            return (parts[0], parts[1])
    return None


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
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _merge_ids_map(base: dict[str, list[str]], incoming: dict[str, Any]) -> dict[str, list[str]]:
    merged = dict(base or {})
    if not isinstance(incoming, dict):
        return merged
    for key, values in incoming.items():
        if values is None:
            continue
        if not isinstance(values, list):
            values = [values]
        norm = _normalize_hint_terms(values)
        if not norm:
            continue
        merged[key] = list(dict.fromkeys(list(merged.get(key, [])) + norm))
    return merged


def _apply_question_analysis_to_intent(normalized_intent, question_analysis: QuestionAnalysis) -> None:
    if not question_analysis:
        return
    if question_analysis.head:
        normalized_intent.base_route = question_analysis.head
    relation = _parse_relation_hint(question_analysis.relation)
    if relation:
        normalized_intent.relation = relation

    normalized_intent.ids_map = _merge_ids_map(
        getattr(normalized_intent, "ids_map", {}),
        dict(question_analysis.ids_map or {}),
    )

    org_terms = _normalize_hint_terms(question_analysis.organizations)
    if org_terms:
        normalized_intent.org_terms = org_terms

    people_terms = [r.name for r in (question_analysis.researchers or []) if r.name]
    people_terms = _normalize_hint_terms(people_terms)
    if people_terms:
        normalized_intent.people_terms = people_terms

    filters = question_analysis.filters or {}
    if isinstance(filters, dict):
        org_terms_hint = _normalize_hint_terms(filters.get("org_name") or filters.get("org"))
        if org_terms_hint:
            normalized_intent.org_terms = org_terms_hint
        people_terms_hint = _normalize_hint_terms(
            filters.get("researcher_name") or filters.get("people_name")
        )
        if people_terms_hint:
            normalized_intent.people_terms = people_terms_hint
        year_terms_hint = _normalize_hint_terms(
            [filters.get("year_from"), filters.get("year_to")]
        )
        if year_terms_hint:
            normalized_intent.years = year_terms_hint
        tag_filters_hint = _normalize_hint_terms(filters.get("tag_filters"))
        if tag_filters_hint:
            normalized_intent.tag_filters = tag_filters_hint
            if question_analysis.head == "perf":
                normalized_intent.perf_tag_filters = tag_filters_hint
            if question_analysis.head == "project":
                normalized_intent.project_tag_filters = tag_filters_hint
        org_role = filters.get("org_role")
        if org_role:
            normalized_intent.org_role = str(org_role).strip().lower() or None

# --- Graph Construction ---
def build_advanced_workflow():
    workflow = StateGraph(AgentState)

    # Add Nodes
    workflow.add_node("load_memory", node_load_memory)
    workflow.add_node("rule_precheck", node_rule_precheck)
    workflow.add_node("analyze_question", node_analyze_question)
    workflow.add_node("knowledge_sufficiency", node_knowledge_sufficiency)
    workflow.add_node("join_analysis", node_join_analysis)

    # 두 모델 각각의 Fast Answer 노드

    workflow.add_node("rag_search", node_rag_search)

    # 두 모델 각각의 Refined Answer 노드
    workflow.add_node("generate_answer_gemma", node_generate_answer_gemma)
    workflow.add_node("generate_answer_gpt", node_generate_answer_gpt)
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

    workflow.add_edge("analyze_question", "knowledge_sufficiency")
    workflow.add_edge("knowledge_sufficiency", "join_analysis")

    def route_after_join_analysis(state: AgentState):
        ks = state.knowledge_sufficiency

        if ks.requires_new_knowledge == "low" and state.prev_context:
            return ["generate_answer_gpt", "generate_answer_gemma"]

        return "rag_search"

    workflow.add_conditional_edges(
        "join_analysis",
        route_after_join_analysis,
        {
            "generate_answer_gpt": "generate_answer_gpt",
            "generate_answer_gemma": "generate_answer_gemma",
            "rag_search": "rag_search"
        }
    )


    # RAG 검색 완료 후 두 모델로 Refine
    workflow.add_edge("rag_search", "generate_answer_gemma")
    workflow.add_edge("rag_search", "generate_answer_gpt")

    # Refined Answer 완료 후 join
    workflow.add_edge("generate_answer_gemma", "join_answers")
    workflow.add_edge("generate_answer_gpt", "join_answers")

    # Refined answers join도 merge로
    workflow.add_edge("join_answers", "merge_answers")


    # Direct answer also goes to save
    workflow.add_edge("direct_answer", "save_history")
    workflow.add_edge("merge_answers", "save_history")

    # End
    workflow.add_edge("save_history", END)

    return workflow


def refine_documents_rule_based(docs: List[Document], is_detail=False) -> str:
    context_chunks: List[str] = []

    for doc in docs:
        mapped_doc = RagMapper.map(doc)

        source_idx = doc.get("source_index")
        title = mapped_doc.get("title", "제목 없음")

        refined_text = format_metadata(mapped_doc.get("meta_basic", {}))
        if is_detail:
            refined_text += format_metadata(mapped_doc.get("meta_detail", {}))

        researcher_lines = RagMapper.get_researcher_info(mapped_doc)
        researcher_block = ""
        if researcher_lines:
            researcher_block = (
                    "\n- 연구원 목록:\n"
                    + "\n".join(researcher_lines)
            )

        combined_text = f"{refined_text}{researcher_block}".strip()
        limited_text = _limit_text_by_sentences_and_tokens(
            combined_text,
            max_sentences=MAX_DOC_SENTENCES,
            max_tokens=MAX_DOC_TOKENS,
        )

        context_chunks.append(
            f"## 출처 {source_idx}. {title}\n"
            f"{limited_text}\n"
        )

    return "\n\n".join(context_chunks)


def _format_metadata_limited(metadata: Dict[str, Any], max_lines: int) -> str:
    lines = []
    for key, value in metadata.items():
        if value is None:
            continue

        if isinstance(value, list):
            value = ", ".join(map(str, value))
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)

        lines.append(f"- {key}: {value}")
        if len(lines) >= max_lines:
            break

    return "\n".join(lines) if lines else ""


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
    max_sentences: int,
    max_tokens: int,
) -> str:
    if not text:
        return ""
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


def summarize_documents_headlines(docs: List[Document]) -> str:
    context_chunks: List[str] = []
    for doc in docs:
        mapped_doc = RagMapper.map(doc)
        source_idx = doc.get("source_index")
        title = mapped_doc.get("title", "제목 없음")
        summary_text = _format_metadata_limited(
            mapped_doc.get("meta_basic", {}),
            max_lines=SUMMARY_DOC_SENTENCES,
        )
        summary_text = _limit_text_by_sentences_and_tokens(
            summary_text,
            max_sentences=SUMMARY_DOC_SENTENCES,
            max_tokens=SUMMARY_DOC_TOKENS,
        )
        context_chunks.append(f"## 출처 {source_idx}. {title}\n{summary_text}\n")
    return "\n\n".join(context_chunks)


def _truncate_context_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...\n(컨텍스트가 너무 길어 일부가 잘렸습니다.)"


def format_metadata(metadata: Dict[str, Any]) -> str:
    """metadata dict → bullet list 텍스트 변환"""
    lines = []

    for key, value in metadata.items():
        if value is None:
            continue

        if isinstance(value, list):
            value = ", ".join(map(str, value))
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)

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
    global redis_client

    # RAG 초기화
    build_rag_objects()

    # Redis 연결
    try:
        redis_client = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        await redis_client.ping()
        logger.info("✅ Redis connected: %s", REDIS_URL)
    except Exception as e:
        redis_client = None
        logger.error("❌ Redis connection failed: %s", e, exc_info=True)

    try:
        workflow = build_advanced_workflow().compile()
        app.state.graph = workflow
        logger.info("✅ Advanced Dual-Model Pipeline compiled successfully")
        yield
    except Exception as e:
        logger.error(f"❌ Startup Error: {e}")
        raise
    finally:
        if redis_client:
            await redis_client.close()

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

    inputs = {
        "conversation_id": conversation_id,
        "messages": [HumanMessage(content=question)]
    }

    graph = app.state.graph

    async def event_generator():
        yield f"data: {json.dumps({'conversationId': conversation_id})}\n\n"

        loaded_history, prev_context = await load_conversation_memory(conversation_id)
        chat_history = loaded_history + [HumanMessage(content=question)]
        intent_payload = await build_intent_payload(
            question,
            conversation_id,
            chat_history,
            prev_context,
        )
        inputs = {
            "conversation_id": conversation_id,
            "messages": [HumanMessage(content=question)],
            "intent_payload": intent_payload,
            "question_analysis": intent_payload.get("question_analysis"),
        }

        log_section("REQUEST START", f"ID: {conversation_id}\nQ: {question}")

        documents_used = []

        try:
            async for event in graph.astream_events(inputs, version="v2"):
                kind = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")
                data = event.get("data", {})
                # Answer 스트리밍 - GPT
                if kind == "on_chat_model_stream" and node == "generate_answer_gpt":
                    chunk = data.get("chunk")
                    if hasattr(chunk, "content") and chunk.content:
                        yield f"data: {json.dumps({'model' : 'GPT', 'content': chunk.content}, ensure_ascii=False)}\n\n"

                # Answer 스트리밍 - Gemma
                elif kind == "on_chat_model_stream" and node == "generate_answer_gemma":
                    chunk = data.get("chunk")
                    if hasattr(chunk, "content") and chunk.content:
                        yield f"data: {json.dumps({'model' : 'GEMMA', 'content': chunk.content}, ensure_ascii=False)}\n\n"

                # Direct Answer (rule-based)
                elif kind == "on_chain_end" and node == "direct_answer":
                    output = data.get("output", {})
                    if "answer_gemma" in output:
                        answer = output["answer_gemma"]
                        yield f"data: {json.dumps({'model' : 'GPT', 'content': answer}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'model' : 'GEMMA', 'content': answer}, ensure_ascii=False)}\n\n"

                elif kind == "on_chain_start" and node == "rag_search":
                    yield f"data: {json.dumps({'status': 'retrieve'}, ensure_ascii=False)}\n\n"

                elif kind == "on_chain_end" and node == "merge_answers":
                    docs = data.get("output", {}).get("context", [])
                    documents_used.extend(docs)

            ref_docs = []

            for d in documents_used:
                ref_docs.append(RagMapper.get_references(d))

            log_section("REF PUSH", f"coq: {conversation_id}{question}\n{json.dumps(ref_docs, ensure_ascii=False, indent=2)}")

            yield f"data: {json.dumps({'reference': ref_docs}, ensure_ascii=False)}\n\n"

            # 루프 종료 후
            yield f"data: {json.dumps({'status': 'done'})}\n\n"

        except Exception as e:
            logger.error(f"Stream Error: {e}", exc_info=True)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

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

    loaded_history, prev_context = await load_conversation_memory(conversation_id)
    chat_history = loaded_history + [HumanMessage(content=question)]
    intent_payload = await build_intent_payload(
        question,
        conversation_id,
        chat_history,
        prev_context,
    )

    inputs = {
        "conversation_id": conversation_id,
        "messages": [HumanMessage(content=question)],
        "intent_payload": intent_payload,
        "question_analysis": intent_payload.get("question_analysis"),
    }

    graph = app.state.graph

    try:
        final_state = await graph.ainvoke(inputs)

        question_analysis = final_state.get("question_analysis")
        knowledge_sufficiency = final_state.get("knowledge_sufficiency")

        return {
            "success": True,
            "conversation_id": conversation_id,
            "answer_gemma": final_state.get("answer_gemma"),
            "answer_gpt": final_state.get("answer_gpt"),
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
        return {"success": False, "error": str(e)}

@app.get("/health")
async def health_check():
    """헬스 체크 엔드포인트"""
    redis_ok = False
    if redis_client:
        try:
            await redis_client.ping()
            redis_ok = True
        except Exception:
            redis_ok = False

    return {
        "status": "healthy",
        "redis": "connected" if redis_ok else "disconnected",
        "graph": "compiled" if hasattr(app.state, "graph") else "not_ready"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007, access_log=False)
