# 운영 환경 변수

프로덕션에서 Gemma 모델을 사용하는 경우 아래 값을 기준으로 설정합니다.

```bash
# Gemma 컨텍스트 길이 (Triton/VLLM 설정과 동일하게 맞춰주세요)
export GEMMA_MAX_MODEL_LEN=32768

# Gemma 출력 토큰 상한 (운영 요구사항에 맞게 조정)
# 예: 4096, 8192 등
export GEMMA_MAX_TOKENS=8192
```


## Redis 메모리 저장소(고정)

서버는 대화 메모리 백엔드를 **Redis로 고정**합니다.

```bash
# Redis 연결 문자열
export REDIS_URL=redis://redis8:6379

# 대화/컨텍스트 TTL(초)
export REDIS_TTL=3600
```

- `MEMORY_BACKEND`, `LOCAL_KV_DIR` 는 더 이상 사용하지 않습니다.
- Redis 연결 실패 시 `/health`의 `kv` 값은 `disconnected`로 표시됩니다.

## 참고

- `MAX_TOKENS`는 모델별 출력 토큰 상한이 지정되지 않았을 때의 기본값입니다.
- Gemma 모델에서는 `GEMMA_MAX_TOKENS`가 `MAX_TOKENS`보다 우선합니다.
- `DEFAULT_MAX_MODEL_LEN`은 모델별 길이를 지정하지 않았을 때의 기본값입니다.

## 수동 확인 시나리오 (최종 응답 페이로드 절단 완화)

1. 긴 `meta_basic`/`meta_detail` 및 본문이 포함된 문서를 반환하도록 질의합니다.
   - 예: 상세 정보가 풍부한 과제/성과 검색, 혹은 관련 문서를 많이 포함하는 질의.
2. 응답 생성 로그에서 `context_text - 페이로드 평탄화 후 데이터` 섹션을 확인합니다.
3. 동일한 입력에서 이전 대비 `context_text`의 길이가 더 길거나, 문장/토큰 절단이 줄어든 것을 확인합니다.
   - 최종 응답 경로는 `relax_limits=True`로 전달되어 `MAX_DOC_*` 절단이 완화됩니다.

## vLLM request_id 추적 운영 설정

- 실행 경로: `scripts/vllm/start_vllm_solar.sh`
- 상세 가이드: `docs/vllm_request_id_tracking.md`

## Solar vLLM 기본 모델/스트리밍 가드

```bash
# Solar vLLM 기본 모델명 (미지정 시 solar_102b)
export SOLAR_VLLM_MODEL=solar_102b

# Solar 스트리밍 제어(기본값)
# - SOLAR_TTFT_DEADLINE_MS 미지정 시 SOLAR_DEADLINE_MS 값을 TTFT 제한으로 사용(하위 호환)
# - SOLAR_GEN_DEADLINE_MS 는 첫 토큰 이후 생성 구간 제한
export SOLAR_DEADLINE_MS=4500
export SOLAR_TTFT_DEADLINE_MS=4500
export SOLAR_GEN_DEADLINE_MS=12000
export SOLAR_STREAM_MAX_CHARS=8000


# Solar 문서 정제 상한(모델별 분기)
# - Solar 경로는 relax_limits=False + 아래 상한 우선 적용
# - Gemma 경로는 기존처럼 relax_limits=True(완화) 유지
export SOLAR_MAX_DOC_SENTENCES=10
export SOLAR_MAX_DOC_TOKENS=600
export SOLAR_MAX_CONTEXT_CHARS=18000

# 핵심 필드 최소 포함(과제명/ID 보존)
export PRIORITY_CONTEXT_FIELDS="title,title_text,title1,title2,pjt_id,pjt_no,project_id,project_no,ntis_task_id,task_id,과제명,과제번호"

# Solar OpenAI-Compat 엔드포인트
# 누락/오타는 서버 부팅 시점에 즉시 에러 처리됩니다.
export SOLAR_VLLM_BASE_URL=http://vllm_solar:8010/v1
export SOLAR_VLLM_API_KEY=EMPTY
export SOLAR_VLLM_TIMEOUT=120

```


## Planner Stagewise 기본값(배포 필수)

```bash
# planner 2-stage 파이프라인 기본 ON
export PLANNER_STAGEWISE_ENABLED=true
```

- 기본값은 `true`이며, 미설정 시에도 stagewise 경로를 사용합니다.
- 배포 템플릿에도 동일하게 고정합니다.
  - `deploy/env/staging.env.example`
  - `deploy/env/prod.env.example`
  - `deploy/helm/values-staging.yaml`
  - `deploy/helm/values-prod.yaml`

## Planner latency 튜닝

```bash
# planner 체인에서 thinking 토큰 생성을 비활성화(기본 권장: true)
export PLANNER_DISABLE_THINKING=true

# planner 체인 1회 호출 timeout(ms)
export PLANNER_TIMEOUT_MS=4500

# planner 파싱 재시도 횟수(실제 적용값은 PLANNER_V2_RETRY_ATTEMPTS_MAX로 상한 제한)
export PLANNER_V2_RETRY_ATTEMPTS=1
export PLANNER_V2_RETRY_ATTEMPTS_MAX=2

# planner retry backoff(초)
export PLANNER_V2_RETRY_BACKOFF_SEC=0.35
export PLANNER_V2_BACKOFF_CAP_SEC=0.8
```

- `PLANNER_DISABLE_THINKING=true`로 두면 planner 응답 지연과 JSON 파싱 실패 확률을 함께 낮출 수 있습니다.
- timeout은 `PLANNER_TIMEOUT_MS`로 통일하고, 로그 이벤트 `PLANNER.PIPELINE.timeout_ms`와 동일 값으로 관측됩니다.
- retry는 기본 1회(총 1 attempt)로 두고, 장애 구간에서만 상향합니다. 상향 시 `attempt`, `backoff_sec`를 함께 모니터링하세요.

## RAG 결과 TopN 로그 설정

```bash
# normal tier 결과 TopN (기본 3)
# 우선순위: RAG_LOG_TOPN_NORMAL > RAG_LOG_TOPN_FINAL(하위호환) > 3
export RAG_LOG_TOPN_NORMAL=3

# debug tier 결과 TopN (기본 12)
# 이벤트: RAG.RESULT.TOP.DEBUG
export RAG_LOG_TOPN_DEBUG=12

# 하위호환 키(legacy): normal 값이 비어 있을 때만 사용
export RAG_LOG_TOPN_FINAL=3
```

- `RAG.RESULT.TOP`(normal)는 운영 기본 관측 이벤트입니다.
- `RAG.RESULT.TOP.DEBUG`(debug)는 상세 점검 시에만 보며 `RAG_LOG_LEVEL=debug` 이상에서 출력됩니다.

## ORG/TITLE 필터 정책 고정값

```bash
# 기관 부분일치(prefix 확장) 정책
export RAG_ORG_PARTIAL_PREFIX_LEN=6
export RAG_ORG_PARTIAL_MIN_LEN=4

# 제목은 server-side 하드필터를 사용하지 않고 soft ranking만 사용
# (환경변수 입력값이 있더라도 내부적으로 soft 고정)
export RAG_LOOKUP_TITLE_FILTER_POLICY=soft
```


## Prometheus SSE 메트릭 스트림 설정

```bash
# Prometheus SSE API 설정(metrics.py)
export PROMETHEUS_URL=http://localhost:9090
export PROMETHEUS_TIMEOUT=5

# /metrics/stream 이벤트 주기(초), 기본 2초
export STREAM_INTERVAL_SECONDS=2
```

- `/metrics/stream`은 SSE `event=metrics`로 전송하며 `data`는 JSON object(`MetricSnapshot`)입니다.
## Metrics API 환경 변수

```bash
# Prometheus 접속 주소(뒤 슬래시는 자동 제거)
export PROMETHEUS_URL=http://localhost:9090

# Prometheus API timeout(초)
export PROMETHEUS_TIMEOUT=5

# SSE(/metrics/stream) 전송 주기(초)
export STREAM_INTERVAL_SECONDS=2

# vLLM running request 수집 PromQL
export VLLM_QUERY='vllm:num_requests_running'

# GPU Util 평균 수집 PromQL
export GPU_UTIL_QUERY='avg(DCGM_FI_DEV_GPU_UTIL{job=~"$dcgm_job", gpu=~"0|2"})'
```

- Prometheus 장애/쿼리 실패 시 해당 필드는 `None`으로 반환되고, 서버 로그에는 예외 스택트레이스가 남습니다.
