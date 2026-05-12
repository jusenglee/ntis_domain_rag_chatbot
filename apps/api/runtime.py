"""FastAPI 애플리케이션의 런타임 부트스트랩 및 종료 헬퍼 모듈.

이 모듈은 서버 시작 시 필요한 리소스(Redis, KV 스토어, RAG 엔진, 워크플로우 등)를 초기화하고,
서버 종료 시 안전하게 리소스를 해제하는 역할을 수행합니다.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
import inspect
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from fastapi import FastAPI

from apps.platform.storage import FileKVStore, KVStore


@dataclass(frozen=True)
class AppRuntimeConfig:
    """
    애플리케이션 구동에 필요한 설정 값들을 담는 데이터 클래스.
    Composition Root(app_factory)에서 생성되어 주입됩니다.
    """
    redis_url: str                # Redis 서버 주소
    file_kv_root: str             # 로컬 파일 KV 스토어 루트 경로
    planner_stagewise_enabled: bool # 플래너 단계별 실행 활성화 여부
    planner_stage1_prompt_version: str # 단계 1 프롬프트 버전
    planner_stage15_prompt_version: str # 단계 1.5 프롬프트 버전
    planner_stage2_prompt_version: str # 단계 2 프롬프트 버전
    ensure_payload_index_on_boot: bool # 시작 시 Qdrant 페이로드 인덱스 생성 여부
    sparse_warmup_on_boot: bool   # 시작 시 Sparse Encoder 워밍업 여부
    metrics_timeout_seconds: float # 메트릭 수집 타임아웃
    payload_keyword_index_targets: dict[str, list[str]] # 키워드 인덱스 대상 필드
    payload_optional_keyword_index_targets: dict[str, list[str]] # 선택적 키워드 인덱스 대상
    payload_text_index_targets: dict[str, list[str]] # 텍스트 인덱스 대상
    payload_integer_index_targets: dict[str, list[str]] # 정수 인덱스 대상
    payload_datetime_index_targets: dict[str, list[str]] # 날짜 인덱스 대상


class RedisKVStore(KVStore):
    """
    공통 KVStore 인터페이스를 만족하는 Redis 어댑터 클래스.
    대화 세션 상태를 Redis에 저장하기 위해 사용합니다.
    """
    def __init__(self, client: Any):
        self.client = client

    async def get(self, key: str) -> Optional[str]:
        """키에 해당하는 값을 조회합니다."""
        return await self.client.get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        """키-값 쌍을 저장하며, 선택적으로 만료 시간(초)을 설정할 수 있습니다."""
        await self.client.set(key, value, ex=ex)

    async def delete(self, key: str) -> None:
        """특정 키를 삭제합니다."""
        await self.client.delete(key)

    async def ping(self) -> bool:
        """Redis 서버 연결 상태를 확인합니다."""
        try:
            await self.client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Redis 클라이언트 연결을 닫습니다."""
        await self.client.close()


async def _close_async_resource(resource: Any) -> None:
    """
    리소스의 `aclose` 또는 `close` 메서드를 안전하게 호출하는 비동기 헬퍼.
    동기/비동기 클로징 메서드 모두에 대응합니다.
    """
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
    logger_obj: Any,
    redis_from_url: Callable[..., Any],
    file_store_factory: Callable[[str], KVStore] = FileKVStore,
) -> Optional[KVStore]:
    """
    Redis 연결을 우선 시도하되, 실패 시 로컬 파일 기반 KV 스토어로 자동 전환합니다.
    운영 환경의 안정성을 보장하기 위한 Fallback 로직입니다.
    """
    redis_client: Any = None
    try:
        # 1. Redis 연결 시도
        redis_client = redis_from_url(redis_url, encoding="utf-8", decode_responses=True)
        await redis_client.ping()
        logger_obj.info("Redis connected: {}", redis_url)
        return RedisKVStore(redis_client)
    except Exception as exc:
        # Redis 실패 시 정리 및 로깅
        try:
            await _close_async_resource(redis_client)
        except Exception:
            logger_obj.warning("Redis cleanup after failed startup connect also failed", exc_info=True)
        logger_obj.error("Redis connection failed: {}", exc, exc_info=True)

    # 2. 파일 스토리지 Fallback 시도
    fallback_root = str(Path(file_kv_root or "local_kvstore").resolve())
    try:
        file_store = file_store_factory(fallback_root)
        await file_store.ping()
        logger_obj.warning("KV backend falling back to file store: {}", fallback_root)
        logger_obj.info("File KV backend ready: {}", fallback_root)
        return file_store
    except Exception as fallback_exc:
        logger_obj.error("File KV fallback failed: {}", fallback_exc, exc_info=True)
        return None


async def _file_kv_sweeper_loop(
    *,
    get_kv_store: Callable[[], Any],
    interval_seconds: float,
    logger_obj: Any,
) -> None:
    """FileKVStore(파일 기반 키-값 저장소)의 만료 파일을 주기적으로 일괄 삭제한다.

    lazy delete(읽을 때만 정리)만으로는 디스크가 무한 증가하므로 active eviction(능동 만료 제거)을 한다.
    백엔드가 redis로 절체된 후에는 sweep 호출이 의미가 없으므로 즉시 종료한다.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        current = get_kv_store()
        sweep_fn = getattr(current, "sweep_expired", None)
        if not callable(sweep_fn):
            # 백엔드가 file에서 다른 종류로 절체된 경우 sweeper도 종료
            return
        try:
            removed = await sweep_fn()
        except Exception as exc:
            try:
                logger_obj.warning("[file_kv] sweeper error: {}", exc)
            except Exception:
                pass
            continue
        if removed:
            try:
                logger_obj.info("[file_kv] swept {} expired entries", int(removed))
            except Exception:
                pass


async def _redis_reconnect_loop(
    app: FastAPI,
    *,
    redis_url: str,
    redis_from_url: Callable[..., Any],
    logger_obj: Any,
    interval_seconds: float,
) -> None:
    """파일 KV(파일 기반 키-값 저장소)로 절체된 상태에서 주기적으로 Redis ping(연결 확인) 재시도.

    Redis가 복구되면 app.state.kv_store를 Redis 어댑터로 교체한다.
    이미 Redis 모드이거나 KV가 없는 경우 즉시 종료.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return
        current = getattr(app.state, "kv_store", None)
        if current is None or isinstance(current, RedisKVStore):
            return
        client: Any = None
        try:
            client = redis_from_url(redis_url, encoding="utf-8", decode_responses=True)
            await client.ping()
        except Exception:
            try:
                await _close_async_resource(client)
            except Exception:
                pass
            continue
        new_store = RedisKVStore(client)
        old_store = current
        app.state.kv_store = new_store
        # file KV sweeper가 돌고 있으면 절체 후 의미가 없으므로 정리
        sweeper_task = getattr(app.state, "file_kv_sweeper_task", None)
        if sweeper_task is not None:
            sweeper_task.cancel()
            try:
                await sweeper_task
            except Exception:
                pass
            app.state.file_kv_sweeper_task = None
        try:
            logger_obj.info(
                "Redis reconnected; KV backend switched from file to redis: {}", redis_url
            )
        except Exception:
            pass
        try:
            await _close_async_resource(old_store)
        except Exception:
            pass
        return


async def initialize_app_runtime(app: FastAPI, *, config: AppRuntimeConfig) -> None:
    """
    서버 구동에 필요한 모든 런타임 리소스를 초기화합니다.
    - 환경 검증
    - RAG 엔진 및 인덱스 정합성 확인
    - KV 스토어(Redis/File) 설정
    - 워크플로우(LangGraph) 컴파일
    """
    from apps.api.contracts.runtime_contracts import validate_project_key_env_contract
    from apps.api.runtime_helpers import log_event, logger
    from apps.api.workflow_builder import build_request_workflow
    from apps.retrieval.rag_store import build_rag_objects
    from apps.retrieval.retrieval import (
        ensure_datetime_index,
        ensure_integer_index,
        ensure_keyword_index,
        ensure_text_index,
        warmup_sparse_encoder,
    )
    import redis.asyncio as redis

    # 기본 환경 설정 계약 검증
    validate_project_key_env_contract(log_info=logger.info)
    
    # RAG 관련 리소스(Qdrant 등) 빌드
    rag_resources = build_rag_objects()

    # 운영 로그: 시스템 부팅 시점의 코드 지문 및 구성 정보 기록
    log_event("CODE.FINGERPRINT", stage="startup")
    log_event(
        "APP.CONFIG",
        stage="startup",
        planner_stagewise_enabled=int(config.planner_stagewise_enabled),
        planner_stage1_prompt_version=config.planner_stage1_prompt_version,
        planner_stage15_prompt_version=config.planner_stage15_prompt_version,
        planner_stage2_prompt_version=config.planner_stage2_prompt_version,
    )

    # Qdrant 페이로드 인덱스 자동 생성 로직
    if config.ensure_payload_index_on_boot:
        client = rag_resources.qdrant_client
        payload_schema_cache: dict[str, dict[str, Any]] = {}
        payload_probe_cache: dict[str, list[Any]] = {}

        def _payload_schema_type_name(collection_name: str, field_name: str) -> str:
            """특정 컬렉션 필드의 인덱스 스키마 타입을 확인합니다."""
            payload_schema = payload_schema_cache.get(collection_name)
            if payload_schema is None:
                collection_info = client.get_collection(collection_name=collection_name)
                payload_schema = getattr(collection_info, "payload_schema", None) or {}
                payload_schema_cache[collection_name] = payload_schema
            field_schema = payload_schema.get(field_name)
            schema_type = getattr(field_schema, "data_type", None)
            return str(schema_type).upper() if schema_type is not None else ""

        def _payload_has_non_empty_field(payload: Any, field_name: str) -> bool:
            """페이로드 내에 특정 필드가 존재하고 비어있지 않은지 재귀적으로 확인합니다."""
            normalized = field_name.replace("[]", "")
            parts = [part for part in normalized.split(".") if part]

            def _walk(value: Any, remaining: list[str]) -> bool:
                if not remaining:
                    if value is None: return False
                    if isinstance(value, str): return bool(value.strip())
                    if isinstance(value, (list, dict)): return bool(value)
                    return True
                if isinstance(value, list):
                    return any(_walk(item, remaining) for item in value)
                if not isinstance(value, dict): return False
                head, *tail = remaining
                if head not in value: return False
                return _walk(value.get(head), tail)

            return _walk(payload, parts)

        def _payload_field_exists_in_collection(collection_name: str, field_name: str) -> bool:
            """실제 데이터 샘플(Scroll)을 조회하여 해당 필드가 데이터로 존재하는지 확인합니다."""
            points = payload_probe_cache.get(collection_name)
            if points is None:
                try:
                    scroll_result = client.scroll(collection_name=collection_name, limit=64, with_payload=True)
                except Exception: return False
                points = scroll_result[0] if isinstance(scroll_result, tuple) else (getattr(scroll_result, "points", None) or [])
                payload_probe_cache[collection_name] = list(points)

            for point in points:
                p = getattr(point, "payload", None) or (point.get("payload") if isinstance(point, dict) else None)
                if isinstance(p, dict) and _payload_has_non_empty_field(p, field_name):
                    return True
            return False

        def _ensure_payload_indexes(
            *,
            label: str,
            targets: dict[str, list[str]],
            ensure_fn: Any,
            expected_schema_type: str,
            require_payload_probe: bool = False,
        ) -> None:
            """설정된 타겟 필드들에 대해 필요한 인덱스를 Qdrant에 생성합니다."""
            for collection_name, field_names in targets.items():
                for field_name in field_names:
                    try:
                        schema_type_name = _payload_schema_type_name(collection_name, field_name)
                        if expected_schema_type in schema_type_name:
                            continue # 이미 존재함

                        if require_payload_probe and not _payload_field_exists_in_collection(collection_name, field_name):
                            continue # 데이터가 없어 생략

                        ensure_fn(client, collection_name, field_name)
                        logger.info("[startup][payload-index][{}] {}.{}: ensure called", label, collection_name, field_name)
                    except Exception as exc:
                        logger.warning("[startup][payload-index][{}] {}.{}: warning ({})", label, collection_name, field_name, exc)

        # 각 타입별 인덱스 생성 실행
        _ensure_payload_indexes(label="keyword", targets=config.payload_keyword_index_targets, ensure_fn=ensure_keyword_index, expected_schema_type="KEYWORD")
        _ensure_payload_indexes(label="keyword-optional", targets=config.payload_optional_keyword_index_targets, ensure_fn=ensure_keyword_index, expected_schema_type="KEYWORD", require_payload_probe=True)
        _ensure_payload_indexes(label="text", targets=config.payload_text_index_targets, ensure_fn=ensure_text_index, expected_schema_type="TEXT")
        _ensure_payload_indexes(label="integer", targets=config.payload_integer_index_targets, ensure_fn=ensure_integer_index, expected_schema_type="INTEGER")
        _ensure_payload_indexes(label="datetime", targets=config.payload_datetime_index_targets, ensure_fn=ensure_datetime_index, expected_schema_type="DATETIME")
    else:
        logger.info("[startup][payload-index] skipped by config")

    # Sparse Encoder 워밍업 (FastEmbed)
    if config.sparse_warmup_on_boot:
        warmup_sparse_encoder()
    else:
        logger.info("[startup][fastembed] skipped by config")

    # KV 스토어 초기화 (Redis or File)
    app.state.kv_store = await _initialize_kv_store_with_fallback(
        redis_url=config.redis_url,
        file_kv_root=config.file_kv_root,
        logger_obj=logger,
        redis_from_url=redis.from_url,
    )

    # Redis 재연결 백그라운드: file KV로 절체된 경우만 시작. interval=0 이면 비활성.
    reconnect_interval = float(os.getenv("REDIS_RECONNECT_INTERVAL_SECONDS", "30") or 0)
    if (
        reconnect_interval > 0
        and app.state.kv_store is not None
        and not isinstance(app.state.kv_store, RedisKVStore)
    ):
        app.state.redis_reconnect_task = asyncio.create_task(
            _redis_reconnect_loop(
                app,
                redis_url=config.redis_url,
                redis_from_url=redis.from_url,
                logger_obj=logger,
                interval_seconds=reconnect_interval,
            )
        )
    else:
        app.state.redis_reconnect_task = None

    # FileKVStore sweeper: file 백엔드에서만 의미 있음. FILE_KV_SWEEP_INTERVAL_SECONDS=0 이면 비활성.
    sweep_interval = float(os.getenv("FILE_KV_SWEEP_INTERVAL_SECONDS", "300") or 0)
    if (
        sweep_interval > 0
        and isinstance(app.state.kv_store, FileKVStore)
    ):
        app.state.file_kv_sweeper_task = asyncio.create_task(
            _file_kv_sweeper_loop(
                get_kv_store=lambda: getattr(app.state, "kv_store", None),
                interval_seconds=sweep_interval,
                logger_obj=logger,
            )
        )
    else:
        app.state.file_kv_sweeper_task = None

    # 메트릭용 HTTP 클라이언트 및 워크플로우 그래프 빌드
    metrics_timeout = httpx.Timeout(config.metrics_timeout_seconds)
    app.state.metrics_http = httpx.AsyncClient(timeout=metrics_timeout)
    app.state.graph = build_request_workflow().compile()
    logger.info("Advanced Dual-Model Pipeline compiled successfully")


async def shutdown_app_runtime(app: FastAPI) -> None:
    """
    서버 종료 시 런타임 리소스를 안전하게 해제합니다.
    초기화의 역순으로 리소스를 닫습니다.
    """
    from apps.api.runtime_helpers import logger
    from apps.chat.llm_runtime import get_llm_cache

    # Redis 재연결 백그라운드 task 정리
    reconnect_task = getattr(app.state, "redis_reconnect_task", None)
    if reconnect_task is not None:
        reconnect_task.cancel()
        try:
            await reconnect_task
        except Exception:
            pass

    # FileKVStore sweeper task 정리
    sweeper_task = getattr(app.state, "file_kv_sweeper_task", None)
    if sweeper_task is not None:
        sweeper_task.cancel()
        try:
            await sweeper_task
        except Exception:
            pass

    # 메트릭 HTTP 클라이언트 종료
    metrics_http = getattr(app.state, "metrics_http", None)
    if metrics_http is not None:
        await metrics_http.aclose()

    # KV 스토어 종료
    kv_store = getattr(app.state, "kv_store", None)
    if kv_store is not None:
        await kv_store.close()

    # LLM 클라이언트(캐시된 인스턴스) 종료
    llm_cache = get_llm_cache()
    for llm in llm_cache.values():
        close_fn = getattr(llm, "aclose", None)
        if close_fn is None: continue
        try:
            await close_fn()
        except Exception as exc:
            logger.warning("[shutdown] llm close failed: model={} error={}", getattr(llm, "model_name", "unknown"), exc)
    llm_cache.clear()
