# docs/ 문서 안내

이 폴더는 NTIS Domain RAG Chatbot의 **운영 진실원(source-of-truth) 문서** 모음입니다.

---

## 독자별 읽기 순서

### 신규 개발자
1. `00_ONBOARDING.md` — 시스템 개요 + 어디서부터 읽을지
2. `01_아키텍처와_흐름.md` — 4개 계층 구조, 요청 흐름
3. `02_실행계약과_전략규칙.md` — 절대 깨지면 안 되는 실행 약속
4. `03_운영과_환경.md` — 환경변수, 검증 명령, 로그 구조
5. `EVIDENCE_PROMPT_PACKING.md` — evidence 조립 상세

### PM / 기획
1. `00_ONBOARDING.md` — 시스템 한 줄 요약과 핵심 용어
2. `01_아키텍처와_흐름.md` — 계층 역할과 제한 사항 (표 위주)

### 운영 / QA
1. `03_운영과_환경.md` — 검증 명령, 로그 트리아지
2. `04_회귀기준과_점검.md` — 반드시 지킬 계약, 골든 테스트 케이스

### AI 에이전트 (Codex / Claude)
1. `CODEX_CONTEXT.md` — bootstrap truth (브랜치, 검증 방식, 패키지 소유권)
2. `SESSION_HANDOFF.md` — 직전 세션 결과와 다음 권장 작업
3. `GOLDEN_TESTS.md` — 계약 검증 케이스 목록

---

## 문서 목록

| 파일 | 용도 | 주 독자 |
|---|---|---|
| `00_ONBOARDING.md` | 입구 문서, 빠른 시스템 이해 | 신규 개발자, PM |
| `01_아키텍처와_흐름.md` | 4계층 구조, 요청 흐름, artifact 소유권 | 개발자, PM |
| `02_실행계약과_전략규칙.md` | SEARCH/LOOKUP/JOIN 경계, follow-up 규칙, planner 단계 | 개발자, QA |
| `03_운영과_환경.md` | 환경변수, 검증 명령, 로그 트리아지 | 운영, 개발자 |
| `04_회귀기준과_점검.md` | 회귀 계약, 골든 테스트 케이스 | QA, 개발자 |
| `05_유지보수와_확장.md` | 패키지 소유권, 문서 동기화 규칙, 기술 부채 | 개발자 |
| `EVIDENCE_PROMPT_PACKING.md` | evidence 조립, 압축, 예산 정책 | 개발자, AI 에이전트 |
| `GOLDEN_TESTS.md` | 계약 검증 케이스 | QA, AI 에이전트 |
| `PRODUCT_BASELINE.md` | 현재 검증 게이트 상태 | 운영, AI 에이전트 |
| `CODEX_CONTEXT.md` | 에이전트 bootstrap truth | AI 에이전트 |
| `SESSION_HANDOFF.md` | 세션별 변경 이력 | AI 에이전트, 개발자 |
| `ADR/` | 아키텍처 결정 기록 | 개발자 |

---

## 갱신 규칙

- planner/runtime owner 변경 시: `03_운영과_환경.md` + `SESSION_HANDOFF.md` 동시 갱신
- contract/strategy 변경 시: `02_실행계약과_전략규칙.md` + `04_회귀기준과_점검.md` 동시 갱신
- 대형 설계 결정 시: `ADR/` 폴더에 ADR 추가 후 관련 문서 링크
- 모든 코드 패치 시: `SESSION_HANDOFF.md` append 필수
