"""FastAPI Composition Root (3계층 권한분리 파이프라인 전용).

ADR-0018 적용 후 슬림 버전. 환경변수만 읽고 RouteDeps와 lifespan을 결합한다.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from apps.api.routes import register_routes
from apps.api.runtime import AppRuntimeConfig, initialize_app_runtime, shutdown_app_runtime
from apps.platform.settings import REDIS_URL


TEMPLATE_INDEX_PATH = Path("templates/index.html")

APP_RUNTIME_CONFIG = AppRuntimeConfig(
    redis_url=REDIS_URL,
    file_kv_root=str(os.getenv("FILE_KVSTORE_ROOT", "local_kvstore") or "local_kvstore").strip()
    or "local_kvstore",
    metrics_timeout_seconds=float(os.getenv("METRICS_TIMEOUT_SECONDS", "5") or 5),
)


def create_app() -> FastAPI:
    """FastAPI 애플리케이션을 생성한다."""

    @asynccontextmanager
    async def runtime_lifespan(app: FastAPI):
        try:
            await initialize_app_runtime(app, config=APP_RUNTIME_CONFIG)
            yield
        finally:
            await shutdown_app_runtime(app)

    app = FastAPI(lifespan=runtime_lifespan)
    register_routes(app, template_index_path=TEMPLATE_INDEX_PATH)
    return app
