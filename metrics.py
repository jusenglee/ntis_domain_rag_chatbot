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

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://203.250.234.159:8004").rstrip("/")
PROMETHEUS_TIMEOUT = float(os.getenv("PROMETHEUS_TIMEOUT", "5"))
STREAM_INTERVAL_SECONDS = float(os.getenv("STREAM_INTERVAL_SECONDS", "2"))

# 필요하면 PromQL 자체를 환경 변수로 바꿔서 라벨 조건을 붙일 수 있습니다.
VLLM_QUERY = os.getenv("VLLM_QUERY", "vllm:num_requests_running")
GPU_UTIL_QUERY = os.getenv(
    "GPU_UTIL_QUERY",
    'DCGM_FI_DEV_GPU_UTIL{gpu=~"0|2"}'
)


class MetricSnapshot(BaseModel):
    """`/metrics`, `/metrics/stream` 공통 응답 스키마.

    Example:
        {
          "requestCount": 4.0,
          "gpuUtilPercent": 78.5
        }

    Fields:
        - requestCount: 현재 시점에 처리 중인 vLLM 요청 수 합계.
        - gpuUtilPercent: 현재 시점의 GPU 사용률 평균(0~100).
    """

    model_config = ConfigDict(populate_by_name=True)

    request_count: float | None = Field(alias="requestCount")
    gpu_util_percent: float | None = Field(alias="gpuUtilPercent")


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
    """Prometheus에서 메트릭 스냅샷을 수집한다.

    Args:
        client: FastAPI lifespan에서 공유하는 ``httpx.AsyncClient``.

    Returns:
        ``MetricSnapshot`` 객체.
        - ``requestCount``: vLLM running request 합계.
        - ``gpuUtilPercent``: GPU Util(%) 평균.
        각 필드는 Prometheus 질의/파싱 실패 또는 빈 시계열일 때 ``None``이 된다.

    Exception behavior:
        함수 자체는 예외를 외부로 전파하지 않는다.
        내부적으로 각 메트릭 수집은 ``_safe_value``로 감싸져 실패 시 예외를 로깅한 뒤
        해당 필드만 ``None``으로 대체한다.
    """
    vllm_value, gpu_avg = await asyncio.gather(
        _safe_value("requestCount", fetch_vllm_num_requests_running(client)),
        _safe_value("gpuUtilPercent", fetch_dcgm_gpu_util_avg(client)),
    )

    return MetricSnapshot(
        request_count=vllm_value,
        gpu_util_percent=gpu_avg,
    )


@app.get("/metrics", response_model=MetricSnapshot, response_model_by_alias=True)
async def get_metrics() -> MetricSnapshot:
    return await collect_snapshot(app.state.http)


@app.get("/metrics/stream")
async def stream_metrics(request: Request) -> StreamingResponse:
    """메트릭 SSE 스트림을 제공한다.

    Args:
        request: 클라이언트 연결 상태 확인(``is_disconnected``)에 사용하는 FastAPI 요청 객체.

    Returns:
        ``text/event-stream`` 응답을 반환한다.
        각 이벤트는 ``event: metrics`` 및 ``data: <MetricSnapshot JSON>`` 형식이다.
        기본 전송 주기는 ``STREAM_INTERVAL_SECONDS`` 환경 변수(기본 2초)다.

    Exception behavior:
        스냅샷 수집 중 Prometheus 오류가 발생해도 스트림은 유지된다.
        실패한 필드는 ``None``으로 내려가며 예외 로그가 남는다.
        클라이언트 연결이 끊기면 generator가 종료된다.
    """

    async def event_generator() -> AsyncIterable[str]:
        while True:
            if await request.is_disconnected():
                break

            snapshot = await collect_snapshot(request.app.state.http)
            payload = snapshot.model_dump_json(by_alias=True)
            yield f"event: metrics\ndata: {payload}\n\n"

            await asyncio.sleep(STREAM_INTERVAL_SECONDS)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
