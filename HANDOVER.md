# 인수인계 문서 (HANDOVER) — 판단-검색-에이전트 브랜치

> **대상**: 본 프로젝트를 인수하여 유지보수·개발할 새 담당자/회사
> **목적**: 코드를 모르는 상태에서도 온보딩·운영·개발을 시작하도록 진입점·현재 상태·즉시 처리 이슈를 한 곳에 모은다.
> **기준**: 2026-06-23 · 브랜치 `판단-검색-에이전트` (HEAD `557cc6e`)

설계·규칙의 정본은 **[`apps/docs/`](./apps/docs/README.md)** 다.

---

## 0. 60초 요약

- **무엇**: NTIS 국가 R&D 데이터를 다루는 **자율 에이전트 챗봇**. 고정 RAG가 아니라, 챗봇이 "이 질문에 뭘 할지"를 먼저 판단하고 **NTIS 검색은 필요할 때 부르는 도구**(MCP식 backplane)다.
- **예시**: "안녕"→검색 없이 인사 / "신동구 연구자(KISTI) 활동내역"→`search` 도구 호출 후 출처와 함께 답 / "오늘 환율"→정직한 거절. 복잡하면 플래너가 도구를 **여러 번**(최대 8스텝) 부른다.
- **구조**: 3계층 권한분리(판단/근거/발행, ADR-0018) + 단일 agentic 파이프라인(ADR-0020) + 듀얼 답변(Solar A / Gemma B, ADR-0021).
- **즉시 볼 이슈**: §5. 1차 관문은 **의존성 매니페스트 부재**와 **외부 엔드포인트 하드코딩**.

---

## 1. 온보딩 순서

1. 이 문서(§2, §5)
2. [`apps/docs/00_ONBOARDING.md`](./apps/docs/00_ONBOARDING.md) — 실제 예시·진입점·멘탈모델
3. [`apps/docs/01_ARCHITECTURE.md`](./apps/docs/01_ARCHITECTURE.md) — 3계층·단일 파이프라인
4. [`apps/docs/03_AGENTIC_RUNTIME.md`](./apps/docs/03_AGENTIC_RUNTIME.md) — **플래너 루프(핵심)**
5. [`AGENTS.md`](./AGENTS.md) — 코드 작업 원칙

---

## 2. 시스템 구성

| 구성 | 내용 |
|---|---|
| API | FastAPI (`apps/api/`), 런타임 조립 `apps/api/runtime.py` |
| 그래프(라이브) | `apps/pipeline/agentic_workflow.py`의 `build_agentic_pipeline_graph()` |
| 흐름 | load_session → dialogue_agent → entity_resolver → **planner_loop ⇄ tool_executor** → answer_curator → answer_agent → critic_agent → save_session |
| LLM | Solar(`solar_vllm_0`, 메인/판단) + Gemma(`gemma_triton_0`, 비교) |
| 벡터 DB | Qdrant (`ntis_project_v1`, `ntis_perf_v1`, `ntis_supports`) — 도구 backplane |
| 세션 | Redis(파일 KV 폴백), 키 `pipeline:v1:{conversation_id}:session` |
| 설정 정본 | `apps/platform/settings.py` |

> 이 브랜치는 `apps/pipeline`(라이브)와 옛 패키지(`apps/{retrieval,chat,evidence,...}`)가 **공존**한다. 옛 패키지는 죽은 코드가 아니라 pipeline이 재사용하는 하위 빌딩블록이다(예: `apps.retrieval.rag_store`, `apps.chat.llm_runtime`). **임의 삭제 금지.**

---

## 3. 데이터 모델 (도메인 핵심)

국가 R&D 데이터 = **과제(Project)** + **성과(Output, 10종)**. ⚠️ `pjt_id`(개별) ≠ `pjt_no`(다년도 묶음), 기관 역할 구분, raw payload 직접 주입 금지(CanonicalEvidence 경유). 상세 → [02 계약과 규칙](./apps/docs/02_CONTRACTS_AND_RULES.md).

---

## 4. ⚠️ 인수 시 즉시 처리 필요 (KNOWN ISSUES)

### 4.1 의존성·실행 매니페스트 부재 [최우선]
루트에 `requirements.txt`/`pyproject.toml`/`Dockerfile` 없음. 검증 환경은 conda env `ntis_domain_rag_chatbot`(Python 3.12). **의존성 고정 + 실행 절차 문서화 시급.**

### 4.2 외부 엔드포인트 하드코딩 디폴트
Qdrant/Triton/Solar 기본값이 `203.250.234.159`로 코드에 박혀 있음(환경변수 오버라이드 가능). 인프라 이전 시 교체 → [06 운영과 환경](./apps/docs/06_OPERATIONS_AND_ENV.md).

### 4.3 테스트 트리 상태
- `tests/`가 `.gitignore`라 **버전관리 안 됨**(로컬만). 정책 결정 필요.
- 타 브랜치 테스트 혼입으로 그냥 `pytest tests`는 수집에러로 중단. **그린 기준선 = 496 passed / 32 failed**(stale 6 수집에러 + 32 failed 제외/감안). 정확한 제외 목록·명령 → [07 검증과 리뷰](./apps/docs/07_VALIDATION_AND_REVIEW.md).

### 4.4 다수 브랜치 — 정본 합의 필요
`판단-검색-에이전트`(현재, `apps/pipeline`)·`고도화`(`apps/api`+conversation+planner 구조, 별도)·`main`·`misp` 등 구조가 서로 다르다. 이 브랜치는 `origin`에만 있고 **팀 서버 `miso`엔 없다**. **정본 브랜치 합의 후 나머지 정리** 필요.

### 4.5 ADR-0025 미해결 설계 이슈 (Minor)
- Issue 6: 명확화 emit 노드가 둘(`emit_clarification`/`emit_agentic_clarify`) — 통합 여지.
- Issue 7: `EntityResolver` 무음 실패(silent failure).
- Issue 8: 다중 관찰(multi-observation) 근거 선택기 — 장기 개선.

### 4.6 로컬 자산
`Models/`(대용량 가중치, `.gitignore`), 제안서 `proposal/`·루트 pptx(untracked 작업파일, 커밋 제외).

---

## 5. 이번 인수인계 준비에서 수행한 작업

브랜치 `판단-검색-에이전트`에 논리적 커밋(push: origin):

1. `chore`: 저장소 위생 — `.gitattributes`(LF 정규화), `.gitignore` 정비(캐시/워크트리/잠금파일 제외), 정크 캐시 정리.
2. `refactor`: 미사용 임포트 22개 제거(ruff F401, 동작 무변경). **검증: 496 passed / 32 failed 유지(회귀 0).**
3. `docs`: `apps/docs`를 한글 가독성 세트(README+00~07)로 전면 재작성, 루트 `README.md`/`HANDOVER.md` 신규. ADR-0018~0025는 결정 기록이라 원본 보존.

---

## 6. 외부 의존·연락

- FUR 요구사항 진행은 **NTIS센터·IRIS 협의** 전제.
- 합격기준 수치·데이터 연계 범위는 협의 후 확정.
