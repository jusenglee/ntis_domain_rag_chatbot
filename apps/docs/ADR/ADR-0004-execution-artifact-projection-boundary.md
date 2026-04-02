# ADR-0004: 실행 결과, 대화 투영, 터미널 응답 경계 분리

- 상태: Proposed
- 작성일: 2026-03-27

## 배경

현재 request workflow 자체는 단순하다.

- `load_memory -> rule_precheck -> analyze_question -> judge_knowledge_sufficiency -> rag_search -> generate_answer_* -> join_answers -> merge_answers -> save_history`

하지만 실제 구조 복잡도는 graph가 아니라 몇 개의 대형 모듈에 몰려 있다.

- `apps/retrieval/retrieval_workflow.py`
- `apps/api/routes.py`
- `apps/api/contracts/workflow_models.py`
- `apps/retrieval/rag_pipeline.py`
- `apps/evidence/rag_result_assembly.py`

이 중 지금 가장 안전하게 건드릴 수 있는 경계는 post-retrieval 단계다. 현재 이 단계에서는 "검색 실행 결과", "대화 상태 투영", "답변 입력 컨텍스트", "사용자에게 보낼 최종 응답"이 서로 다른 artifact로 분리되지 않은 채 함께 움직인다.

## 현재 구조 요약

### 현재 코드가 하는 일

- `apps/retrieval/retrieval_workflow.py`의 `node_rag_search()`는 다음 책임을 한 함수 안에서 함께 처리한다.
  - follow-up anchor / focus entity 복원
  - detail cache hit/miss 판단
  - exact detail lookup query 강제
  - retrieval query provenance 계산
  - retriever 호출
  - canonical evidence / display payload 정규화
  - display snapshot 생성 및 `view_state` mutation
  - detail coverage 계산
  - short-circuit용 `answer_artifact` 또는 `answer_context_text` 준비

- `apps/evidence/result_set.py`의 `RetrievalBundle`은 아래 성격이 다른 내용을 동시에 들고 있다.
  - execution 결과 item 집합
  - render profile
  - raw count
  - no-result / clarification
  - answer context text
  - context source

- `apps/api/routes.py`는 terminal truth를 단일 필드에서 읽지 못해 다음 값을 순서대로 재구성한다.
  - `final_answer_artifact`
  - `answer_artifact`
  - `final_answer_text`
  - `messages[-1]`
  - `retrieval_bundle.clarification`

- `apps/api/contracts/workflow_models.py`의 `AgentState`는 새 경계와 legacy 경계가 함께 남아 있다.
  - `answer_artifact`
  - `answer_artifact_gemma`
  - `answer_artifact_solar`
  - `final_answer_text`
  - `final_answer_artifact`
  - `retrieval_bundle`
  - `answer_context_text`
  - `context`
  - `canonical_evidence`
  - `view_state`

### 현재 회귀면

현재 테스트는 이미 아래 계약을 강하게 고정하고 있다.

- detail cache hit은 direct answer가 아니라 `detail_contract_context` 기반 narration 경로를 유지해야 한다.
- follow-up anchor가 있으면 exact lookup query가 seed truth를 우선해야 한다.
- canonical evidence 축이 docs wrapper보다 강하면 display snapshot은 canonical axis로 복구되어야 한다.
- route는 `answer.final`과 `done`을 정확히 한 번씩 내보내야 한다.
- legacy flat SSE payload 모양은 유지되어야 한다.

즉 지금 필요한 것은 broad rewrite가 아니라, 기존 동작을 유지한 채 artifact ownership을 분리하는 단계적 설계다.

## 문제점

### 1. 검색 실행과 대화 상태 mutation이 같은 경계에 묶여 있다

`node_rag_search()`는 retrieval 실행 결과를 만드는 함수이면서 동시에:

- `view_state.latest_display_snapshot`
- `view_state.latest_focus_entity`
- `view_state.detail_cache`
- follow-up/detail short-circuit answer inputs

까지 직접 만든다.

이 구조에서는 retrieval 변경이 session state 변경과 쉽게 엮이고, rollback 범위도 커진다.

### 2. terminal response의 소유자가 불분명하다

route는 serializer여야 하지만 현재는 final answer truth를 여러 legacy field에서 재구성하는 복구 계층 역할도 맡고 있다.

이 상태에서는:

- 어떤 node가 user-visible terminal truth를 확정하는지 모호하고
- short-circuit response와 LLM merge response가 같은 contract를 공유하지 못하며
- route 테스트가 serializer 테스트이면서 동시에 state reconstruction 테스트가 된다.

### 3. `RetrievalBundle`이 서로 다른 계층 artifact를 혼합한다

현재 bundle은 item set, context metadata, clarification, no-result, answer context를 함께 담고 있다. 이 때문에 bundle이 "검색 결과"인지 "답변 입력"인지 "route fallback"인지 역할이 흐려진다.

### 4. `AgentState`가 중간 이행용 필드를 오래 품고 있다

현재 state는 migration 과정에서 필요한 중복 필드를 이미 많이 가지고 있다. 하지만 artifact ownership이 문서화되지 않아, 새 필드가 추가될수록 state는 더 넓어지고 route/retrieval code는 더 방어적으로 변한다.

### 5. 구조 의도가 repo 안에 충분히 남아 있지 않다

현재 Korean docs와 regression tests는 운영 규칙을 잘 담고 있지만, "왜 post-retrieval을 분리해야 하는가"와 "어디부터 안전하게 옮길 것인가"는 별도 ADR로 고정되어 있지 않다.

## 결정

post-retrieval concerns를 아래 네 개의 additive artifact로 분리하는 방향을 채택한다.

### 1. Execution Result

검색 실행의 진실원이다.

포함해야 할 내용:

- query provenance
  - `raw_query`
  - `planner_query`
  - `resolved_retrieval_query`
  - `actual_retrieval_query`
- execution items
  - canonical item
  - display item
  - raw hit linkage
- render profile
- raw result count
- no-result classification

포함하지 않아야 할 내용:

- `view_state` mutation
- route wire payload
- final answer text

### 2. Conversation Projection

execution result를 대화/session truth로 투영한 결과다.

포함해야 할 내용:

- display snapshot
- focus entity update
- detail cache patch
- visible count / requested count diagnostics
- reference payload materialization에 필요한 projection data

이 artifact는 "무엇을 보여줄 수 있는가"와 "다음 turn이 무엇을 참조해야 하는가"를 책임진다.

### 3. Answer Input Bundle

answer generation이 읽는 공식 입력이다.

포함해야 할 내용:

- `answer_context_text`
- `context_source`
- answer prompt에 내려갈 canonical/context projection
- detail contract context 같은 narration-specific context

이 artifact는 retrieval bundle이나 route fallback 용도와 분리되어야 한다.

### 4. Terminal Response Envelope

user-visible terminal truth의 단일 소유자다.

포함해야 할 내용:

- final answer artifact
- clarification payload
- degraded/error metadata
- reference payload
- done emission policy

route는 이 envelope를 wire shape로 serialize만 해야 한다.

## 목표 상태

목표 상태는 다음과 같다.

- retrieval node는 execution truth를 만든다.
- projection builder는 session/display truth를 만든다.
- answer generation은 answer input bundle만 읽는다.
- merge 또는 short-circuit path는 terminal response envelope만 쓴다.
- route는 terminal response envelope를 SSE/legacy flat payload로 변환만 한다.

이 구조가 되면 다음 효과가 생긴다.

- retrieval, projection, terminal serialization rollback 범위가 분리된다.
- detail cache와 follow-up anchor 규칙을 route 계층 밖에서 일관되게 유지할 수 있다.
- `answer_artifact` / `final_answer_text` / `messages[-1]` 같은 복구성 fallback을 점진적으로 제거할 수 있다.
- route 테스트를 serializer 테스트로 더 좁힐 수 있다.

## staged migration plan

### Phase 0. additive contract 도입

목표:

- 새 artifact model을 추가하되 기존 state/output은 그대로 유지한다.

작업:

- 새 contract module 추가
- `AgentState`에 optional additive field 추가
- legacy field -> new artifact adapter 추가
- 기존 로그와 SSE wire format 유지

검증:

- 기존 retrieval/route 회귀 테스트 green
- 새 artifact가 legacy field와 동일한 truth를 담는지 adapter 테스트 추가

rollback:

- 새 artifact 필드를 읽는 caller를 늘리지 않고, 쓰기만 중단하면 즉시 복귀 가능

### Phase 1. conversation projection 추출

목표:

- display snapshot, focus entity, detail cache 관련 로직을 pure projection helper로 분리한다.

작업:

- `node_rag_search()` 안의 display normalization / snapshot / focus update / cache patch를 projection builder 함수로 분리
- `view_state` mutation 이전의 projection result를 명시적으로 만든 뒤 적용

검증:

- `DISPLAY.SNAPSHOT.BUILT`, `RAG.COUNT_PIPELINE.RESULT`, `DETAIL.CACHE.*` 로그 유지
- detail follow-up / canonical-axis recovery 회귀 테스트 유지

rollback:

- projection helper 호출을 제거하고 기존 inline 로직으로 되돌리면 된다

### Phase 2. answer input 경계 고정

목표:

- `answer_generation.py`가 `retrieval_bundle`과 scattered state field 대신 `AnswerInputBundle`만 읽게 만든다.

작업:

- `answer_context_text`, `context_source`, rendered canonical context source를 bundle로 묶기
- short-circuit detail/no-result/clarification 경로도 같은 bundle contract를 사용

검증:

- `LLM.GENERATE`의 `context_source` 해석 유지
- detail cache narration 테스트 유지

rollback:

- bundle consumer를 legacy state field 읽기로 되돌리면 된다

### Phase 3. terminal response envelope 전환

목표:

- route가 final answer를 재구성하지 않고 terminal envelope 하나만 serialize하도록 만든다.

작업:

- `merge_answers`, `direct_answer`, retrieval short-circuit가 모두 terminal envelope를 채우게 함
- route는 `final_answer_artifact` / `answer_artifact` / `messages[-1]` fallback 재구성 로직을 더 이상 확장하지 않음
- legacy flat SSE payload mapping은 유지

검증:

- `answer.final` / `done` 단일 emission 회귀 유지
- clarification hidden/visible legacy contract 유지
- `MISSING_FINAL_ANSWER` guard는 terminal envelope 부재 guard로 축소

rollback:

- route serializer를 legacy state reconstruction 경로로 되돌리면 된다

### Phase 4. legacy field 정리

목표:

- duplicated state field와 transitional adapter를 제거한다.

작업:

- 사용처가 사라진 legacy fields 제거
- `AgentState` 축소
- route/retrieval helper 정리

검증:

- full pytest + baseline checks
- ops summary field가 새 artifact source와 일치하는지 확인

rollback:

- Phase 3에서 남겨둔 adapter layer를 다시 활성화한다

## 첫 번째 안전한 단계

첫 단계는 "코드 이동"이 아니라 "경계 명시"다.

구체적으로는:

1. `apps/api/contracts/` 아래에 additive artifact model을 새로 만든다.
2. `AgentState`에 optional field를 추가하되 기존 field는 제거하지 않는다.
3. `node_rag_search()`가 기존 return dict를 유지하면서 `execution_result`와 `answer_input_bundle`을 함께 채우게 한다.
4. route는 아직 새 envelope를 읽지 않고 legacy path를 유지한다.

이 단계의 장점:

- behavior change가 거의 없다.
- snapshot/detail/follow-up regression을 깨지 않고 새 contract를 도입할 수 있다.
- 이후 phase에서 route/retrieval 정리를 하더라도 rollback 기준점이 분명하다.

## files to create or update

첫 migration implementation에서 우선 후보가 되는 파일은 아래와 같다.

- create: `apps/api/contracts/execution_artifacts.py`
- update: `apps/api/contracts/workflow_models.py`
- update: `apps/retrieval/retrieval_workflow.py`
- update: `apps/evidence/result_set.py`
- update: `apps/chat/answer_generation.py`
- update: `apps/api/routes.py`
- update: `tests/test_retrieval_workflow_detail_runtime.py`
- update: `tests/test_api_routes_reference_payload.py`

`apps/retrieval/rag_pipeline.py`와 `apps/evidence/rag_result_assembly.py`는 현재도 크지만, 이 ADR의 첫 단계에서는 직접 분해 대상으로 잡지 않는다. 먼저 route/retrieval/post-retrieval boundary를 고정한 뒤 다음 단계 debt로 다루는 편이 안전하다.

## trade-offs

- 단기적으로는 field duplication이 잠시 늘어난다.
- additive adapter와 transition test가 필요해 코드량이 조금 증가한다.
- 하지만 broad rewrite보다 rollback이 훨씬 쉽고, 현재 regression surface를 보존하기 좋다.

## risks and unknowns

- detail cache entry와 display snapshot이 같은 projection artifact에 완전히 합쳐질 수 있는지는 추가 검증이 필요하다.
- legacy SSE flat payload가 terminal envelope 전환 후에도 모든 프런트엔드 소비자와 호환되는지는 별도 확인이 필요하다.
- `AgentState`를 줄이는 마지막 단계에서 hidden consumer가 있는지 아직 확정되지 않았다.
- `apps/retrieval/rag_pipeline.py`와 `apps/evidence/rag_result_assembly.py`의 대형 시그니처는 별도 debt로 남는다.

## 비적용 범위

이 ADR은 지금 당장 아래 작업을 요구하지 않는다.

- workflow graph topology 변경
- planner contract 변경
- `SEARCH / LOOKUP / JOIN` semantics 변경
- `rag_pipeline.py` 대형 리라이트
- legacy SSE wire format 제거
