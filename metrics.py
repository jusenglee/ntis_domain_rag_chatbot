import asyncio
import logging
import math
import os
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.sse import EventSourceResponse
from pydantic import BaseModel

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090").rstrip("/")
PROMETHEUS_TIMEOUT = float(os.getenv("PROMETHEUS_TIMEOUT", "5"))
STREAM_INTERVAL_SECONDS = float(os.getenv("STREAM_INTERVAL_SECONDS", "2"))

# 필요하면 PromQL 자체를 환경 변수로 바꿔서 라벨 조건을 붙일 수 있습니다.
VLLM_QUERY = os.getenv("VLLM_QUERY", "vllm:num_requests_running")
GPU_UTIL_QUERY = os.getenv("GPU_UTIL_QUERY", "DCGM_FI_DEV_GPU_UTIL")


class MetricSnapshot(BaseModel):
    vllm_num_requests_running: float | None
    dcgm_fi_dev_gpu_util_avg: float | None


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(PROMETHEUS_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        app.state.http = client
        yield


logger = logging.getLogger(__name__)


app = FastAPI(title="Prometheus SSE API", lifespan=lifespan)


def _extract_numeric_values(result_type: str, result: Any) -> list[float]:
    values: list[float] = []

    if result_type == "scalar":
        try:
            value = float(result[1])
            if math.isfinite(value):
                values.append(value)
        except (TypeError, ValueError, IndexError):
            return []
        return values

    if result_type != "vector":
        return []

    for item in result:
        if "value" not in item:
            continue

        try:
            value = float(item["value"][1])
        except (TypeError, ValueError, IndexError):
            continue

        if math.isfinite(value):
            values.append(value)

    return values


async def _prometheus_instant_query(client: httpx.AsyncClient, query: str) -> dict[str, Any]:
    response = await client.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": query},
    )
    response.raise_for_status()

    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected Prometheus payload: {payload}")

    return data


async def fetch_vllm_num_requests_running(client: httpx.AsyncClient) -> float | None:
    data = await _prometheus_instant_query(client, VLLM_QUERY)
    values = _extract_numeric_values(data.get("resultType", ""), data.get("result", []))

    if not values:
        return None

    # 추측입니다: 여러 시계열이 반환되면 전체 running request 총합이 더 자연스러워 보여 합산합니다.
    # 단일 시계열만 필요하면 아래 return values[0] 로 바꾸면 됩니다.
    return round(sum(values), 4)


async def fetch_dcgm_gpu_util_avg(client: httpx.AsyncClient) -> float | None:
    data = await _prometheus_instant_query(client, GPU_UTIL_QUERY)
    values = _extract_numeric_values(data.get("resultType", ""), data.get("result", []))

    if not values:
        return None

    return round(sum(values) / len(values), 4)


async def _safe_value(name: str, coro) -> float | None:
    try:
        return await coro
    except Exception:
        logger.exception("Failed to collect %s from Prometheus", name)
        return None


async def collect_snapshot(client: httpx.AsyncClient) -> MetricSnapshot:
    vllm_value, gpu_avg = await asyncio.gather(
        _safe_value("requestCount", fetch_vllm_num_requests_running(client)),
        _safe_value("gpuUtilPercent", fetch_dcgm_gpu_util_avg(client)),
    )

    return MetricSnapshot(
        vllm_num_requests_running=vllm_value,
        dcgm_fi_dev_gpu_util_avg=gpu_avg,
    )


@app.get("/metrics", response_model=MetricSnapshot)
async def get_metrics() -> MetricSnapshot:
    return await collect_snapshot(app.state.http)


@app.get("/metrics/stream", response_class=EventSourceResponse)
async def stream_metrics(request: Request) -> AsyncIterable[dict[str, Any]]:
    async def event_generator() -> AsyncIterable[dict[str, Any]]:
        while True:
            if await request.is_disconnected():
                break

            snapshot = await collect_snapshot(request.app.state.http)
            payload = snapshot.model_dump(by_alias=True)
            yield {"event": "metrics", "data": payload}

            await asyncio.sleep(STREAM_INTERVAL_SECONDS)

    return event_generator()
