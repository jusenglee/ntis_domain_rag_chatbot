# NTIS RAG 시스템 문서 인덱스 (Documentation Index)

> **2026-05-18 갱신**: [ADR-0018](./ADR/ADR-0018_Three_Layer_Authority_Separation.md) 권한분리
> 3계층 아키텍처 적용. 현재 production 기준은 **JudgmentAgent / SearchAgent / FinalGuard 3계층
> + SearchTask 단일 캐리어**이다. 이전의 `IntentContract / Planner stagewise / ExecutionManager`
> 기반 모델은 폐기되었다.

---

## 📖 문서 가이드

| 번호 | 문서명 | 상태 | 주요 내용 | 주 독자 |
|---|---|---|---|---|
| **00** | [온보딩 (Onboarding)](./00_ONBOARDING.md) | ✅ ADR-0018 | 시스템 한 줄 요약, 용어, 코드/로그 읽기 순서 | 신규 개발자, PM |
| **01** | [아키텍처와 흐름](./01_ARCHITECTURE.md) | ✅ ADR-0018 | 권한분리 3계층, L1 캐리어, 실행 흐름 | 개발자, 아키텍트 |
| **02** | [실행 계약과 전략 규칙](./02_CONTRACTS_AND_RULES.md) | ✅ ADR-0018 | SearchTask, 식별자 의미, FinalGuard 검증 | 개발자, QA |
| **03** | [행동 안전 (L2)](./03_BEHAVIORAL_SAFETY.md) | ⛔ Archived | `ExecutionManager` 기반 정책 (폐기) | 역사 추적용 |
| **04** | [도구화 표준](./04_TOOLING_STANDARDS.md) | ⛔ Archived | Agent tool backend 규약 (폐기) | 역사 추적용 |
| **05** | [API 응답 명세](./05_API_응답명세.md) | ✅ ADR-0018 | `/query`, `/query/stream`, `/health` 응답 명세 | 프런트엔드, 연동 |
| **06** | [운영과 환경](./06_운영과_환경.md) | ✅ ADR-0018 | 부팅·로그 triage·환경 변수·이슈 대응 | 운영, SRE |
| **07** | [회귀 기준과 점검](./07_회귀기준과_점검.md) | ✅ ADR-0018 | 4건 회귀 + 6건 follow-up 시나리오, 65 passed gate | QA, 개발자 |
| **08** | [리팩토링 로드맵](./08_단계적_리팩토링_로드맵.md) | ⛔ Archived | ADR-0018로 흡수됨 | 역사 추적용 |

---

## 🛠️ 참고 자료

* [ADR/](./ADR/) — 아키텍처 결정 기록
  - [ADR-0001](./ADR/ADR-0001_LLM-First%20Dialogue%20Agent%20over%20Contract-Guarded%20RAG%20Tools.md) — LLM-first Dialogue Agent 원형
  - [ADR-0016](./ADR/ADR-0016_Agent_Contract_Shock_Absorber.md) — Shock Absorber 원칙 (`SearchTask`에 흡수됨)
  - [ADR-0017](./ADR/ADR-0017_Answer_Rank_Remapped_Reference_Manifest.md) — published_rank manifest (FinalGuard 구현)
  - [ADR-0018](./ADR/ADR-0018_Three_Layer_Authority_Separation.md) — **현재 진실원**
* [reference/](./reference/) — 레거시 문서
* [reports/](./reports/) — 분석 리포트
  - [ADR-0018 레거시 제거 매니페스트](./reports/ADR-0018_Legacy_Cleanup_Manifest.md) — 폐기된 모듈 목록

---

## 💡 핵심 키워드 (ADR-0018 이후)

* **JudgmentAgent** — "사용자가 무엇을 원했는가" 결정. rule-based + LLM JSON 출력.
* **SearchAgent** — SearchTask만 받아 Qdrant 조회 후 SearchResult 반환.
* **FinalGuard** — groundedness + published_rank manifest 검증 + 발행 결정.
* **SearchTask** — L1 의도를 텍스트로 평탄화하지 않고 끝까지 들고 가는 단일 캐리어.
* **CanonicalEvidence** — raw payload를 정규화한 prompt-safe 단위.
* **ReferenceManifest** — published_rank ↔ source_snapshot_rank 매핑 (ADR-0017).
* **Strategy** — `exact_lookup` / `subject_anchor` / `hybrid_search` / `detail_anchor` 4 분기.

### 더 이상 사용하지 않는 키워드

`IntentContract`, `IntentPayloadV3`, `QuestionAnalysisV3`, `Planner stagewise`,
`ExecutionManager`, `Shock Absorber 정책`, `Smart Coercion`, `Internal Error Loop`,
`Atomic Tool`, `Agent dialogue router`, `RAG_LOOKUP_FILTER_POLICY`,
`drift detection`, `prefix_subset` 정책, `merge_answers`.
