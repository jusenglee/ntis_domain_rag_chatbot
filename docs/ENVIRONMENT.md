# 환경 (ENVIRONMENT)

이 문서는 staged-only NTIS RAG baseline의 runtime environment 기본값, validation entrypoint, deploy-facing knob를 기록합니다.

## Validation 기준선

주요 점검 명령:

```powershell
python -m py_compile apps/api/main.py apps/api/app_factory.py apps/api/routes.py apps/api/runtime.py apps/api/services/workflow_builder.py apps/api/services/request_facade.py apps/core/query_intent.py apps/core/planner_staged.py
python -m pytest
python -m pytest -m smoke
powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1
```

현재 로컬 smoke baseline에는 최소한 다음이 포함되어야 합니다.

```powershell
python -m pytest tests/test_api_routes_runtime.py tests/test_planner_stagewise.py tests/test_request_facade_and_context.py
```

## Redis Memory Backend

Runtime은 Redis를 persisted memory backend로 사용합니다.

```bash
export REDIS_URL=redis://redis8:6379
export REDIS_TTL=3600
```

메모:
- `MEMORY_BACKEND`, `LOCAL_KV_DIR`는 더 이상 활성 runtime knob가 아닙니다.
- `/health`는 Redis degradation을 payload에 보여주되, graph-ready 인스턴스를 그 이유만으로 hard readiness failure로 만들면 안 됩니다.

## Planner 와 Runtime 기준선

Runtime은 staged-only + strict입니다. 하위 레이어 invalid-strategy fallback과 promotion 재실행은 제거되었습니다.

```bash
export RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT=true
```

메모:
- 하위 runtime layer는 fallback plan을 만들지 말고 `StrategyViolation`을 발생시켜야 합니다.
- 하위 runtime layer는 promotion 재실행을 수행하면 안 됩니다.
- `RAG_ALLOW_LEGACY_META_KEYS` 기반 legacy payload-key fallback은 제거되었습니다.

## Context Window 제어

Runtime은 model budget에서 context document hard cap을 계산하며, 명시적으로 고정한 경우만 예외입니다.

```bash
export RAG_CTX_HARD_LIMIT=24
export RAG_CTX_DOC_TOKEN_ESTIMATE=900
export RAG_CTX_HARD_LIMIT_MAX=80
```

의미:
- `RAG_CTX_HARD_LIMIT`: context document hard cap을 명시적으로 고정합니다.
- `RAG_CTX_DOC_TOKEN_ESTIMATE`: hard cap 계산 시 document당 예상 token 비용입니다.
- `RAG_CTX_HARD_LIMIT_MAX`: 계산된 hard cap의 상한입니다.

## Join Debug 제어

Runtime은 join-key 강제 조사 용도의 명시적 debug 전용 switch 하나를 유지합니다.

```bash
export RAG_DEBUG_FORCE_JOIN_KEYS=false
```

메모:
- 이 플래그는 runtime debugging 전용입니다.
- 운영 기본값에서는 비활성화 상태를 유지해야 합니다.

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

Deploy 템플릿은 다음 파일 전반에서 동일한 staged-only 기본값을 유지해야 합니다.

- `deploy/env/staging.env.example`
- `deploy/env/prod.env.example`
- `deploy/helm/values-staging.yaml`
- `deploy/helm/values-prod.yaml`

## 운영 리마인더

환경 기본값이 바뀌면, observability 또는 runtime 동작도 함께 바뀌는 경우 `docs/README.md`, `docs/CONTRACT.md`, `docs/RUNBOOK.md`도 같이 업데이트합니다.

