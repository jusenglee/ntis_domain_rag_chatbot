# NTIS RAG 시스템 문서 인덱스 (Documentation Index)

이 디렉터리는 NTIS RAG 서비스의 아키텍처, 실행 규칙, 운영 가이드를 담고 있는 **운영 진실원(Source of Truth)** 문서 모음입니다.

---

## 📖 문서 가이드

| 번호 | 문서명 | 주요 내용 | 주 독자 |
|---|---|---|---|
| **00** | [온보딩 (Onboarding)](./00_ONBOARDING.md) | 시스템 한 줄 요약, 용어 정리, 코드 읽기 순서 | 신규 개발자, PM |
| **01** | [아키텍처와 흐름](./01_ARCHITECTURE.md) | 2층 계약(L1/L2) 철학, 4계층 구조, 데이터 흐름 | 개발자, 아키텍트 |
| **02** | [실행 계약과 전략 규칙](./02_CONTRACTS_AND_RULES.md) | L1 의도 정의, 식별자(pjt_id/no) 계약, Follow-up 규칙 | 개발자, QA |
| **03** | [행동 안전 (L2)](./03_BEHAVIORAL_SAFETY.md) | Orchestrator 보정 정책, 예외 처리 및 회복 규칙 | 개발자, 운영 |
| **04** | [도구화 표준](./04_TOOLING_STANDARDS.md) | Atomic Tool 구현 규격, 상태 격리 원칙 | 개발자 |
| **05** | [API 응답 명세](./05_API_응답명세.md) | `/query/stream` 등 API 엔드포인트 및 페이로드 규격 | 프런트엔드, 연동 |
| **06** | [운영과 환경](./06_운영과_환경.md) | 로그 트리아지, 검증 명령, 환경 설정 | 운영, SRE |
| **07** | [회귀 기준과 점검](./07_회귀기준과_점검.md) | 골든 테스트 케이스, 반드시 지킬 회귀 계약 | QA, 개발자 |
| **08** | [리팩토링 로드맵](./08_단계적_리팩토링_로드맵.md) | 모듈 해체 및 고도화 계획 | 개발자, PM |

---

## 🛠️ 참고 자료

*   [ADR/](./ADR/): 아키텍처 결정 기록 (Architecture Decision Records)
*   [reference/](./reference/): 레거시 문서 및 참고 데이터
*   [reports/](./reports/): 시스템 분석 리포트

---

## 💡 핵심 키워드

*   **L1 (Intent Truth):** "사용자가 무엇을 원하는가?" (의도의 진실)
*   **L2 (Behavioral Safety):** "시스템은 어떻게 안전하게 실행하는가?" (행동의 안전)
*   **Atomic Tool:** 상태가 없는(Stateless) 순수 기능 조회 모듈.
