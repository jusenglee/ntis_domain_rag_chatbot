# 06. 운영과 환경

> "어떻게 실행하고, 어떤 토글이 있고, 로그를 어떻게 보나". §1~§2가 실행·엔드포인트, §3 이후가 참조.

## 1. 실행

```bash
export PYTHONPATH=.            # PowerShell: $env:PYTHONPATH='.'
python apps/api/main.py        # uvicorn
```

- KV는 Redis 우선, 실패 시 파일 KV 폴백. 세션 키: `pipeline:v1:{conversation_id}:session`.
- ⚠️ `requirements.txt`/`Dockerfile` 없음. 검증 환경 = conda env `ntis_domain_rag_chatbot`(Python 3.12). 실행·의존성 재현 절차는 인계 후 정비 필요 → 루트 `HANDOVER.md`.

## 2. ⚠️ 외부 엔드포인트 기본값 (인수 시 교체)

코드 디폴트가 특정 IP로 박혀 있으나 **전부 환경변수 오버라이드** 가능(`apps/platform/settings.py`).

| 대상 | 환경변수 | 코드 디폴트 |
|---|---|---|
| Qdrant | `QDRANT_HOST` / `QDRANT_PORT` | `203.250.234.159` / `8005` |
| Triton | `TRITON_URL` | `203.250.234.159:8001` |
| Solar(vLLM) | `SOLAR_VLLM_BASE_URL` | `http://203.250.234.159:8010/v1` |
| Redis | `REDIS_URL` | `redis://redis8:6379` |

## 3. 런타임 토글

| 환경변수 | 기본 | 효과 |
|---|---|---|
| `RAG_DUAL_ANSWER_ENABLED` | `true` | 듀얼: 메인 Solar(A) + 비교 Gemma(B). `false`=Solar 단일을 두 패널에 |
| `RAG_GROUNDING_CHECKER_ENABLED` | `true` | critic grounding 판정 활성 |
| `RAG_PLANNER_THINKING_ENABLED` | `true` | 플래너 Pass 1 Solar thinking |
| `RAG_CRITIC_THINKING_ENABLED` | `true` | critic grounding Solar thinking |
| `RAG_CRITIC_THREAD_TIMEOUT_SECONDS` | `600` | critic 판정 동기 래퍼 타임아웃 |
| `PIPELINE_SESSION_TTL_SECONDS` | `604800` | KV 세션 TTL(초) |
| `SOLAR_VLLM_TIMEOUT` | `600.0` | OpenAI 호환 클라이언트 타임아웃 |

> **제거된 토글:** `RAG_AGENTIC_MODE`(ADR-0020), `RAG_ADEQUACY_GATE_ENABLED`/`RAG_ADEQUACY_THINKING_ENABLED`(ADR-0023). 이들 롤백은 코드 레벨 revert로만 가능.

## 4. 모델 역할

- **`solar_vllm_0`** (vLLM, OpenAI 호환): dialogue/planner/critic 판단 + **메인 답변**(패널 A). 이 초안을 CriticAgent가 검증하고, `reference.set`·세션 상태를 구동.
- **`gemma_triton_0`** (Triton): **비교 답변**(패널 B, raw — 검증·저장 안 함) + `response.*` 직접답변 도구.

> ADR-0021로 답변 모델 역할이 Gemma → **Solar**로 이동.

## 5. 로그

agentic 흐름 로그는 `[agentic_trace]` 태그. 이벤트: 플래너 스텝(action/tool/reason), 도구 결과(status+latency), tool_executor 라우팅(`response.*` 종결 vs planner 복귀), answer_curator 선택 경로, critic 결정, plan 요약, 세션 저장 상태(발행 여부·evidence view·subject/manifest/focused-detail 존재·KV 저장 결과·총 latency).

## 6. 디버깅 — 어디를 보나

| 증상 | 위치 |
|---|---|
| 도구를 잘못/너무 많이 부름, 안 끝남 | `planner_agent.py`(루프·max_steps=8), `dialogue_agent` |
| 검색 자체가 이상 | `search_agent.py` / SearchRouter(ADR-0022) |
| 답 품질·환각, 발행 보류 | `answer_agent` / `critic_agent` / grounding `llm_judge.py` |
| 이어묻기/세션 깨짐 | `session_state.py` / `session_store.py`(KV 압축 경로) |
