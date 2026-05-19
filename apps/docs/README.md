# NTIS RAG 시스템 문서 인덱스 (Documentation Index)

> **2026-05-19 갱신**: [ADR-0019](./ADR/ADR-0019_Seven_Agent_Agentic_Redesign.md) 7-agent
> 재설계 적용. 현재 production 기준은 **DialogueAgent → EntityResolverAgent →
> SearchPlannerAgent → RetrievalAgent → EvidenceCuratorAgent → AnswerAgent →
> CriticAgent (7-agent 권한분리 + frozen Pydantic 계약)** 이다.
>
> 이전 ADR-0018(JudgmentAgent / SearchAgent / FinalGuard 3계층 + SearchTask 단일 캐리어)는
> 진화의 출발점으로만 참고하라. JudgmentAgent / FinalGuard / LLMGenerator / `apps/pipeline/workflow.py` /
> `apps/pipeline/state.py` 5개 모듈은 ADR-0019에서 git rm 폐기되었다.

---

## 📖 문서 가이드

| 번호 | 문서명 | 상태 | 주요 내용 | 주 독자 |
|---|---|---|---|---|
| **00** | [온보딩 (Onboarding)](./00_ONBOARDING.md) | ⚠️ ADR-0019 갱신 필요 | 시스템 한 줄 요약, 용어, 코드/로그 읽기 순서 | 신규 개발자, PM |
| **01** | [아키텍처와 흐름](./01_ARCHITECTURE.md) | ⚠️ ADR-0019 갱신 필요 | 7-agent 권한분리, SessionState 3 평행 슬롯, 실행 흐름 | 개발자, 아키텍트 |
| **02** | [실행 계약과 전략 규칙](./02_CONTRACTS_AND_RULES.md) | ⚠️ ADR-0019 갱신 필요 | 6개 frozen 계약, 식별자 의미, CriticAgent 검증 | 개발자, QA |
| **03** | [행동 안전 (L2)](./03_BEHAVIORAL_SAFETY.md) | ⛔ Archived | `ExecutionManager` 기반 정책 (폐기) | 역사 추적용 |
| **04** | [도구화 표준](./04_TOOLING_STANDARDS.md) | ⛔ Archived | Agent tool backend 규약 (폐기) | 역사 추적용 |
| **05** | [API 응답 명세](./05_API_응답명세.md) | ⚠️ ADR-0019 갱신 필요 | `/query`, `/query/stream`, `/health` 응답 명세 (`dialogue_intent`, `guard_decision` 포함) | 프런트엔드, 연동 |
| **06** | [운영과 환경](./06_운영과_환경.md) | ✅ 유효 | 부팅·로그 triage·환경 변수·이슈 대응 | 운영, SRE |
| **07** | [회귀 기준과 점검](./07_회귀기준과_점검.md) | ⚠️ ADR-0019 갱신 필요 | Golden + Observability + Regression 230 passed gate | QA, 개발자 |
| **08** | [리팩토링 로드맵](./08_단계적_리팩토링_로드맵.md) | ⛔ Archived | ADR-0018→0019로 흡수됨 | 역사 추적용 |

---

## 🛠️ 참고 자료

* [ADR/](./ADR/) — 아키텍처 결정 기록
  - [ADR-0001](./ADR/ADR-0001_LLM-First%20Dialogue%20Agent%20over%20Contract-Guarded%20RAG%20Tools.md) — LLM-first Dialogue Agent 원형
  - [ADR-0016](./ADR/ADR-0016_Agent_Contract_Shock_Absorber.md) — Shock Absorber 원칙 (계약에 흡수됨)
  - [ADR-0017](./ADR/ADR-0017_Answer_Rank_Remapped_Reference_Manifest.md) — published_rank manifest (CriticAgent 구현)
  - [ADR-0018](./ADR/ADR-0018_Three_Layer_Authority_Separation.md) — 3계층 권한분리 (ADR-0019의 토대)
  - [ADR-0019](./ADR/ADR-0019_Seven_Agent_Agentic_Redesign.md) — **현재 진실원: 7-agent 재설계**
* [reference/](./reference/) — 레거시 문서
* [reports/](./reports/) — 분석 리포트

---

## 💡 핵심 키워드 (ADR-0019 이후)

### 7-agent (순서)
* **DialogueAgent** — 사용자 의도 분류만 (LLM 1회 호출 → `DialogueIntent` JSON).
* **EntityResolverAgent** — 결정적 식별자 해소 (manifest_rank, rst_id→perf 강제, ASCII 식별자 형식 검증).
* **SearchPlannerAgent** — `SearchPlan` 생성 (1개 이상의 `SearchTask` + merge_strategy).
* **RetrievalAgent** — `SearchPlan` 병렬 실행 (`asyncio.gather`) + merge + dedup.
* **EvidenceCuratorAgent** — view 결정(single_detail/subject_activity/list_compact/stats_summary/comparison_table/empty) + display_rank 최종 고정.
* **AnswerAgent** — view별 prompt 분기 + LLM 답변 stream + `[N]` 인용 파싱.
* **CriticAgent** — citation 정합 검증 + ReferenceManifest 발행 + repair/clarify/error 결정 (FinalGuard 후속).

### 계약 (모두 frozen Pydantic, `extra="forbid"`)
* `DialogueIntent`, `EntityResolution`, `SearchPlan`, `EvidenceBundle`, `AnswerDraft`, `GuardDecision`
* 기존: `SearchTask`, `CanonicalEvidence`, `SearchResult`, `IdentifierBundle`, `SubjectAnchor`,
  `FilterBundle`, `ReferenceItem`, `ReferenceManifest` (재사용)

### 세션 메모리
* **`SessionState`** (frozen) — 3 평행 슬롯:
  - `current_subject` (SubjectSlot) — 사람/기관 anchor
  - `published_manifest` (ManifestSlot) — 직전 turn N건
  - `focused_detail` (FocusedDetailSlot) — 가장 최근 본 단일 대상
* **`SessionStateAdapter`** — 기존 `SessionMemory` ↔ `SessionState` 변환

### LangGraph 워크플로우
* `apps.pipeline.build_agent_pipeline_graph(deps)` — 12 노드 + 5 라우팅
* 재시도: critic `repair_answer` → answer 1회 재호출 (state.repair_attempted 플래그)

### 더 이상 사용하지 않는 키워드 (ADR-0019에서 폐기)

`JudgmentAgent`, `JudgmentDecision`, `DirectAnswer`, `Clarification`(contracts.py 모델),
`FinalGuard`, `FinalAnswer`, `LLMGenerator`, `GeneratedAnswer`,
`apps.pipeline.workflow`, `apps.pipeline.state`, `PipelineState`, `PipelineDeps`,
`build_pipeline_graph`.

ADR-0018 이전의 폐기 키워드(`IntentContract`, `Planner stagewise`, `ExecutionManager`,
`Shock Absorber 정책`, `Smart Coercion`, `Atomic Tool`, `RAG_LOOKUP_FILTER_POLICY`,
`prefix_subset`, `merge_answers`)도 그대로 폐기 상태 유지.
