# 문서 인덱스 (apps/docs)

NTIS Domain RAG Chatbot의 **정본(Source of Truth) 문서 모음**이다. 코드와 일치하지 않는 방향의 변경은 이 문서들과 먼저 맞춘다.

처음 인수받았다면 저장소 루트의 **[`HANDOVER.md`](../../HANDOVER.md)** 를 먼저 읽고, 그다음 아래 순서로 본다.

| # | 문서 | 내용 | 주 독자 |
|---|---|---|---|
| 00 | [온보딩](./00_ONBOARDING.md) | 한 줄 요약, 진입점, 코드 읽기 순서, 디버깅 순서 | 전원 |
| 01 | [아키텍처](./01_ARCHITECTURE.md) | 2층 계약(L1/L2), 4계층 구조, 패키지 지도, 요청 흐름 | 개발자 |
| 02 | [계약과 도메인 규칙](./02_CONTRACTS_AND_RULES.md) | `pjt_id`/`pjt_no`, 기관 역할, follow-up·detail 계약, groundedness | 개발자, QA |
| 03 | [행동 안전(L2)](./03_BEHAVIORAL_SAFETY.md) | 복구 정책, Detail Guard, Internal Error Loop | 개발자, 운영 |
| 04 | [API와 스트리밍](./04_API_AND_STREAMING.md) | `/query/stream` 이벤트, 듀얼 답변(Solar/Gemma), 종료 메타 | 프런트엔드, 연동 |
| 05 | [운영과 환경](./05_OPERATIONS.md) | 실행 방법, 환경변수, 외부 엔드포인트, 로그 triage | 운영, SRE |
| 06 | [테스트와 회귀](./06_TESTING.md) | 검증 게이트, 회귀 불변식, 현재 테스트 트리 상태 | QA, 개발자 |

**ADR/** — 아키텍처 결정 기록(원본 보존). 핵심 결정의 "왜"가 필요하면 참조.
- `ADR-0001` LLM-First Dialogue Agent (대화 통제를 결정적 파이프라인 → LLM 에이전트로 이동)
- `ADR-0016` Agent Contract Shock Absorber (L1 의도 보호 + Smart Coercion)
- `ADR-0017` Answer-Rank Remapped Reference Manifest (사용자 표시 출처번호 ↔ 검색 rank 분리)

---

## 핵심 용어 한눈에

| 용어 | 의미 |
|---|---|
| **L1 (의도 진실)** | Dialogue Agent가 확정한 전략적 의도·대상·식별자 축. 하위 계층이 절대 못 바꾼다. |
| **L2 (실행 계약)** | Planner가 컴파일한 기술 파라미터(필터·검색어·query plan) + Orchestrator의 보정 정책. |
| **Shock Absorber** | LLM의 사소한 부수 파라미터 오류만 흡수하고 L1 진실은 보존하는 완충 원칙. |
| **Smart Coercion** | `limit`/`display_limit` 같은 표시·개수 파라미터만 좁게 교정하는 규칙. |
| **SEARCH/LOOKUP/JOIN** | 검색 모드. 벡터 검색 / ID 기반 조회 / 관계 조인. retrieval 전략의 최상위 계약. |
| **canonical evidence** | raw 검색 결과를 프롬프트에 안전한 근거로 정규화한 결과. raw payload 직접 주입 금지. |
| **Detail Guard** | detail 답변 전에 대상이 정확히 하나인지 확인하는 가드(단일 후보 미확정 시 broad search 금지). |
| **Internal Error Loop** | 도구·플래너 내부 오류를 사용자 모호성으로 위장하지 않고 1회 자기교정 후 종료하는 루프. |
