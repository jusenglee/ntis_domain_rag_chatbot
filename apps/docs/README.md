# NTIS RAG 시스템 문서 인덱스 (Documentation Index)

> **2026-05-22 갱신**: [ADR-0020](./ADR/ADR-0020_Tooling_Catalog_Expansion.md) 도구 카탈로그
> 확장. ADR-0019의 7-agent core 위에 19 phase에 걸쳐 추가된 도구·필터·캐싱·진단을 단일
> 진실원으로 정리. production 기준은 **7-agent core (ADR-0019) + 확장 카탈로그 (ADR-0020)**.
>
> 핵심 변경 요약:
> - **DialogueKind 10종** (ask_search/ask_detail/ask_meta/ask_children/ask_similar +
>   refine_previous/compare/stats/direct_answer/clarification)
> - **SearchStrategy 4종** (`detail_anchor` 제거; exact_lookup/subject_anchor/hybrid_search/aggregate)
> - **단축 노드 5종** (emit_direct_answer/clarification/meta_answer/children_list/internal_error)
> - **FilterBundle** = year + lead/participant_org + perf_type + coparticipants(AND) + exclude_*(NOT)
> - **stats 집계 6 축** (year/lead_org/tag/perf_type/participant_org/participant_person)
> - **결정적 추출 fallback 6종** (LLM 안전망)
> - **FocusedDetailSlot evidence 캐싱** (P0-B) — 동일 식별자 재조회 0ms
> - **response.diagnostics** — 매 turn 도구 분기·캐시·세션 상태 단일 dict 노출
>
> 이전 ADR-0018(JudgmentAgent / SearchAgent / FinalGuard 3계층)는 진화의 출발점으로만 참고.

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
  - [ADR-0019](./ADR/ADR-0019_Seven_Agent_Agentic_Redesign.md) — 7-agent core
  - [ADR-0020](./ADR/ADR-0020_Tooling_Catalog_Expansion.md) — **현재 진실원: 19 phase 도구 카탈로그 확장**
* [reference/](./reference/) — 레거시 문서
* [reports/](./reports/) — 분석 리포트

---

## 💡 핵심 키워드 (ADR-0020 시점)

### 7-agent (순서) — ADR-0019
* **DialogueAgent** — 사용자 의도 분류 + **결정적 추출 fallback 6종** (manifest_rank/focused_detail anaphora/refine_promotion/sort_by/year_window/length_hint).
* **EntityResolverAgent** — manifest_rank / focused_detail anaphora / manifest_filter / 식별자 ASCII 검증 / rst_id→perf 강제.
* **SearchPlannerAgent** — `SearchPlan` 생성 (`SearchTask` × N + merge_strategy + aggregate_by + sort_by).
* **RetrievalAgent** — 병렬 실행 + **cache hit 시 SearchAgent skip** (P0-B).
* **EvidenceCuratorAgent** — view 결정 + display_rank 고정 + **activity_summary 통계**.
* **AnswerAgent** — view별 prompt + **사용자 조건 reflection** + **length_hint** + LLM stream.
* **CriticAgent** — citation 정합 + ReferenceManifest 발행 + repair/clarify/error.

### 도구 (DialogueKind 10종) — ADR-0020
`ask_search` / `ask_detail` / **`ask_meta`** / **`ask_children`** / **`ask_similar`** / `refine_previous` / `compare` / `stats` / `direct_answer` / `clarification`

### 단축 노드 5종 — ADR-0020
`emit_direct_answer` / `emit_clarification` / **`emit_meta_answer`** / **`emit_children_list`** / `emit_internal_error`

### 검색·필터·정렬 — ADR-0020
* **SearchStrategy 4종**: exact_lookup / subject_anchor / hybrid_search / aggregate (`detail_anchor` 제거)
* **FilterBundle**: year + lead/participant_org + perf_type + **coparticipants(AND)** + **exclude_*(NOT)**
* **stats 집계 6 축**: year / lead_org / tag / perf_type / **participant_org** / **participant_person**
* **SortBy 3 모드**: relevance / recent_desc / recent_asc + LLM 정직성 가드
* **Length Hint 3 모드**: brief / default / detailed

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
