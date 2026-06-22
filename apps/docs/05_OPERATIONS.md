# 05. 운영과 환경

> "어떻게 실행하고, 문제가 생기면 로그로 어떻게 추적하나"를 다룬다. §1~§2가 실행, §3이 디버깅 출발점, 나머지는 참조다. 운영 표면: `apps/api/app_factory.py`(`create_app()`), `apps/api/runtime.py`(런타임 초기화·그래프 컴파일), `apps/api/routes.py`. 설정 정본: `apps/platform/settings.py`.

## 1. 처음 실행할 때

```bash
export PYTHONPATH=.            # 윈도우 PowerShell: $env:PYTHONPATH='.'
python apps/api/main.py        # uvicorn, 0.0.0.0:8008
```

뜨고 나면 `GET /health`로 상태 확인, `POST /query/stream`(body `{"question": "..."}`)으로 실제 질의.

- **세션 저장소**는 Redis 우선, 안 붙으면 자동으로 파일(`local_kvstore`)로 폴백한다. 그래서 로컬 개발에선 Redis 없어도 돈다.
- 외부 서버(Qdrant/Triton/Solar) 주소는 §2 참고 — **로컬에 그게 없으면 검색·LLM 단계에서 실패**한다(개발 환경 경고는 §5).

> ⚠️ `requirements.txt`/`Dockerfile`이 저장소에 없다. 현재 검증 환경은 conda env `ntis_domain_rag_chatbot`(Python 3.12). 정확한 의존성·실행 절차 재현은 인계 후 정비 필요 → 루트 `HANDOVER.md` §5.

## 2. ⚠️ 외부 엔드포인트 기본값 (인수 시 교체 대상)

코드 디폴트가 특정 IP로 박혀 있으나 **전부 환경변수로 오버라이드**된다. 자사 인프라로 옮길 때 환경변수 주입 또는 `settings.py` 디폴트 교체.

| 대상 | 환경변수 | 코드 디폴트 |
|---|---|---|
| Qdrant | `QDRANT_HOST` / `QDRANT_PORT` | `203.250.234.159` / `8005` |
| Triton | `TRITON_URL` | `203.250.234.159:8001` |
| Solar(vLLM, OpenAI 호환) | `SOLAR_VLLM_BASE_URL` | `http://203.250.234.159:8010/v1` |
| Redis | `REDIS_URL` | `redis://redis8:6379` |
| Oracle(요청 기본값 소스) | — | host `203.250.234.203`, port `1253`, service `KNTIS`, table `IRD_PARAM` |

### 주요 환경변수

| 변수 | 디폴트/비고 |
|---|---|
| `NTIS_LOCAL_MODELS_ROOT` | 로컬 임베딩/모델 루트 |
| `FILE_KVSTORE_ROOT` | `local_kvstore` |
| `ENABLE_DEBUG_ROUTES` | off — `/query/debug` 활성화 |
| `RAG_EVIDENCE_TOKEN_BUDGET` | `20000` |
| `RAG_MIN_DENSE_SCORE` / `RAG_TOPK_DENSE` | `0.2` / `50` |
| `RAG_W_LEX` / `RAG_TOPK_LEX_CAND` | `0.4` / `100` |
| `AGENTIC_DECISION_REPAIR_MAX_ATTEMPTS` | `1` (자기교정 1회가 정상) |
| `AGENTIC_MAX_STEPS` | 예약(현재 단일 결정 워크플로에선 미적용) |
| `LLM_DISAMBIGUATION_LABEL_ENABLED` / `LLM_PROSE_ENABLED` / `TURN_INTERPRETATION_LLM_FIRST` | ADR-0014 스코프 A/B/C 토글, 기본 off |

## 3. 문제 추적: 로그 triage 순서

"답이 이상하다" 싶으면 이 순서로 로그 키를 따라간다 (요청 1건의 생애 순).

1. `REQ.START` — 질문·override·conversation_id (입력 확인)
2. `PLANNER.SIGNALS` / `STAGE1` / `STAGE15.*` / `STAGE2.*` / `STAGE*.ERROR` / `ASSEMBLE` (의도 → 조건 컴파일)
3. `RAG.RETRIEVAL_QUERY.RESOLUTION` / `RAG.RESULT` / `RAG.CONTEXT` (실제 검색)
4. `AGENT.STATE_CARD.BUILT` / `AGENT.DECISION` / `TOOL_DIRECT_COMPILE` / `REFINE_CURRENT_SUBJECT` / `TOOL_OBSERVATION` / `TOOL_RETRY.*` (에이전트 판단)
5. `AGENT.PARSE_ERROR` / `SELF_REPAIR.*` / `INVOKE_ERROR` / `CLARIFICATION.BLOCKED` / `INTERNAL_ERROR` (에이전트 오류)
6. `PLANNER.COUNT_CONTRACT` / `RAG.EXECUTION_MANAGER.RESULT` / `DISPLAY.SNAPSHOT.BUILT` / `ANSWER.STATE_DIAG` (개수·표시·정합성)
7. `REFERENCE.INVALID_PAYLOAD` / `REFERENCE.FINALIZATION` (출처)
8. `LLM.RESULT` / `REQ.SUMMARY` (최종 결과 요약)

핵심 함정: `AGENT.CLARIFICATION`(되묻기)은 결정이 `ask_clarification`일 때만 나와야 한다. 도구 백엔드 오류(`planner_error`/`LLMJSONExtractionError`/`PLANNER.STAGE*.ERROR`)는 **사용자 모호성이 아니다** → `AGENT.TOOL_RETRY.*` 또는 `AGENT.INTERNAL_ERROR`로 가야 하며 되묻기로 위장되면 회귀다.

> 전체 프롬프트/raw payload는 로깅하지 않는다(필드별 char count + 짧은 preview만).

## 4. 정밀 참조 — 검증 명령 (PowerShell, 루트에서)

```powershell
$env:PYTHONPATH='.'
python -m compileall -q apps                                          # 문법
python -c "from apps.api.app_factory import create_app; create_app()" # 조립 스모크
python -c "from apps.api.workflow_builder import build_request_workflow; build_request_workflow().compile()"  # 그래프 스모크
# 타깃 pytest → 06_TESTING.md
```

## 5. 정상 개발환경 경고 (무시해도 됨)

`Redis → File KV 폴백`, `Oracle defaults unavailable`, `oracledb import failed`는 로컬 개발에서 **정상**이다. triage 우선순위에서 제외.
