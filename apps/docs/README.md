# 문서 인덱스 (apps/docs) — 판단-검색-에이전트 브랜치

이 브랜치(`판단-검색-에이전트`)의 **정본 문서 모음**이다. 이 브랜치는 `apps/pipeline` 기반의 **자율 에이전트 챗봇**으로, NTIS 벡터 검색은 에이전트가 *필요할 때 부르는 도구 하나*(MCP 서버 같은 backplane)일 뿐이다.

처음 인수받았다면 저장소 루트의 **[`HANDOVER.md`](../../HANDOVER.md)** 를 먼저 읽고, 그다음 아래 순서로 본다.

| # | 문서 | 내용 | 주 독자 |
|---|---|---|---|
| 00 | [온보딩](./00_ONBOARDING.md) | 이게 무슨 시스템인가, 실제 예시, 진입점, 멘탈모델 | 전원 |
| 01 | [아키텍처](./01_ARCHITECTURE.md) | agent-first, 3계층 권한분리, 단일 agentic 파이프라인 | 개발자 |
| 02 | [계약과 규칙](./02_CONTRACTS_AND_RULES.md) | SearchTask·식별자·필터·evidence·세션 계약 | 개발자, QA |
| 03 | [에이전트 런타임](./03_AGENTIC_RUNTIME.md) | **플래너 루프**(최대 8스텝)·도구 실행·Critic·듀얼답변 | 개발자 |
| 04 | [NTIS 도구 backplane](./04_NTIS_TOOL_BACKPLANE.md) | 9개 도구, SearchRouter, NTIS를 안 쓰는 경우 | 개발자 |
| 05 | [API와 스트리밍](./05_API_AND_STREAMING.md) | `/query`·`/query/stream`, 듀얼답변 레인(A/B) | 프런트엔드, 연동 |
| 06 | [운영과 환경](./06_OPERATIONS_AND_ENV.md) | 토글, 모델 역할, KV, 로그, 의존성 주의 | 운영, SRE |
| 07 | [검증과 리뷰](./07_VALIDATION_AND_REVIEW.md) | 테스트 게이트, 현재 테스트 트리 상태, 리뷰 체크리스트 | QA, 개발자 |

**ADR/** — 아키텍처 결정 기록(원본 보존, 영문). 이 브랜치 구조의 "왜"가 필요하면 참조.
- `0018` 3계층 권한분리 · `0019` agentic 챗봇 + NTIS backplane · `0020` **단일 agentic 파이프라인**(정적 그래프 제거)
- `0021` 듀얼모델 답변(Solar A / Gemma B) · `0022` SearchRouter(DB 스키마 은닉) · `0023` AdequacyGate 제거(Planner가 유일 판정자)
- `0024` 발행 계층 분리(answer_curator=evidence-only) · `0025` 설계 이슈 등록부

> ⚠️ 이 브랜치는 다른 브랜치(`고도화` 등)와 코드·문서 구조가 다르다. 브랜치 정본 합의 전까지 혼동 주의. → `HANDOVER.md`

## 핵심 용어 한눈에

| 용어 | 의미 |
|---|---|
| **agent-first** | 챗봇이 대화를 주도. RAG는 매 턴 필수가 아니라 *필요할 때 부르는 도구*. |
| **planner_loop** | LLM 플래너가 "도구 더 부를까/답할까/되물을까"를 **반복 결정**(최대 8스텝). 이 브랜치를 "진짜 에이전트"로 만드는 핵심. |
| **3계층 권한분리** | ①판단(Dialogue·Planner) ②근거(ToolExecutor·SearchAgent) ③발행(Answer·Critic). 서로 침범 금지. |
| **SearchTask** | SearchAgent가 실행하는 유일한 검색 계약(action·target·filters 등). |
| **CanonicalEvidence** | 답변에 들어가는 유일한 근거 단위. raw Qdrant payload 직접 주입 금지. |
| **듀얼 답변** | 같은 근거로 Solar(A, 메인/검증)·Gemma(B, 비교/raw) 두 답을 낸다. |
