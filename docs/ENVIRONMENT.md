# ENVIRONMENT

이 문서는 staged-only NTIS RAG baseline의 runtime environment 기본값, validation entrypoint, deploy-facing knob를 기록한다.

## Validation 기준

주요 검증 명령:

```powershell
python -m py_compile apps/api/main.py apps/api/app_factory.py apps/api/routes.py apps/api/runtime.py apps/api/services/workflow_builder.py apps/api/services/request_facade.py apps/core/query_intent.py apps/core/planner_staged.py
python -m pytest
python -m pytest -m smoke
powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1
```

현재 로컬 smoke baseline은 최소한 아래를 포함한다.

```powershell
python -m pytest tests/test_api_routes_runtime.py tests/test_planner_stagewise.py tests/test_request_facade_and_context.py
```

## Redis Memory Backend

Runtime는 Redis를 persisted memory backend로 사용한다.

```bash
export REDIS_URL=redis://redis8:6379
export REDIS_TTL=3600
```

메모:
- `MEMORY_BACKEND`, `LOCAL_KV_DIR`는 더 이상 활성 runtime knob가 아니다.
- `/health`는 Redis degradation payload를 보여주되, graph-ready 인스턴스를 그 이유만으로 hard readiness failure로 만들지 않는다.

## Planner 와 Runtime 기본값

Runtime는 staged-only + strict다. 하위 레이어의 invalid-strategy fallback과 promotion은 제거된 상태다.

```bash
export RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT=true
```

메모:
- 하위 runtime layer는 fallback plan을 만들지 않고 `StrategyViolation`을 발생시킨다.
- 하위 runtime layer는 promotion 재시도를 수행하지 않는다.
- `RAG_ALLOW_LEGACY_META_KEYS` 기반 legacy payload-key fallback은 제거됐다.

## Context Window 제어

Runtime는 model budget 안에서 context document hard cap을 계산하고, 명시적으로 고정한 경우만 예외로 둔다.

```bash
export RAG_CTX_HARD_LIMIT=24
export RAG_CTX_DOC_TOKEN_ESTIMATE=900
export RAG_CTX_HARD_LIMIT_MAX=80
```

메모:
- `RAG_CTX_HARD_LIMIT`: context document hard cap을 명시적으로 고정한다.
- `RAG_CTX_DOC_TOKEN_ESTIMATE`: hard cap 계산 시 document 1건의 예상 token 비용이다.
- `RAG_CTX_HARD_LIMIT_MAX`: 계산된 hard cap의 상한이다.

## Join Debug 제어

Runtime는 join-key 강제 조사 용도의 명시적 debug switch 하나를 둔다.

```bash
export RAG_DEBUG_FORCE_JOIN_KEYS=false
```

메모:
- 이 플래그는 runtime debugging 전용이다.
- 운영 기본값에서는 비활성 상태를 유지해야 한다.

## Model 기본값

Gemma 예시:

```bash
export GEMMA_MAX_MODEL_LEN=32768
export GEMMA_MAX_TOKENS=8192
```

Solar vLLM 예시:

```bash
export SOLAR_VLLM_MODEL=solar_102b
export SOLAR_DEADLINE_MS=4500
export SOLAR_TTFT_DEADLINE_MS=4500
export SOLAR_GEN_DEADLINE_MS=12000
export SOLAR_STREAM_MAX_CHARS=8000
export SOLAR_MAX_DOC_SENTENCES=10
export SOLAR_MAX_DOC_TOKENS=600
export SOLAR_MAX_CONTEXT_CHARS=18000
export PRIORITY_CONTEXT_FIELDS="title,title_text,title1,title2,pjt_id,pjt_no,project_id,project_no,ntis_task_id,task_id"
export SOLAR_VLLM_BASE_URL=http://vllm_solar:8010/v1
export SOLAR_VLLM_API_KEY=EMPTY
export SOLAR_VLLM_TIMEOUT=120
```

## Deploy 메모

Deploy 템플릿은 아래 파일 계열에서 동일한 staged-only 기본값을 유지해야 한다.

- `deploy/env/staging.env.example`
- `deploy/env/prod.env.example`
- `deploy/helm/values-staging.yaml`
- `deploy/helm/values-prod.yaml`

## 운영 리마인더

환경 기본값이 바뀌거나 observability 또는 runtime 동작에 직접 영향이 생기면 `docs/README.md`, `docs/CONTRACT.md`, `docs/RUNBOOK.md`를 함께 업데이트한다.
