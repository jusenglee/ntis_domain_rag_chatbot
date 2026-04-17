# ADR-0010: 2-Layer Contract Refinement

- Status: Accepted
- Updated: 2026-04-15

---

## Context

초기 NTIS RAG는 SEARCH / LOOKUP / JOIN을 강하게 분리한 검색엔진형 파이프라인으로 출발했다.
이 구조는 정확성과 재현성에는 강했지만, 다음 문제가 있었다.

- runtime ownership이 거대 모듈에 몰려 있었다.
- legacy retry가 여러 경로에 흩어져 있었다.
- `people/org/perf` 검색과 `JOIN` 실패 처리의 행동 규칙이 코드에 암묵적으로 묻혀 있었다.

우리는 이를 `L1 의도 계약 + L2 행동안전` 구조로 재정렬하기로 했다.

---

## Decision

### L1: Intent Truth

- `QuestionAnalysisV3`는 사용자 질문의 의도, 식별자 축, filter 의미를 고정한다.
- runtime은 L1이 정한 `mode`, `head`, `ids_map`, `join_key_mode`, `target_cols`를 재해석하지 않는다.

### L2: Behavioral Safety

- `RuntimeDispatchPlan`이 ownership을 결정한다.
- `ExecutionManager`가 bounded policy만 실행한다.
- tool은 raw collaborator를 감싼 stateless 실행 단위로 유지한다.

---

## Implemented Scope

### Project ownership

- `project LOOKUP` -> `LOOKUP_MISSING_RECOVERY`
- `project SEARCH` -> `SEARCH_RECOVERY`
- `project JOIN` -> `JOIN_QUALITY_RECOVERY`

### Route observation ownership

- `people SEARCH` -> `PEOPLE_SEARCH_OBSERVATION`
- `org SEARCH` -> `ORG_SEARCH_OBSERVATION`
- `perf SEARCH` -> `PERF_SEARCH_OBSERVATION`

### JOIN bounded recovery

`JOIN_QUALITY_RECOVERY`의 유일한 자동 보정은 아래 한 가지다.

- primary `instance JOIN` 0건
- active anchor에 이미 `pjt_no`가 있음
- explicit `pjt_id` 또는 hard axis lock이 아님
- 이때만 `group` retry 1회

즉, free-text에서 새 key를 발명하지 않고, state anchor truth만 recovery authority로 인정한다.

---

## Constraints

- top-level mode promotion 금지
- invented key 금지
- `pjt_id` / `pjt_no` free-text 재해석 금지
- relation 변경 금지
- retry 1회 초과 금지
- full `execution_trace`는 state/log only
- `selected_answer_meta`는 summary only

---

## Consequences

긍정적 효과:

- runtime ownership이 `RuntimeDispatchPlan`으로 명시화되었다.
- legacy retry는 legacy SEARCH로만 축소되었다.
- `people/org/perf SEARCH`와 `JOIN`의 행동 규칙이 테스트 가능한 정책 단위로 올라왔다.

의도적으로 남긴 것:

- `support SEARCH`는 아직 legacy 유지
- unsupported/deferred `JOIN`은 후속 backlog
- specialist flow 전체 정리는 후속 단계
