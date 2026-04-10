"""Runtime bootstrap and shutdown helpers for the FastAPI app."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from fastapi import FastAPI

from apps.platform.storage import FileKVStore, KVStore


@dataclass(frozen=True)
class AppRuntimeConfig:
    """Startup contract passed from the composition root."""

    redis_url: str
    file_kv_root: str
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
    """Redis adapter that satisfies the shared KVStore contract."""

    def __init__(self, client: Any):
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
    """Best-effort async close for resources that may expose close/aclose."""

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
    """Prefer Redis, but fall back to the file-backed KV store on startup failure."""

    redis_client: Any = None
    try:
        redis_client = redis_from_url(redis_url, encoding="utf-8", decode_responses=True)
        await redis_client.ping()
        logger_obj.info("Redis connected: %s", redis_url)
        return RedisKVStore(redis_client)
    except Exception as exc:
        try:
            await _close_async_resource(redis_client)
        except Exception:
            logger_obj.warning("Redis cleanup after failed startup connect also failed", exc_info=True)
        logger_obj.error("Redis connection failed: %s", exc, exc_info=True)

    fallback_root = str(Path(file_kv_root or "local_kvstore").resolve())
    try:
        file_store = file_store_factory(fallback_root)
        await file_store.ping()
        logger_obj.warning("KV backend falling back to file store: %s", fallback_root)
        logger_obj.info("File KV backend ready: %s", fallback_root)
        return file_store
    except Exception as fallback_exc:
        logger_obj.error("File KV fallback failed: %s", fallback_exc, exc_info=True)
        return None


async def initialize_app_runtime(app: FastAPI, *, config: AppRuntimeConfig) -> None:
    """Initialize runtime resources needed before serving requests."""

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

    validate_project_key_env_contract(log_info=logger.info)
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

    app.state.kv_store = await _initialize_kv_store_with_fallback(
        redis_url=config.redis_url,
        file_kv_root=config.file_kv_root,
        logger_obj=logger,
        redis_from_url=redis.from_url,
    )

    metrics_timeout = httpx.Timeout(config.metrics_timeout_seconds)
    app.state.metrics_http = httpx.AsyncClient(timeout=metrics_timeout)
    app.state.graph = build_request_workflow().compile()
    logger.info("Advanced Dual-Model Pipeline compiled successfully")


async def shutdown_app_runtime(app: FastAPI) -> None:
    """Close runtime resources in reverse startup order."""

    from apps.api.runtime_helpers import logger
    from apps.chat.llm_runtime import get_llm_cache

    metrics_http = getattr(app.state, "metrics_http", None)
    if metrics_http is not None:
        await metrics_http.aclose()

    kv_store = getattr(app.state, "kv_store", None)
    if kv_store is not None:
        await kv_store.close()

    llm_cache = get_llm_cache()
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
