# 인수인계 문서 (HANDOVER)

> **대상**: 본 프로젝트(NTIS Domain RAG Chatbot)를 인수하여 유지보수·개발할 새 담당자/회사
> **목적**: 코드를 모르는 상태에서도 온보딩·운영·개발을 막힘없이 시작하도록, 진입점·현재 상태·즉시 처리 이슈를 한 곳에 모은다.
> **기준**: 2026-06-22 · 브랜치 `고도화`

이 문서는 "어디서부터 보는가 + 지금 무엇이 위험한가"만 담는다. 설계·규칙의 정본은 **[`apps/docs/`](./apps/docs/README.md)** 다.

---

## 0. 60초 요약

- **무엇**: NTIS 국가 R&D 데이터를 **자연어로 묻고 출처와 함께 답받는** 챗봇. 예) "신동구 연구자(KISTI)의 2014~2020년 활동 내역" → 참여 과제를 연도별로, 각 항목에 `[1]` 출처번호를 달아 답하고, "그럼 2020년 이후는?" 이어 묻기도 된다. (BM25+벡터 하이브리드 검색 + LangGraph 다중 에이전트)
- **설계 초점**: ①환각 방지(근거에 없으면 답 보류) ②엉뚱한 대상 섞기 방지(동명이인·과제번호·기관역할 함정). 코드 복잡도의 대부분이 이 둘 때문이다.
- **구조**: LLM **Dialogue Agent**가 "무엇을 원하는지"를 정하면(L1), 하위 단계는 그걸 *안 바꾸고* 검색·실행만 한다(L2). → [01 아키텍처](./apps/docs/01_ARCHITECTURE.md)
- **서버**: FastAPI + uvicorn, 진입점 `apps/api/main.py`(포트 **8008**).
- **즉시 볼 이슈**: §5. 그중 **의존성·실행 매니페스트 부재**와 **외부 엔드포인트 하드코딩**이 인프라 이전의 1차 관문.

---

## 1. 온보딩 순서

1. 이 문서(§2 시스템, §5 이슈)
2. [`apps/docs/00_ONBOARDING.md`](./apps/docs/00_ONBOARDING.md) — 진입점·코드 읽기 순서·디버깅 순서
3. [`apps/docs/01_ARCHITECTURE.md`](./apps/docs/01_ARCHITECTURE.md) — 2층 계약/4계층 구조
4. [`apps/docs/02_CONTRACTS_AND_RULES.md`](./apps/docs/02_CONTRACTS_AND_RULES.md) — **도메인 제약(필독)**
5. [`AGENTS.md`](./AGENTS.md) — 코드 작업 원칙

---

## 2. 시스템 구성

| 구성 | 내용 |
|---|---|
| API | FastAPI (`apps/api/`), 진입점 `apps/api/main.py`(8008), 조립 `app_factory.py:create_app()` |
| 오케스트레이션 | LangGraph `build_request_workflow()` (`apps/api/workflow_builder.py`) — Dialogue Agent → (tool) Planner → Retrieval → Evidence → Answer(Solar/Gemma) → 발행 가드 |
| 벡터 DB | Qdrant (과제/성과/지원 컬렉션) |
| LLM | Solar(vLLM, OpenAI 호환) + Gemma(Triton) **듀얼 답변** |
| 세션 | Redis (실패 시 파일 KV 폴백) |
| 설정 정본 | `apps/platform/settings.py` (환경변수 기반) |

엔드포인트 기본값과 환경변수 표는 [05 운영과 환경](./apps/docs/05_OPERATIONS.md).

---

## 3. 데이터 모델 (도메인 핵심)

국가 R&D 데이터는 **과제(Project)** 와 **성과(Output)** 로 나뉘고, 성과는 수행 과제에서 파생된다.

- **성과 10종**: 논문 · 특허 · 소프트웨어 · 신품종 · 생명정보 · 생물자원 · 화합물 · 연구보고서 · 시설장비 · 기술요약. 매퍼는 `apps/api/rag_mapper/domains/`.
- **참여 주체 3종(혼용 금지)**: 수행기관 / 참여기관 / 참여인력 소속기관.
- ⚠️ 제약: `pjt_id` ≠ `pjt_no`, 기관 역할 3종 구분, raw payload 직접 주입 금지(canonical_evidence 경유). 상세 → [02 계약과 도메인 규칙](./apps/docs/02_CONTRACTS_AND_RULES.md).

---

## 4. 코드/문서 진입점

- 코드 읽기 순서·기준 파일 지도 → [00 온보딩](./apps/docs/00_ONBOARDING.md)
- 핵심 진입점: `apps/api/main.py` → `app_factory.py` → `routes.py`(`/query/stream`) → `workflow_builder.py` → `agent_dialogue_router.py` → `agent_tool_executor.py` → `planner_runtime.py` → `retrieval_workflow.py`/`execution_manager.py` → `canonical_evidence.py` → `answer_generation.py`.

---

## 5. ⚠️ 인수 시 즉시 처리 필요 (KNOWN ISSUES)

### 5.1 의존성·실행 매니페스트 부재 [최우선]
루트에 `requirements.txt`/`pyproject.toml`/`Dockerfile`/`Makefile`이 **없다**. 설치·실행 절차가 코드 외부에 문서화되어 있지 않다. 현재 검증 환경은 conda env `ntis_domain_rag_chatbot`(Python 3.12). **의존성 고정 파일과 실행 절차 문서화가 시급**(운영 담당 인터뷰로 확인 필요).

### 5.2 외부 엔드포인트 하드코딩 디폴트
LLM/Qdrant/Triton/Redis/Oracle 기본값이 특정 IP(`203.250.234.159`, Oracle `203.250.234.203`)로 코드에 박혀 있다(환경변수 오버라이드 가능). 인수 인프라로 이전 시 환경변수 주입 또는 `apps/platform/settings.py` 디폴트 교체. → [05 운영과 환경](./apps/docs/05_OPERATIONS.md)

### 5.3 테스트 트리 상태
- `tests/`가 `.gitignore`에 포함되어 **버전관리되지 않는다**(로컬에만 존재). 버전관리 포함 여부 **정책 결정 필요**.
- 로컬 `tests/`에 **다른 브랜치용 테스트가 섞여** 있다: `tests/pipeline/`(23개)는 이 브랜치에 없는 `apps.pipeline`을 임포트해 수집 에러. 이를 제외하면 **38 passed**(깨끗). 상세·검증 게이트 → [06 테스트와 회귀](./apps/docs/06_TESTING.md).

### 5.4 다수 브랜치 — 정본 합의 필요
`고도화`(현재)·`main`·`misp`·`판단-검색-에이전트`·`multi-agent` 등 브랜치가 다수이며 구조가 서로 다르다(예: `판단-검색-에이전트`는 `apps/pipeline` 구조). **인수 시 어느 브랜치를 정본으로 유지·배포할지 합의**하고 나머지를 정리할 것. 현재 인계 기준은 `고도화`.

### 5.5 미완 기능 / 기술 부채
- `join_project_perf` 도구 **미구현**(`apps/conversation/agent_tools.py`에서 `implemented=False`). `lookup_specific_entity`는 구현됨.
- `support SEARCH`는 의도적으로 레거시 유지(상품 결정 필요).
- 로드맵 open 항목: Phase 12(visible/canonical 축 불일치 발행 차단·active-scope 정리), Phase 13(contract-invalid 시 LLM chunk 금지·Solar reasoning-only 타임아웃 분리).
- 이전 로드맵에 보고된 항목: `apps/chat/answer_generation.py`의 `node_merge_answers` `LLM.RESULT` 로그 호출 정리(검증 권장). *(현재 `compileall`·`create_app()` 스모크는 통과)*

### 5.6 로컬 자산
- `Models/`(대용량 모델 가중치) — `.gitignore` 포함, 로컬 디스크 점유. 배포 방식 결정 필요.
- 제안서 산출물은 `proposal/`(+ 루트 일부 `*.pptx`)에 있으며 **untracked 작업 파일**이다(저장소에 커밋되지 않음). FUR 검증 자료가 필요하면 별도 전달.

---

## 6. 이번 인수인계 준비에서 수행한 작업

논리적 커밋 3건(브랜치 `고도화`, push는 보류):

1. `chore`: 저장소 위생 — `.gitattributes`로 LF 정규화(CRLF 노이즈 18파일 정리), `.gitignore` 정비(캐시/워크트리/잠금파일 제외), 잔여 개발메모(`logs/이슈 1.md`) 추적 해제. 정크 디렉터리(`apps/.claude` 워크트리 14MB, `.pytest_cache`) 삭제.
2. `refactor`: 미사용 임포트 88개 제거(ruff F401, 동작 무변경) + `retrieval_workflow.py`의 `ExecutionOutcome` import 누락(잠재 NameError) 수정. **검증: 38 passed, 회귀 없음.**
3. `docs`: 문서 전면 재작성. 과거 이력/메타 문서(SESSION_HANDOFF 314KB, reports/, reference/, cleanup_plan 3종, 루트 docs/) 삭제. `apps/docs`를 00~06 + ADR의 lean 세트로 정리하고 루트 `README.md`/`HANDOVER.md` 재작성.

---

## 7. 외부 의존·연락

- FUR(R&D 특화 RAG 검증·인용 신뢰성 등) 요구사항 진행은 **NTIS센터·IRIS 협의** 전제(외부 의존).
- 합격기준 수치·데이터 연계 범위는 협의 후 확정.
