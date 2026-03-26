from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

"""프로메테우스 지표를 수집하고 요약 스냅샷으로 노출하는 독립 모듈입니다."""

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://203.250.234.159:8004").rstrip("/")
PROMETHEUS_TIMEOUT = float(os.getenv("PROMETHEUS_TIMEOUT", "5"))
STREAM_INTERVAL_SECONDS = float(os.getenv("STREAM_INTERVAL_SECONDS", "2"))
SSE_INITIAL_PADDING_BYTES = int(os.getenv("SSE_INITIAL_PADDING_BYTES", "2048"))
SSE_MIN_EVENT_BYTES = int(os.getenv("SSE_MIN_EVENT_BYTES", "1024"))
SSE_RETRY_MS = int(os.getenv("SSE_RETRY_MS", "3000"))

VLLM_QUERY = os.getenv("VLLM_QUERY", "vllm:num_requests_running")
GPU_UTIL_QUERY = os.getenv(
    "GPU_UTIL_QUERY",
    'avg(DCGM_FI_DEV_GPU_UTIL{job=~"$dcgm_job", gpu=~"0|2"})',
)

logger = logging.getLogger(__name__)


class MetricSnapshot(BaseModel):
    """메트릭 요약 스냅샷을 표현합니다."""

    model_config = ConfigDict(populate_by_name=True)

    request_count: float | None = Field(alias="requestCount")
    gpu_util_percent: float | None = Field(alias="gpuUtilPercent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """프로메테우스 클라이언트 수명주기를 관리합니다."""

    timeout = httpx.Timeout(PROMETHEUS_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        app.state.http = client
        yield


app = FastAPI(title="Prometheus SSE API", lifespan=lifespan)


def _build_paaded_see_frame(payload: str) -> str:
    """SSE 프레임을 버퍼링 안전 크기로 만듭니다."""

    event_frame = f"event: metrics\ndata: {payload}\n\n"
    event_size = len(event_frame.encode("utf-8"))
    if event_size >= SSE_MIN_EVENT_BYTES:
        return event_frame
    need = SSE_MIN_EVENT_BYTES - event_size
    comment_frame = ":" + (" " * max(0, need - 3)) + "\n\n"
    return event_frame + comment_frame


def _extract_numeric_values(result_type: str, result: Any) -> list[float]:
    """Prometheus 응답에서 유효한 숫자값만 추출합니다."""

    values: list[float] = []
    if result_type == "scalar":
        try:
            value = float(result[1])
        except (TypeError, ValueError, IndexError):
            return []
        return [value] if math.isfinite(value) else []
    if result_type != "vector":
        return []
    for item in result or []:
        try:
            value = float(item["value"][1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


async def _prometheus_instant_query(client: httpx.AsyncClient, query: str) -> dict[str, Any]:
    """즉시 쿼리로 Prometheus 스냅샷을 가져옵니다."""

    response = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query})
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected Prometheus payload: {payload}")
    return data


async def fetch_vllm_num_requests_running(client: httpx.AsyncClient) -> float | None:
    """vLLM 진행 중 요청 건수를 가져옵니다."""

    data = await _prometheus_instant_query(client, VLLM_QUERY)
    values = _extract_numeric_values(data.get("resultType", ""), data.get("result", []))
    return round(sum(values), 4) if values else None


async def fetch_dcgm_gpu_util_avg(client: httpx.AsyncClient) -> float | None:
    """GPU 활용률 평균값을 가져옵니다."""

    data = await _prometheus_instant_query(client, GPU_UTIL_QUERY)
    values = _extract_numeric_values(data.get("resultType", ""), data.get("result", []))
    return round(sum(values) / len(values), 4) if values else None


async def _safe_value(name: str, coro: Any) -> float | None:
    """단일 메트릭 수집 실패를 `None`으로 다운그레이드합니다."""

    try:
        return await coro
    except Exception:
        logger.exception("Failed to collect %s from Prometheus", name)
        return None


async def collect_snapshot(client: httpx.AsyncClient) -> MetricSnapshot:
    """현재 시점의 메트릭 스냅샷을 수집합니다."""

    request_count, gpu_util = await asyncio.gather(
        _safe_value("requestCount", fetch_vllm_num_requests_running(client)),
        _safe_value("gpuUtilPercent", fetch_dcgm_gpu_util_avg(client)),
    )
    return MetricSnapshot(request_count=request_count, gpu_util_percent=gpu_util)


@app.get("/metrics", response_model=MetricSnapshot, response_model_by_alias=True)
async def get_metrics() -> MetricSnapshot:
    """현재 메트릭 스냅샷을 단건 응답으로 반환합니다."""

    return await collect_snapshot(app.state.http)


@app.get("/metrics/stream")
async def stream_metrics(request: Request) -> StreamingResponse:
    """메트릭 SSE 스트림을 제공합니다."""

    async def event_generator() -> AsyncIterable[str]:
        """연결이 유지되는 동안 최신 스냅샷을 반복 전송합니다."""

        yield f"retry: {SSE_RETRY_MS}\n:" + (" " * SSE_INITIAL_PADDING_BYTES) + "\n\n"
        while True:
            if await request.is_disconnected():
                break
            snapshot = await collect_snapshot(request.app.state.http)
            payload = snapshot.model_dump_json(by_alias=True)
            yield _build_paaded_see_frame(payload)
            await asyncio.sleep(STREAM_INTERVAL_SECONDS)

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)
