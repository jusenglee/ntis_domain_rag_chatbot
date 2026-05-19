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

import asyncio
import inspect
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from fastapi import FastAPI
from loguru import logger

from apps.chat.llm_runtime import build_llm
from apps.pipeline.agent_workflow import AgentPipelineDeps, build_agent_pipeline_graph
from apps.pipeline.agents import (
    AnswerAgent,
    CriticAgent,
    DialogueAgent,
    EntityResolverAgent,
    EvidenceCuratorAgent,
    RetrievalAgent,
    SearchPlannerAgent,
)
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

    # 2) LLM 어댑터 빌드 (Solar = DialogueAgent 분류, Gemma = AnswerAgent 답변)
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
    deps = AgentPipelineDeps(
        dialogue_agent=DialogueAgent(llm=dialogue_llm),
        entity_resolver=EntityResolverAgent(),
        search_planner=SearchPlannerAgent(),
        retrieval_agent=RetrievalAgent(
            qdrant_client=rag_resources.qdrant_client,
            embed_e5i=rag_resources.embed_e5i,
            embed_e5=rag_resources.embed_e5,
            task_executor=search_agent,
        ),
        evidence_curator=EvidenceCuratorAgent(),
        answer_agent=AnswerAgent(llm=answer_llm),
        critic_agent=CriticAgent(),
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

    # 6) LangGraph 컴파일
    app.state.graph = build_agent_pipeline_graph(deps)
    logger.info("7-agent pipeline compiled successfully")


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
