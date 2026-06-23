"""FastAPI 런타임 부트스트랩 (3계층 권한분리 파이프라인 전용).

ADR-0018 적용 후 슬림 버전. 다음 책임만 갖는다:
    - KV 스토어 초기화 (Redis 우선, 실패 시 파일 KV로 폴백)
    - Qdrant + 임베딩 리소스 빌드 (apps.retrieval.rag_store)
    - LLM 어댑터 빌드 (Solar/Gemma)
    - PipelineDeps 구성 및 LangGraph 컴파일

레거시 제거:
    - planner_stagewise 관련 config 전부 폐기
    - Qdrant 부팅 시 payload 인덱스 자동 생성 비활성 (운영 절차로 분리)
    - sparse encoder warmup 비활성 (필요 시 재도입)
    - apps.api.workflow_builder import 제거
"""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from fastapi import FastAPI
from loguru import logger

from apps.chat.llm_runtime import build_llm
from apps.pipeline.agent_workflow import AgentPipelineDeps
from apps.pipeline.agents import (
    AnswerAgent,
    CriticAgent,
    DialogueAgent,
    EntityResolverAgent,
)
from apps.pipeline.agents.grounding.llm_judge import LLMJudgeChecker
from apps.pipeline.search_agent import SearchAgent
from apps.platform.storage import FileKVStore, KVStore


@dataclass(frozen=True)
class AppRuntimeConfig:
    """애플리케이션 런타임 설정 (3계층 파이프라인 전용)."""

    redis_url: str
    file_kv_root: str
    metrics_timeout_seconds: float


class RedisKVStore(KVStore):
    """공통 KVStore 인터페이스를 만족하는 Redis 어댑터."""

    def __init__(self, client: Any) -> None:
        self.client = client

    async def get(self, key: str) -> Optional[str]:
        return await self.client.get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        await self.client.set(key, value, ex=ex)

    async def delete(self, key: str) -> None:
        await self.client.delete(key)

    async def ping(self) -> bool:
        try:
            await self.client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self.client.close()


async def _close_async_resource(resource: Any) -> None:
    """리소스의 aclose/close를 안전하게 호출."""
    if resource is None:
        return
    close_fn = getattr(resource, "aclose", None) or getattr(resource, "close", None)
    if close_fn is None:
        return
    result = close_fn()
    if inspect.isawaitable(result):
        await result


async def _initialize_kv_store_with_fallback(
    *,
    redis_url: str,
    file_kv_root: str,
    redis_from_url: Callable[..., Any],
    file_store_factory: Callable[[str], KVStore] = FileKVStore,
) -> Optional[KVStore]:
    """Redis 우선, 실패 시 파일 KV로 폴백."""
    redis_client: Any = None
    try:
        redis_client = redis_from_url(redis_url, encoding="utf-8", decode_responses=True)
        await redis_client.ping()
        logger.info("Redis connected: {}", redis_url)
        return RedisKVStore(redis_client)
    except Exception as exc:
        try:
            await _close_async_resource(redis_client)
        except Exception:
            pass
        logger.warning("Redis connection failed: {} (falling back to file KV)", exc)

    fallback_root = str(Path(file_kv_root or "local_kvstore").resolve())
    try:
        file_store = file_store_factory(fallback_root)
        await file_store.ping()
        logger.info("File KV backend ready: {}", fallback_root)
        return file_store
    except Exception as exc:
        logger.error("File KV fallback failed: {}", exc)
        return None


async def initialize_app_runtime(app: FastAPI, *, config: AppRuntimeConfig) -> None:
    """런타임 리소스 초기화 및 LangGraph 컴파일."""
    # 1) Qdrant + 임베딩 리소스 빌드
    from apps.retrieval.rag_store import build_rag_objects

    rag_resources = build_rag_objects()
    app.state.qdrant_client = rag_resources.qdrant_client
    app.state.embed_e5i = rag_resources.embed_e5i
    app.state.embed_e5 = rag_resources.embed_e5

    # 2) LLM 어댑터 빌드.
    #    Solar(solar_vllm_0) = DialogueAgent 분류 + Planner/Critic 판정 + **메인 답변(패널 A)**.
    #    Gemma(gemma_triton_0) = 비교 답변(패널 B) + 도구(response.*)용.
    dialogue_llm = build_llm(model_name="solar_vllm_0")
    answer_llm = build_llm(model_name="gemma_triton_0")
    app.state.dialogue_llm = dialogue_llm
    app.state.answer_llm = answer_llm

    # 3) 7-agent 컴포넌트 구성 (DialogueAgent → EntityResolver → SearchPlanner
    #    → RetrievalAgent → EvidenceCurator → AnswerAgent → CriticAgent).
    search_agent = SearchAgent(
        qdrant_client=rag_resources.qdrant_client,
        embed_e5i=rag_resources.embed_e5i,
        embed_e5=rag_resources.embed_e5,
    )
    # CriticAgent grounding 검증 — 2026-05-26 기본 활성화. LLM-as-Judge로 답변과
    # evidence 의미 일치 판정. 회귀 시 환경변수 RAG_GROUNDING_CHECKER_ENABLED=false 로 우회.
    grounding_enabled = (os.environ.get("RAG_GROUNDING_CHECKER_ENABLED", "true").strip().lower()
                         not in {"0", "false", "no", "off"})
    grounding_checker = LLMJudgeChecker(llm=dialogue_llm) if grounding_enabled else None
    logger.info(
        f"CriticAgent grounding_checker={'enabled (LLMJudgeChecker on dialogue_llm)' if grounding_enabled else 'disabled (env override)'}"
    )

    # ADR-0020: 단일 Agentic 파이프라인. ADR-0023: AdequacyGate 제거 — 충분성 판단은 Planner 단일 권한.
    from apps.pipeline.agents.planner_agent import PlannerAgent
    from apps.pipeline.tools.contracts import ToolContext, ToolExecutor as _ToolExecutor
    from apps.pipeline.tools.registry import build_default_registry

    tool_ctx = ToolContext(
        qdrant_client=rag_resources.qdrant_client,
        embed_e5i=rag_resources.embed_e5i,
        embed_e5=rag_resources.embed_e5,
        dialogue_llm=dialogue_llm,
        answer_llm=answer_llm,
        search_agent=search_agent,
    )
    tool_executor = _ToolExecutor(
        registry=build_default_registry(), context=tool_ctx,
    )
    planner_agent = PlannerAgent(llm=dialogue_llm)
    logger.info(
        f"Agentic mode ENABLED — PlannerAgent + ToolExecutor with "
        f"{len(tool_executor.specs())} tools (충분성 판단=Planner 단일 권한, AdequacyGate 폐기)"
    )

    # 이중 모델 답변(2026-06-01 사용자 확정): A=Solar(메인) / B=Gemma(비교).
    # 메인 답변(Solar)이 CriticAgent 검증·repair·references·세션 발행의 기준이 된다.
    # 보조 답변(Gemma)은 패널 B로 동시 스트리밍되며 검증 없이 원문 비교용으로만 노출.
    # 회귀 시 RAG_DUAL_ANSWER_ENABLED=false 로 단일 모델(메인 Solar) 답변으로 롤백.
    dual_answer_enabled = os.environ.get("RAG_DUAL_ANSWER_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off",
    }
    primary_answer_agent = AnswerAgent(llm=dialogue_llm)  # 메인 = Solar (패널 A)
    secondary_answer_agent = (
        AnswerAgent(llm=answer_llm) if dual_answer_enabled else None  # 비교 = Gemma (패널 B)
    )
    logger.info(
        "Dual answer {} — primary=Solar(solar_vllm_0, 패널 A){}".format(
            "ENABLED" if dual_answer_enabled else "disabled",
            " + secondary=Gemma(gemma_triton_0, 패널 B)" if dual_answer_enabled else " (단일 모델)",
        )
    )

    deps = AgentPipelineDeps(
        dialogue_agent=DialogueAgent(llm=dialogue_llm),
        entity_resolver=EntityResolverAgent(qdrant_client=rag_resources.qdrant_client),
        answer_agent=primary_answer_agent,
        critic_agent=CriticAgent(grounding_checker=grounding_checker),
        answer_agent_secondary=secondary_answer_agent,
        planner_agent=planner_agent,
        tool_executor=tool_executor,
    )
    app.state.pipeline_deps = deps

    # 4) KV 스토어 초기화
    import redis.asyncio as redis

    app.state.kv_store = await _initialize_kv_store_with_fallback(
        redis_url=config.redis_url,
        file_kv_root=config.file_kv_root,
        redis_from_url=redis.from_url,
    )

    # 5) 메트릭용 HTTP 클라이언트 (관측 외부 시스템 연동에 사용; 기본은 사용 안 함)
    app.state.metrics_http = httpx.AsyncClient(timeout=httpx.Timeout(config.metrics_timeout_seconds))

    # 6) LangGraph 컴파일 — 단일 Agentic 파이프라인 (ADR-0020).
    from apps.pipeline.agentic_workflow import build_agentic_pipeline_graph
    app.state.graph = build_agentic_pipeline_graph(deps)
    logger.info("Agentic pipeline (cyclic LangGraph) compiled successfully")


async def shutdown_app_runtime(app: FastAPI) -> None:
    """리소스 해제 (초기화 역순)."""
    # 메트릭 클라이언트
    metrics_http = getattr(app.state, "metrics_http", None)
    if metrics_http is not None:
        try:
            await metrics_http.aclose()
        except Exception:
            pass

    # KV 스토어
    kv_store = getattr(app.state, "kv_store", None)
    if kv_store is not None:
        try:
            await kv_store.close()
        except Exception:
            pass

    # LLM 어댑터
    for attr in ("dialogue_llm", "answer_llm"):
        llm = getattr(app.state, attr, None)
        if llm is None:
            continue
        aclose = getattr(llm, "aclose", None)
        if aclose is None:
            continue
        try:
            await aclose()
        except Exception as exc:
            logger.warning("[shutdown] llm aclose failed: {} err={}", attr, exc)
