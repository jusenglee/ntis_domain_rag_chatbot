# NTIS RAG 시스템 문서 인덱스 (Documentation Index)

이 디렉터리는 NTIS RAG 서비스의 아키텍처, 실행 규칙, 운영 가이드를 담고 있는 **운영 진실원(Source of Truth)** 문서 모음입니다.
현재 production 기준은 **LLM Dialogue Agent가 대화 흐름을 판단하고, Tool Backend / Planner / Retrieval / Answer Publication Guard가 계약으로 실행을 통제하는 Agentic RAG**입니다.

---

## 📖 문서 가이드

| 번호 | 문서명 | 주요 내용 | 주 독자 |
|---|---|---|---|
| **00** | [온보딩 (Onboarding)](./00_ONBOARDING.md) | Agentic RAG 한 줄 모델, 용어, 코드/로그 읽기 순서 | 신규 개발자, PM |
| **01** | [아키텍처와 흐름](./01_ARCHITECTURE.md) | 2층 계약(L1/L2), Shock Absorber 원칙, Agentic 실행 흐름 | 개발자, 아키텍트 |
| **02** | [실행 계약과 전략 규칙](./02_CONTRACTS_AND_RULES.md) | L1 의도, Smart Coercion 범위, detail/follow-up 계약 | 개발자, QA |
| **03** | [행동 안전 (L2)](./03_BEHAVIORAL_SAFETY.md) | Bounded recovery, Internal Error Loop, SEARCH_RECOVERY 금지 조건 | 개발자, 운영 |
| **04** | [도구화 표준](./04_TOOLING_STANDARDS.md) | Agent-facing tool 경계, query materialization, 상태 격리 | 개발자 |
| **05** | [API 응답 명세](./05_API_응답명세.md) | `/query/stream` 이벤트, contract-invalid/provider failure 응답 | 프런트엔드, 연동 |
| **06** | [운영과 환경](./06_운영과_환경.md) | 로그 triage, 정상 개발환경 경고, 검증 명령 | 운영, SRE |
| **07** | [회귀 기준과 점검](./07_회귀기준과_점검.md) | 골든 테스트, detail/follow-up 회귀 계약 | QA, 개발자 |
| **08** | [리팩토링 로드맵](./08_단계적_리팩토링_로드맵.md) | 문서 드리프트 이후 코드 리팩토링 단계 | 개발자, PM |

---

## 🛠️ 참고 자료

*   [ADR/](./ADR/): 아키텍처 결정 기록 (Architecture Decision Records)
*   [reference/](./reference/): 레거시 문서 및 참고 데이터
*   [reports/](./reports/): 시스템 분석 리포트

---

## 💡 핵심 키워드

*   **L1 (Intent Truth):** "사용자가 무엇을 원하는가?" (Dialogue Agent가 확정한 대화 의도, 대상, 식별자 축의 진실)
*   **L2 (Technical Fact):** "의도를 어떻게 기술적으로 달성하는가?" (Planner가 컴파일한 필터, 검색어와 Orchestrator의 보정 정책)
*   **Shock Absorber:** LLM의 사소한 부수 파라미터 오류를 L1 의도에 맞게 흡수하되, L1 진실은 절대 고치지 않는 완충 원칙.
*   **Smart Coercion:** `limit`, `display_limit` 같은 표시/개수 파라미터만 좁게 교정하는 규칙.
*   **Internal Error Loop:** tool backend/planner 오류를 사용자 모호성으로 위장하지 않고 Agent에게 최대 1회 compact observation으로 되돌리는 자기 교정 루프.
*   **Atomic Tool:** 상태가 없는(Stateless) 순수 기능 조회 모듈.
