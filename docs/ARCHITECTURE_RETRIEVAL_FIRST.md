# Retrieval-First 아키텍처

이 문서는 NTIS Domain RAG 시스템을 `retrieval-first search system with chat UX`로 설명하는 기준 문서입니다.

## 시스템 정체성

이 시스템의 1차 목적은 NTIS 도메인 질의에 대해 올바른 검색 전략을 선택하고,
정확한 근거를 조합해 일관된 retrieval result를 만드는 것입니다.

대화형 응답, streaming, memory, 답변 스타일은 중요하지만 모두 retrieval contract를 바꾸지 못하는 상위 UX 계층입니다.

## 4개 레이어

### 1. Query Understanding

입력:
- 사용자 질의
- 대화 힌트
- 이전 canonical evidence

출력:
- `NormalizedIntent`
- planner 입력 컨텍스트

책임:
- 질의에서 retrieval intent를 읽는다.
- `SEARCH / LOOKUP / JOIN` 판단에 필요한 seed를 조립한다.
- retrieval 의미를 planner 입력 형태로 정리한다.

금지:
- planner contract를 우회해 실행 전략을 확정하지 않는다.
- raw payload를 prompt에 그대로 밀어 넣지 않는다.

### 2. Retrieval Execution

입력:
- 최종 조립된 strategy
- planner filter spec
- runtime policy

출력:
- retrieval hit
- compiled filter
- join hop result

책임:
- planner가 확정한 strategy를 검증하고 실행한다.
- `SEARCH / LOOKUP / JOIN`에 맞는 필터와 retrieval orchestration을 수행한다.
- contract violation 시 fail-close 한다.

금지:
- `mode`, `relation`, `join_key_mode`, `target_cols`를 새로 발명하지 않는다.
- invalid strategy를 fallback strategy로 바꾸지 않는다.

### 3. Evidence Assembly

입력:
- retrieval hit
- route / mode / output_type

출력:
- canonical evidence
- render profile
- context text

책임:
- raw retrieval payload를 canonical schema로 정규화한다.
- `output_type`에 맞는 evidence presentation shape를 선택한다.
- `pjt_id`, `pjt_no`, 기관 역할 의미를 보존한다.

금지:
- retrieval metadata 전체를 LLM에 직접 노출하지 않는다.
- retrieval view와 prompt view를 같은 것으로 취급하지 않는다.

### 4. Chat UX

입력:
- canonical evidence
- render profile
- answer policy
- conversation state

출력:
- 사용자 응답
- streaming event
- conversation memory snapshot

책임:
- retrieval result를 사람이 읽기 쉬운 답변으로 변환한다.
- memory와 follow-up UX를 제공한다.
- conversation history와 canonical context snapshot을 함께 다룬다.
- answer merge, streaming, degraded response를 관리한다.

금지:
- retrieval contract를 덮어써서 다른 전략처럼 보이게 하지 않는다.
- canonical evidence 없이 임의의 답을 우선하지 않는다.

## 레이어별 책임 경계

| 레이어 | 결정 가능 | 결정 금지 |
|---|---|---|
| Query Understanding | retrieval intent 후보, hint 정규화 | final runtime strategy 재발명 |
| Retrieval Execution | contract enforcement, compile, retrieval 실행 | mode / relation / join key 보정 |
| Evidence Assembly | canonicalization, render profile, output presentation | source semantics 변경 |
| Chat UX | 답변 표현, 대화 흐름, streaming | retrieval contract 변경 |

## 정적 구조와 동적 흐름

이 문서는 정적 레이어 구조와 책임 경계를 설명합니다.
사용자 질의 이후 단계별 실행 흐름과 artifact handoff는 `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`를 기준으로 봅니다.

### 정적 구조 다이어그램

```mermaid
flowchart LR
    A[Query Understanding]
    B[Retrieval Execution]
    C[Evidence Assembly]
    D[Chat UX]

    A --> B --> C --> D
```

### 레이어별 source of truth

| 레이어 | 핵심 artifact | source of truth |
|---|---|---|
| Query Understanding | `question_analysis`, `normalized_intent` | `normalized_intent` |
| Retrieval Execution | `strategy`, runtime prelude result | `strategy` |
| Evidence Assembly | `canonical_evidence`, `render_profile` | `canonical_evidence` + `render_profile` |
| Chat UX | answer payload, stream event | canonical evidence 기반 최종 응답 |

### 레이어 간 금지 경계

- Query Understanding은 planner artifact를 만들 수 있지만 execution strategy를 확정하지 않습니다.
- Retrieval Execution은 strategy를 검증하고 실행하지만 새 전략을 발명하지 않습니다.
- Evidence Assembly는 source semantics를 보존하지만 retrieval metadata 전체를 prompt에 노출하지 않습니다.
- Chat UX는 answer를 표현하지만 retrieval contract를 덮어쓰지 않습니다.
- history는 대화 UX 맥락용이며 retrieval anchor source of truth를 대체하지 않습니다.

## Source Of Truth 매핑

- Query Understanding: `apps/api/services/request_facade.py`, `apps/core/query_intent.py`, `apps/core/pipeline_steps.py`
- Strategy / Contract: `apps/api/services/planner_service.py`, `apps/core/planner_contract.py`
- Runtime Enforcement: `apps/core/rag_runtime_prelude.py`
- Retrieval Dispatcher: `apps/core/rag_pipeline.py`
- Base / Join Orchestration: `apps/core/rag_base_orchestration.py`, `apps/core/rag_join_orchestration.py`
- Evidence Assembly: `apps/core/canonical_evidence.py`, `apps/api/services/rag_result_assembly.py`
- Chat UX: `apps/api/routes.py`, `apps/api/services/answer_generation.py`, `apps/api/services/answer_merge.py`

## 다이어그램 작성 원칙

- 구조 다이어그램은 이 문서를 기준으로 그립니다.
- 단계별 요청 흐름 다이어그램은 `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`를 기준으로 그립니다.
- planner artifact와 execution artifact를 같은 박스나 같은 source of truth로 묶지 않습니다.
- `question_analysis`, `normalized_intent`, `strategy`, `canonical_evidence`, `render_profile`를 서로 다른 artifact로 표기합니다.

## 품질 우선순위

1. retrieval strategy correctness
2. join/filter correctness
3. canonical evidence fidelity
4. render/output correctness
5. chat UX 품질

## 확장 원칙

- 다양한 사용자 질의 대응력은 planner contract를 느슨하게 만드는 방식이 아니라 retrieval policy, query reformulation, ranking 계층을 보강하는 방식으로 확장합니다.
- 관련 설계 로드맵은 `docs/RETRIEVAL_ROBUSTNESS_PLAN.md`를 기준으로 관리합니다.
