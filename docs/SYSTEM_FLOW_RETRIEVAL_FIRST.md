# Retrieval-First 시스템 흐름

이 문서는 사용자 질의가 NTIS Domain RAG에서 어떤 단계와 artifact를 거쳐 처리되는지 설명한다.
문서의 목적은 두 가지다.

- 운영자가 요청 1건의 흐름을 단계별로 추적할 수 있게 한다.
- 구현자가 문서만 읽고도 현재 retrieval-first vocabulary를 이해할 수 있게 한다.

## 1. 전체 요청 라이프사이클

사용자 질의는 아래 순서로 처리된다.

1. Request ingress
2. Query understanding / intent assembly
3. Planner contract / strategy assembly
4. Runtime prelude / strict validation
5. Retrieval execution
6. Evidence assembly
7. Answer generation / chat UX
8. Response / streaming / ops summary

## 2. 단계별 입력과 출력

| 단계 | 입력 | 출력 | source of truth |
|---|---|---|---|
| Request ingress | raw query, request metadata | workflow state seed | raw request |
| Query understanding | raw query, conversation hint, previous canonical evidence | `question_analysis`, `intent_payload.normalized_intent` | `intent_payload.normalized_intent` |
| Planner contract / strategy assembly | normalized intent, planner artifact | `strategy` | `strategy` |
| Runtime prelude | strategy, planner/runtime contract | `RuntimePreludeResult` | prelude result |
| Retrieval execution | validated strategy, compiled filter, execution policy | retrieval hit, join hop result, retrieval meta | executed retrieval result |
| Evidence assembly | retrieval hit, route/mode/output_type | `canonical_evidence`, `render_profile`, context text | canonical evidence + render profile |
| Answer generation / chat UX | canonical evidence, render profile, answer policy | answer text, stream events | canonical evidence + render profile |
| Response / ops summary | final state | response payload, `strategy_summary`, `REQ.SUMMARY` | final execution state |

## 3. 핵심 artifact와 소유권

### `question_analysis`

- planner artifact다.
- 질의 해석 과정의 planner 출력이며 execution truth가 아니다.
- debug와 planner drift 진단에는 유용하지만 실행 레이어가 그대로 truth로 사용하면 안 된다.

### `intent_payload.normalized_intent`

- retrieval intent의 canonical input이다.
- query understanding 단계가 만든 정규화 결과이며 retrieval workflow의 기본 입력이다.
- `question_analysis`보다 아래 레이어에서 우선한다.

### `strategy`

- execution strategy truth다.
- planner merge, contract validation, runtime prelude를 통과한 최종 실행 전략이다.
- runtime, orchestration, response observability는 이 값을 기준으로 본다.

### `canonical_evidence`

- retrieval result normalization truth다.
- raw retrieval payload를 prompt에 직접 넣지 않고 canonical schema로 정규화한 결과다.
- `pjt_id`, `pjt_no`, 기관 역할 의미를 보존해야 한다.

### `render_profile`

- presentation contract다.
- `output_type`과 route/mode에 맞는 context shape를 결정한다.
- answer wording이 아니라 evidence presentation shape를 담당한다.

## 4. 단계별 동작 설명

### 4.1 Request ingress

입력:
- 사용자 질의
- request id, conversation id
- 운영 fingerprint

동작:
- API route가 workflow state seed를 만든다.
- 이 단계에서는 retrieval strategy를 결정하지 않는다.

주요 관측 지점:
- `REQ.START`

### 4.2 Query understanding / intent assembly

입력:
- raw query
- previous canonical evidence
- follow-up hint

동작:
- query intent heuristic과 planner 입력 컨텍스트를 조립한다.
- `question_analysis`와 `intent_payload.normalized_intent`를 만든다.
- broad query, exact lookup, relation query를 retrieval intent 관점에서 정리한다.

출력:
- `question_analysis`
- `intent_payload.normalized_intent`

주요 관측 지점:
- `PLANNER.PIPELINE`
- `PLANNER.STAGE1`
- `PLANNER.STAGE2`
- `PLANNER.ASSEMBLE`

### 4.3 Planner contract / strategy assembly

입력:
- `intent_payload.normalized_intent`
- planner artifact

동작:
- deterministic gate와 planner merge를 거쳐 final strategy를 조립한다.
- `mode`, `relation`, `join_key_mode`, `target_cols`, `output_type`를 strict contract로 확정한다.

출력:
- `strategy`

주요 관측 지점:
- `RAG.PLAN`
- `RAG.STRATEGY.DIFF.*`

### 4.4 Runtime prelude / strict validation

입력:
- final `strategy`
- planner contract

동작:
- planner contract와 runtime contract를 strict하게 검증한다.
- `JOIN`이면 `join_key_mode`, `ids_map`, relation, target consistency를 fail-close 기준으로 본다.
- 하위 runtime이 새 전략을 발명하지 못하도록 gate를 형성한다.

출력:
- `RuntimePreludeResult`

주요 관측 지점:
- `validate_planner_contract()`
- `REQ.ERROR`

### 4.5 Retrieval execution

입력:
- validated strategy
- compiled filter
- execution policy

동작:
- `SEARCH / LOOKUP / JOIN`에 맞는 orchestration을 수행한다.
- `JOIN`이면 hop1 execution policy, runtime key materialization, hop2 compile selection을 거친다.
- execution layer는 planner artifact를 다시 truth처럼 해석하지 않는다.

출력:
- retrieval hit
- join hop result
- execution metadata

주요 관측 지점:
- `RAG.RETRIEVE`
- `RAG.COL.RETRIEVE`
- `RAG.COL.STATS`
- `RAG.JOIN.POLICY`
- `RAG.JOIN.HOP1`
- `RAG.JOIN.HOP2.TOP`
- `executed_join_filter_spec._meta`

### 4.6 Evidence assembly

입력:
- retrieval hit
- execution mode / route / output_type

동작:
- retrieval hit를 canonical evidence로 정규화한다.
- `render_profile`을 선택하고 context text를 만든다.
- raw retrieval payload 전체를 prompt에 dump하지 않는다.

출력:
- `canonical_evidence`
- `render_profile`
- context text

주요 관측 지점:
- `RAG.RESULT.TOP`
- `RAG.CONTEXT`
- `RAG.CTX`
- `RAG.RESULT`

### 4.7 Answer generation / chat UX

입력:
- canonical evidence
- render profile
- answer policy

동작:
- retrieval result를 사용자에게 읽기 쉬운 답변으로 변환한다.
- stream event, answer merge, follow-up UX를 제공한다.
- conversation history와 canonical context snapshot을 함께 저장한다.
- retrieval contract를 바꾸지 않는다.

출력:
- answer text
- stream event
- conversation memory snapshot

주요 관측 지점:
- `KS.RESULT`
- `LLM.RESULT`
- `STREAM.DONE`

### 4.8 Response / ops summary

입력:
- final workflow state

동작:
- 사용자 응답 payload를 만든다.
- `/query/debug`에서는 `question_analysis`와 `strategy_summary`를 함께 보여주되, execution truth는 `strategy_summary`로 본다.
- 운영 요약 로그 `REQ.SUMMARY`는 execution strategy를 기준으로 기록한다.

출력:
- response payload
- `strategy_summary`
- `REQ.SUMMARY`

주요 관측 지점:
- `/query/debug.strategy_summary`
- `REQ.SUMMARY`
- `REQ.END`

## 5. SEARCH / LOOKUP / JOIN 분기

### SEARCH

- broad topic query와 recall 우선 질의에 사용한다.
- 과도한 hard filter를 얹지 않는다.
- ranking과 retrieval breadth가 중요하다.

### LOOKUP

- identifier 또는 강한 제약 기반 조회에 사용한다.
- 정확한 filter와 field constraint가 중요하다.
- 사람/기관 이름 기반 질의는 기본적으로 LOOKUP 계열에서 시작한다.

### JOIN

- project-perf 관계형 질의에 사용한다.
- relation과 join seed가 모두 있어야 한다.
- hop1 / hop2 execution policy와 runtime key materialization이 중요하다.
