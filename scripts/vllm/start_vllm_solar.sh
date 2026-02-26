#!/usr/bin/env bash
set -euo pipefail

# 운영 중 엔진 로그를 더 상세히 보고 싶을 때만 활성화하세요.
# 예) export VLLM_LOGGING_LEVEL=DEBUG
: "${VLLM_LOGGING_LEVEL:=INFO}"

MODEL_PATH="${SOLAR_VLLM_MODEL:-solar_102b}"
HOST="${SOLAR_VLLM_HOST:-0.0.0.0}"
PORT="${SOLAR_VLLM_PORT:-8010}"

exec vllm serve "${MODEL_PATH}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --enable-request-id-headers \
  --uvicorn-log-level debug \
  --max-log-len 512
