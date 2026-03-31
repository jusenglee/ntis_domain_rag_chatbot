"""Runtime bootstrap and shutdown helpers for the FastAPI app.\n\nThis module centralizes startup side effects such as Redis connection, metrics client setup,\npayload index guarantees, and graph compilation so route code stays request-focused.\n"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from fastapi import FastAPI
import redis.asyncio as redis

from apps.core.storage import KVStore


@dataclass(frozen=True)
class AppRuntimeConfig:
    # This dataclass is the single startup contract passed from the composition root.
    """앱 부트스트랩에 필요한 startup 계약을 묶는 dataclass다.
    redis, planner prompt version, payload index warmup, metrics timeout 같은 시작 설정을 composition root에서 하나로 주입하게 한다.
    """
    redis_url: str
    planner_stagewise_enabled: bool
    planner_stage1_prompt_version: str
    planner_stage15_prompt_version: str
    planner_stage2_prompt_version: str
    ensure_payload_index_on_boot: bool
    sparse_warmup_on_boot: bool
    metrics_timeout_seconds: float
    payload_keyword_index_targets: dict[str, list[str]]
    payload_optional_keyword_index_targets: dict[str, list[str]]
    payload_text_index_targets: dict[str, list[str]]
    payload_integer_index_targets: dict[str, list[str]]
    payload_datetime_index_targets: dict[str, list[str]]


class RedisKVStore(KVStore):
    """Redis client를 저장소 KVStore 계약으로 감싼 어댑터다.
    app은 대화 메모리를 KVStore 추상화로 다루므로, Redis 전용 API 차이를 이 클래스에서 흡수한다.
    """

    def __init__(self, client: Any):
        """Redis client 인스턴스를 내부 상태에 보관한다.
        KVStore 계약은 부가 설정 없이 client 핸들만 있으면 출발할 수 있게 유지한다.
        """
        self.client = client

    async def get(self, key: str) -> Optional[str]:
        """Redis에서 문자열 값을 읽어 KVStore 계약으로 돌려준다.
        conversation memory 로드가 저장소 종류를 모르더라도 같은 get 의미로 호출할 수 있게 한다.
        """
        return await self.client.get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> None:
        """Redis에 문자열 값을 저장하고 필요하면 TTL을 적용한다.
        memory save 경로가 저장소 구현 상세를 모르고도 만료 정책까지 함께 주입할 수 있게 한다.
        """
        await self.client.set(key, value, ex=ex)

    async def delete(self, key: str) -> None:
        """Redis에서 키를 삭제한다."""
        await self.client.delete(key)

    async def ping(self) -> bool:
        """Redis 연결 상태를 건강 체크 형태로 확인한다.
        예외를 삼켜 boolean으로 바꿔 startup/shutdown 점검이 저장소 오류로 연쇄 실패하지 않게 한다.
        """
        try:
            await self.client.ping()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Redis client 연결을 정리한다.
        app shutdown 시 socket/resource leak를 남기지 않는 종료 후처리다.
        """
        await self.client.close()


async def initialize_app_runtime(
    app: FastAPI,
    *,
    config: AppRuntimeConfig,
    logger: logging.Logger,
    log_event: Any,
    validate_project_key_env_contract: Any,
    build_rag_objects: Any,
    ensure_keyword_index: Any,
    ensure_text_index: Any,
    ensure_integer_index: Any,
    ensure_datetime_index: Any,
    warmup_sparse_encoder: Any,
    build_workflow: Any,
) -> None:

    """FastAPI app의 startup 단계에서 RAG runtime 자원을 초기화한다.
    payload index 확보, sparse warmup, Redis 연결, metrics client 생성, workflow compile까지 요청 처리 전 선행 작업을 한 곳에서 완결한다.
    """
    validate_project_key_env_contract()
    rag_resources = build_rag_objects()

    log_event("CODE.FINGERPRINT", stage="startup")
    log_event(
        "APP.CONFIG",
        stage="startup",
        planner_stagewise_enabled=int(config.planner_stagewise_enabled),
        planner_stage1_prompt_version=config.planner_stage1_prompt_version,
        planner_stage15_prompt_version=config.planner_stage15_prompt_version,
        planner_stage2_prompt_version=config.planner_stage2_prompt_version,
    )

    if config.ensure_payload_index_on_boot:
        client = rag_resources.qdrant_client
        payload_schema_cache: dict[str, dict[str, Any]] = {}
        payload_probe_cache: dict[str, list[Any]] = {}

        def _payload_schema_type_name(collection_name: str, field_name: str) -> str:
            payload_schema = payload_schema_cache.get(collection_name)
            if payload_schema is None:
                collection_info = client.get_collection(collection_name=collection_name)
                payload_schema = getattr(collection_info, "payload_schema", None) or {}
                payload_schema_cache[collection_name] = payload_schema
            field_schema = payload_schema.get(field_name)
            schema_type = getattr(field_schema, "data_type", None)
            return str(schema_type).upper() if schema_type is not None else ""

        def _payload_has_non_empty_field(payload: Any, field_name: str) -> bool:
            normalized = field_name.replace("[]", "")
            parts = [part for part in normalized.split(".") if part]

            def _walk(value: Any, remaining: list[str]) -> bool:
                if not remaining:
                    if value is None:
                        return False
                    if isinstance(value, str):
                        return bool(value.strip())
                    if isinstance(value, (list, dict)):
                        return bool(value)
                    return True
                if isinstance(value, list):
                    return any(_walk(item, remaining) for item in value)
                if not isinstance(value, dict):
                    return False
                head, *tail = remaining
                if head not in value:
                    return False
                return _walk(value.get(head), tail)

            return _walk(payload, parts)

        def _payload_field_exists_in_collection(collection_name: str, field_name: str) -> bool:
            points = payload_probe_cache.get(collection_name)
            if points is None:
                scroll_kwargs = {
                    "collection_name": collection_name,
                    "limit": 64,
                    "with_payload": True,
                    "with_vectors": False,
                }
                try:
                    scroll_result = client.scroll(**scroll_kwargs)
                except Exception:
                    return False

                if isinstance(scroll_result, tuple):
                    points = scroll_result[0] or []
                else:
                    points = getattr(scroll_result, "points", None) or scroll_result or []
                payload_probe_cache[collection_name] = list(points)

            for point in points:
                payload = getattr(point, "payload", None)
                if not isinstance(payload, dict) and isinstance(point, dict):
                    payload = point.get("payload")
                if isinstance(payload, dict) and _payload_has_non_empty_field(payload, field_name):
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
            for collection_name, field_names in targets.items():
                for field_name in field_names:
                    try:
                        schema_type_name = _payload_schema_type_name(collection_name, field_name)
                        if expected_schema_type in schema_type_name:
                            logger.info(
                                "[startup][payload-index][%s] %s.%s: skip (already exists)",
                                label,
                                collection_name,
                                field_name,
                            )
                            continue

                        if require_payload_probe and not _payload_field_exists_in_collection(collection_name, field_name):
                            logger.info(
                                "[startup][payload-index][%s] %s.%s: skip (field not observed)",
                                label,
                                collection_name,
                                field_name,
                            )
                            continue

                        ensure_fn(client, collection_name, field_name)
                        logger.info(
                            "[startup][payload-index][%s] %s.%s: ensure called",
                            label,
                            collection_name,
                            field_name,
                        )
                    except Exception as exc:
                        logger.warning(
                            "[startup][payload-index][%s] %s.%s: warning (%s)",
                            label,
                            collection_name,
                            field_name,
                            exc,
                        )

        _ensure_payload_indexes(
            label="keyword",
            targets=config.payload_keyword_index_targets,
            ensure_fn=ensure_keyword_index,
            expected_schema_type="KEYWORD",
        )
        _ensure_payload_indexes(
            label="keyword-optional",
            targets=config.payload_optional_keyword_index_targets,
            ensure_fn=ensure_keyword_index,
            expected_schema_type="KEYWORD",
            require_payload_probe=True,
        )
        _ensure_payload_indexes(
            label="text",
            targets=config.payload_text_index_targets,
            ensure_fn=ensure_text_index,
            expected_schema_type="TEXT",
        )
        _ensure_payload_indexes(
            label="integer",
            targets=config.payload_integer_index_targets,
            ensure_fn=ensure_integer_index,
            expected_schema_type="INTEGER",
        )
        _ensure_payload_indexes(
            label="datetime",
            targets=config.payload_datetime_index_targets,
            ensure_fn=ensure_datetime_index,
            expected_schema_type="DATETIME",
        )
    else:
        logger.info("[startup][payload-index] skipped by config")

    if config.sparse_warmup_on_boot:
        warmup_sparse_encoder()
    else:
        logger.info("[startup][fastembed] skipped by config")

    try:
        client = redis.from_url(config.redis_url, encoding="utf-8", decode_responses=True)
        await client.ping()
        app.state.kv_store = RedisKVStore(client)
        logger.info("Redis connected: %s", config.redis_url)
    except Exception as exc:
        app.state.kv_store = None
        logger.error("Redis connection failed: %s", exc, exc_info=True)

    metrics_timeout = httpx.Timeout(config.metrics_timeout_seconds)
    app.state.metrics_http = httpx.AsyncClient(timeout=metrics_timeout)
    app.state.graph = build_workflow().compile()
    logger.info("Advanced Dual-Model Pipeline compiled successfully")


async def shutdown_app_runtime(
    app: FastAPI,
    *,
    logger: logging.Logger,
    llm_cache: dict[str, Any],
) -> None:

    """FastAPI app이 종료될 때 runtime 자원을 역순으로 정리한다.
    metrics client, kv store, cached llm에 달린 close hook을 호출해 다음 부트에 영향을 주지 않게 한다.
    """
    metrics_http = getattr(app.state, "metrics_http", None)
    if metrics_http is not None:
        await metrics_http.aclose()

    kv_store = getattr(app.state, "kv_store", None)
    if kv_store is not None:
        await kv_store.close()

    for llm in llm_cache.values():
        close_fn = getattr(llm, "aclose", None)
        if close_fn is None:
            continue
        try:
            await close_fn()
        except Exception as exc:
            logger.warning(
                "[shutdown] llm close failed: model=%s error=%s",
                getattr(llm, "model_name", "unknown"),
                exc,
            )
    llm_cache.clear()
