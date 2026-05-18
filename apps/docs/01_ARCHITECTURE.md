# 01 아키텍처와 흐름 (Architecture & Flow)

> **2026-05-18 갱신**: ADR-0018 권한분리 3계층 아키텍처 적용. 본 문서는 새 `apps.pipeline`
> 패키지를 진실원으로 한다. 이전 `IntentContract` / `IntentPayloadV3` / `QuestionAnalysisV3` /
> `Planner stagewise` 등 레거시 용어는 더 이상 유효하지 않다. 변경 결정은
> [ADR-0018](./ADR/ADR-0018_Three_Layer_Authority_Separation.md) 참조.

---

## 1. 설계 철학: 권한 분리 3계층 (Authority Separation)

이 시스템은 한 가지 질문에 대해 세 개의 독립된 책임을 분리해서 실행한다.

| 계층 | 단일 책임 | 절대 하지 않는 일 |
|------|-----------|--------------------|
| **JudgmentAgent** | "사용자가 무엇을 원했는가" 결정 | 검색기를 직접 호출하지 않는다 |
| **SearchAgent** | "그 요구를 만족하는 근거가 있는가" 확인 | 사용자 의도를 재해석하지 않는다 |
| **FinalGuard** | "이 답변이 근거와 계약을 위반하지 않는가" 검증 | 검색을 다시 트리거하지 않는다 |

세 계층은 상대 계층의 내부 상태에 접근하지 않는다. 통신은 정의된 계약 객체(`SearchTask`,
`SearchResult`, `FinalAnswer`)로만 한다.

### 핵심 약속: L1 의도 캐리어 보존

이전 아키텍처에서 가장 빈번한 결함은 **L1(에이전트 의도)이 L2(검색 실행) 경계를 넘어가는 도중
텍스트 한 줄로 평탄화되어 사실상 증발**하는 것이었다. ADR-0018 이후 L1은 단일 구조화 캐리어
`SearchTask`로 박혀 SearchAgent까지 그대로 전달된다.

- `SearchTask.subject` — `SubjectAnchor`(person_no, org_id, affiliation 등) 1급 필드
- `SearchTask.identifiers` — pjt_id/pjt_no/rst_id/person_no/org_id를 텍스트 없이 보존
- `SearchTask.filters` — 연도/소속/성과타입 등 구조화 필터
- `SearchTask.strategy` — `exact_lookup` / `subject_anchor` / `hybrid_search` / `detail_anchor` 중 하나
- `SearchTask.retrieval_query` — **보조 신호**일 뿐 식별자를 평탄화하지 않는다

---

## 2. 패키지 구조 (Package Structure)

```
apps/
  pipeline/                       ── 3계층 권한분리 핵심 (ADR-0018)
    contracts.py                  ── SearchTask / SearchResult / FinalAnswer / ReferenceManifest
    state.py                      ── PipelineState (LangGraph 상태)
    judgment_agent.py             ── L1 의도 결정 (rule + LLM)
    search_agent.py               ── L1을 받아 hits 만 만든다
    final_guard.py                ── groundedness + published_rank manifest
    llm_generator.py              ── 단일 LLM 답변 생성
    session_store.py              ── pipeline:v1 슬림 세션 저장
    workflow.py                   ── LangGraph 그래프 빌더
    retrieval/                    ── Qdrant primitives
      exact_lookup.py             ── by-axis 정확 조회
      qdrant_search.py            ── hybrid (dense + filter) 검색
      canonical_normalizer.py     ── Qdrant point → CanonicalEvidence

  api/
    main.py                       ── FastAPI 진입점
    app_factory.py                ── Composition root
    runtime.py                    ── 부트스트랩 (Qdrant/LLM/KV/Graph)
    routes.py                     ── /query, /query/stream, /health, /
    streaming/                    ── SSE 인코더 + emitter

  conversation/
    session_memory.py             ── SubjectQueryContext 등 (대화 연속성용)
    view_state.py                 ── 호환 view 생성기
    entity_registry.py            ── 도메인 kind 등록 레지스트리

  evidence/canonical_evidence.py  ── raw payload → CanonicalEvidence dataclass
  retrieval/rag_store.py          ── Qdrant + 임베딩 리소스 빌더
  chat/llm_runtime.py             ── Solar(vLLM) / Gemma(Triton) 어댑터 캐시
  platform/                       ── settings/storage/clients
  prompts/                        ── 시스템 프롬프트 자산
  docs/                           ── 본 문서 및 ADR
```

`apps.planner`, `apps.retrieval`의 RAG 오케스트레이션, `apps.conversation`의 agent dialogue
router/tool executor/turn 처리 등은 ADR-0018에서 모두 폐기되었다. 새 시스템은 단순히 위
13개 신규 모듈로 같은 기능을 더 적은 코드로 구현한다.

---

## 3. 요청 실행 흐름 (Execution Flow)

```text
1. HTTP Ingress (apps/api/routes.py)
   └─ /query 또는 /query/stream 수신
   └─ PipelineState 생성: question, conversation_id, request_id, turn_id,
      kv_store, stream_emitter

2. load_session
   └─ KV에서 SessionMemory 복원 (apps/pipeline/session_store.py)
      key = "pipeline:v1:{conversation_id}:session"

3. JudgmentAgent.decide()
   ├─ Rule-based pre-pass: 코드형 식별자(pjt_no, pjt_id, EQU/SNW/PTO/REP)
   │   탐지 시 즉시 SearchTask 생성 (LLM 우회)
   ├─ LLM-based classification: 자연어 질문 → JSON → JudgmentDecision
   └─ 출력: search_task | direct_answer | clarification

4. Routing
   ├─ direct_answer → emit_direct_answer → save_session
   ├─ clarification → emit_clarification → save_session
   └─ search_task → SearchAgent

5. SearchAgent.execute(SearchTask)
   ├─ strategy=exact_lookup → pipeline/retrieval/exact_lookup.py (by-axis)
   ├─ strategy=subject_anchor → qdrant_search.py + subject nested filter
   ├─ strategy=detail_anchor → qdrant_search.py (limit=1)
   ├─ strategy=hybrid_search → qdrant_search.py (anchor 없음)
   └─ 출력: SearchResult(status ∈ {single, multiple, empty, error})

6. Routing after search
   ├─ status=error → emit_internal_error
   ├─ status=multiple AND not refine_attempted → refine_judgment (1회만)
   ├─ status=empty → generate(no_result message)
   └─ status=single → generate(LLM)

7. LLMGenerator.generate()
   └─ Triton(Gemma)로 단일 LLM 호출
   └─ evidence block을 prompt-safe 형태로 정리 후 system + user 메시지 구성
   └─ stream_emitter로 토큰 단위 SSE publish
   └─ 출력: GeneratedAnswer

8. FinalGuard.review()
   ├─ groundedness: 답변의 모든 식별자 인용이 evidence.ids 안에 있어야 함
   ├─ published_rank remap: 답변 [N] → ReferenceItem (ADR-0017)
   ├─ tag → id_axis 매핑: IRD_NAI_PJT_INFO→pjt_id, 성과계열→rst_id
   ├─ 부분 발행 허용: 정합한 항목만 manifest에 포함
   └─ 출력: FinalAnswer(decision ∈ {publish, clarify, internal_error})

9. Routing after final_guard
   ├─ publish → save_session
   ├─ clarify → emit_clarification → save_session
   └─ internal_error → emit_internal_error → save_session

10. save_session
    └─ SubjectQueryContext commit (search_task.subject 있을 때)
    └─ KV에 SessionMemory 저장

11. Response / SSE termination (apps/api/routes.py)
    └─ /query/stream: reference.set + done 이벤트 후 종료
    └─ /query: JSON payload 반환
```

---

## 4. 관측 신호 (Observability)

운영 중 문제 발생 시 다음 로그 이벤트를 우선 확인한다.

| 이벤트 | 위치 | 의미 |
|---|---|---|
| `[node_judgment]` | workflow.py | JudgmentDecision의 kind, strategy, latency |
| `[SearchAgent]` | search_agent.py | dispatch 실패 / 컬렉션·strategy 정보 |
| `[node_search]` | workflow.py | status, total_hits, latency |
| `[FinalGuard]` | final_guard.py | groundedness 위반 / unsupported identifiers |
| `[hybrid_search]` | qdrant_search.py | 컬렉션·필터·임베딩 단계 실패 |
| `[exact_lookup]` | exact_lookup.py | 컬렉션·axis별 scroll 결과 |
| `[load_session]` / `[save_session]` | workflow.py | KV 세션 복원/저장 결과 |
| `[stream_metrics]` | llm_generator.py (간접) | TTFT, 청크 수, truncated 여부 |

레거시 `OPS` 이벤트(`AGENT.DECISION`, `RAG.RETRIEVE`, `ANSWER.STATE_DIAG` 등)는 더 이상
발행되지 않는다.

---

## 5. 도메인 제약 (Domain Constraints)

| 제약 | 의미 |
|---|---|
| `pjt_id` ≠ `pjt_no` | 개별 과제(pjt_id) vs 다년차 과제군(pjt_no). 혼용 금지. detail 조회는 반드시 `pjt_id` 축 사용. |
| 기관 역할 분리 | `lead_org_name`(수행기관) / `participant_org_name`(참여기관) / `people_affiliation_org_name`(소속) 서로 다른 의미. `SearchTask.filters`에 별도 필드로 보존. |
| `ReferenceItem.id` 의미 고정 | `tag=IRD_NAI_PJT_INFO` → `id=pjt_id`. 성과 계열(`IRD_NAI_RI_*`) → `id=rst_id`. `pjt_no`는 primary `ReferenceItem.id`로 사용하지 않음. ([ADR-0017](./ADR/ADR-0017_Answer_Rank_Remapped_Reference_Manifest.md)) |
| `published_rank` vs `source_snapshot_rank` | 사용자에게 보이는 [N] = published_rank. 내부 retrieval snapshot = source_snapshot_rank. FinalGuard가 매핑 발행. |
| canonical_evidence 필수 | raw Qdrant payload를 prompt에 그대로 넣지 않는다. `CanonicalEvidence` 단위로 정규화 후 prompt 작성. |

---

## 6. 계층별 제약

| 계층 | 가능한 것 | 금지된 것 |
|------|-----------|-----------|
| **JudgmentAgent** | rule-based 패턴 매칭, LLM 분류, SubjectAnchor 구성 | Qdrant·LLM-Gen 직접 호출, SearchResult 해석 |
| **SearchAgent** | exact_lookup / hybrid / 정규화 / 상태 판정 | retrieval_query 재작성, JudgmentDecision 무시 |
| **LLMGenerator** | 단일 LLM 스트리밍, evidence block 포맷팅 | FinalGuard 검증 우회, 부가 검색 |
| **FinalGuard** | groundedness + identity + manifest 발행 + 부분 publish | 재검색, 새 SearchTask 생성 |
| **routes/runtime** | SSE 인코딩, KV 셋업, graph 컴파일 | Pipeline 내부 상태 직접 변형 |

---

## 7. 관련 문서

- [02 실행 계약과 전략 규칙](./02_CONTRACTS_AND_RULES.md) — SearchTask/FinalAnswer 계약 상세
- [ADR-0001: LLM-First Dialogue Agent](./ADR/ADR-0001_LLM-First%20Dialogue%20Agent%20over%20Contract-Guarded%20RAG%20Tools.md) — 원형 결정
- [ADR-0016: Agent Contract Shock Absorber](./ADR/ADR-0016_Agent_Contract_Shock_Absorber.md) — Smart Coercion 원칙 (`SearchTask`에 흡수됨)
- [ADR-0017: Answer Rank Remapped Reference Manifest](./ADR/ADR-0017_Answer_Rank_Remapped_Reference_Manifest.md) — FinalGuard ReferenceManifest 구현
- [ADR-0018: 권한 분리 3계층 아키텍처](./ADR/ADR-0018_Three_Layer_Authority_Separation.md) — 본 문서의 최신 진실원
- [레거시 제거 매니페스트](./reports/ADR-0018_Legacy_Cleanup_Manifest.md) — 폐기된 모듈 목록
