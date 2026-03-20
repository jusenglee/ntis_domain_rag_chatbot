# ARCHITECTURE_RETRIEVAL_FIRST

이 문서는 NTIS Domain RAG를 `retrieval-first search system with chat UX`로 설명하는 기준 문서다.

## 시스템의 1차 목적

시스템의 1차 목적은 NTIS 도메인 질의에 대해 올바른 retrieval 전략을 고정하고,
정확한 근거를 조합해 canonical evidence와 prompt view를 만드는 것이다.

답변 wording, streaming, memory, 대화 UX는 중요하지만 모두 retrieval contract를 바꾸지 못하는 상위 레이어다.

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
- retrieval 힌트를 planner 입력 형태로 정리한다.

금지:
- planner contract를 우회해 실행 전략을 확정하지 않는다.
- raw payload를 prompt에 바로 넘기지 않는다.

### 2. Retrieval Execution

입력:
- 최종 조립된 `strategy`
- planner filter spec
- runtime policy

출력:
- retrieval hit
- compiled filter
- join hop result

책임:
- planner가 고정한 strategy를 검증하고 실행한다.
- `SEARCH / LOOKUP / JOIN`에 맞는 orchestration을 수행한다.
- contract violation은 fail-close 한다.

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
- retrieval view와 prompt view를 같은 artifact로 취급하지 않는다.

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
- retrieval result를 읽기 쉬운 답변으로 변환한다.
- memory와 follow-up UX를 제공한다.
- answer merge, streaming, degraded response를 관리한다.

금지:
- retrieval contract를 다른 전략처럼 바꾸지 않는다.
- canonical evidence 없이 임의 답변을 우선하지 않는다.

## 레이어 책임 경계

| 레이어 | 결정 가능 | 결정 금지 |
|---|---|---|
| Query Understanding | retrieval intent 후보, hint 정규화 | final runtime strategy 재결정 |
| Retrieval Execution | contract enforcement, compile, retrieval 수행 | mode / relation / join key 보정 |
| Evidence Assembly | canonicalization, render profile, output presentation | source semantics 변경 |
| Chat UX | 응답 표현, 대화 흐름, streaming | retrieval contract 변경 |

## 구조와 흐름

정적 구조는 `Query Understanding -> Retrieval Execution -> Evidence Assembly -> Chat UX` 순서다.
요청별 동적 흐름과 artifact handoff는 `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`를 기준으로 본다.

```mermaid
flowchart LR
    A[Query Understanding]
    B[Retrieval Execution]
    C[Evidence Assembly]
    D[Chat UX]

    A --> B --> C --> D
```

## Source Of Truth 매핑

- Query Understanding: `apps/api/services/request_facade.py`, `apps/core/query_intent.py`, `apps/core/pipeline_steps.py`
- Strategy / Contract: `apps/api/services/planner_service.py`, `apps/core/planner_contract.py`
- Runtime Enforcement: `apps/core/rag_runtime_prelude.py`
- Retrieval Dispatcher: `apps/core/rag_pipeline.py`
- Base / Join Orchestration: `apps/core/rag_base_orchestration.py`, `apps/core/rag_join_orchestration.py`
- Evidence Assembly: `apps/core/canonical_evidence.py`, `apps/api/services/rag_result_assembly.py`
- Chat UX: `apps/api/routes.py`, `apps/api/services/answer_generation.py`, `apps/api/services/answer_merge.py`

## 운영 우선순위

1. retrieval strategy correctness
2. join/filter correctness
3. canonical evidence fidelity
4. render/output correctness
5. chat UX correctness

## 확장 원칙

- planner contract를 느슨하게 만드는 방향으로 확장하지 않는다.
- broad query, 비교형 질의, 복합 조건 질의는 retrieval policy, reformulation, ranking 계층에서 보강한다.
- 관련 로드맵은 `docs/RETRIEVAL_ROBUSTNESS_PLAN.md`를 기준으로 관리한다.
