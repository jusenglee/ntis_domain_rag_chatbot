# NTIS Domain RAG Chatbot

NTIS(국가과학기술지식정보서비스) 데이터를 기반으로 한 전문 도메인 RAG 챗봇 시스템입니다.

---

## 🚀 빠른 시작 (Quick Start)

시스템에 처음 오셨나요? 아래 문서를 먼저 확인해주세요.

👉 **[신규 개발자 온보딩 가이드 (Onboarding)](./apps/docs/00_ONBOARDING.md)**

---

## 🏗️ 아키텍처 개요 (Architecture)

본 시스템은 **"2층 계약(2-Layer Contract)"** 아키텍처를 따릅니다.

1.  **L1 (Intent Truth):** 사용자 질문에서 변하지 않는 의도와 식별자를 추출합니다.
2.  **L2 (Behavioral Safety):** 추출된 의도를 바탕으로 안전하고 유연하게 도구를 조율하여 실행합니다.

상세한 구조는 **[아키텍처 문서](./apps/docs/01_ARCHITECTURE.md)**를 참고하세요.

---

## 📂 문서 지도 (Documentation Map)

모든 공식 문서는 **[`apps/docs/`](./apps/docs/README.md)** 폴더에 통합되어 관리됩니다.

*   **[온보딩](./apps/docs/00_ONBOARDING.md)**: 시스템 빠른 이해
*   **[아키텍처](./apps/docs/01_ARCHITECTURE.md)**: 4계층 구조 및 흐름
*   **[실행 규칙](./apps/docs/02_CONTRACTS_AND_RULES.md)**: 식별자 및 Follow-up 계약
*   **[API 명세](./apps/docs/05_API_응답명세.md)**: 인터페이스 규격
*   **[운영 가이드](./apps/docs/06_운영과_환경.md)**: 로그 및 디버깅

---

## 🤖 AI 에이전트 가이드

이 저장소에서 작업하는 AI 에이전트는 **[`AGENTS.md`](./AGENTS.md)**의 지침을 반드시 준수해야 합니다.
