# 03 행동안전(L2) 정책과 보정

이 문서는 `ExecutionManager`가 소유하는 L2 행동안전 정책과 bounded recovery 규칙을 정의한다.
L2의 역할은 L1(`QuestionAnalysisV3`)이 고정한 의도와 식별자 의미를 보존하면서,
정책으로 허용된 범위 안에서만 실행 순서와 보정을 제어하는 것이다.

---

## 원칙

- L2는 L1의 `mode`, `head`, `ids_map`, `join_key_mode`, `target_cols` 의미를 재해석하지 않는다.
- 보정은 항상 bounded rule로만 허용한다.
- raw retrieval payload는 그대로 prompt에 올리지 않는다.
- full `execution_trace`는 state/log 전용이고, `selected_answer_meta`에는 summary만 남긴다.
- legacy retry는 legacy SEARCH 전용이며, orchestrator-owned 요청은 재진입하지 않는다.

---

## 현재 ownership 경계

다음 경로는 `ExecutionManager`가 소유한다.

- `project LOOKUP`
- `project SEARCH`
- `project JOIN`
- `people SEARCH`
- `org SEARCH`
- `perf SEARCH`

다음 경로는 아직 legacy retriever에 남겨둔다.

- `support SEARCH`
- unsupported/deferred `JOIN`
- 별도 contract가 없는 specialist flow

---

## 구현된 정책

### 1. `LOOKUP_MISSING_RECOVERY`

- 대상: `project LOOKUP`
- primary: `fetch_project_detail`
- secondary: `search_projects_by_text`
- 허용 보정: 동일 filter를 유지한 SEARCH 1회
- 금지: exact ID 질의의 임의 완화

### 2. `SEARCH_RECOVERY`

- 대상: `project SEARCH`
- primary/secondary: 모두 `search_projects_by_text`
- 허용 보정: 사람/기관 계열 filter 1회 완화
- 금지:
  - top-level mode 변경
  - `pjt_id` / `pjt_no` 축 drift
  - active anchor truth 무시
- observation codes:
  - `empty_primary_search`
  - `filter_gate_too_strict`
  - `candidate_underflow`
  - `relax_budget_exhausted`
  - `search_recovery_succeeded`

### 3. `PEOPLE_SEARCH_OBSERVATION`

- 대상: `people SEARCH`
- `max_steps=1`
- `retry_mode=observation_only`
- 자동 보정 없음
- 근거 수집은 `project + perf` 축을 유지한다.

### 4. `ORG_SEARCH_OBSERVATION`

- 대상: `org SEARCH`
- `max_steps=1`
- `retry_mode=observation_only`
- 자동 보정 없음
- 근거 수집은 `project + perf` 축을 유지한다.

### 5. `PERF_SEARCH_OBSERVATION`

- 대상: `perf SEARCH`
- `max_steps=1`
- `retry_mode=observation_only`
- 자동 보정 없음
- `target_cols=[COL_PERF]`를 기본값으로 사용한다.
- `display_source`는 항상 `docs` 기준으로 유지한다.

### 6. `JOIN_QUALITY_RECOVERY`

- 대상: `project JOIN`
- primary: 현재 contract 그대로 `fetch_project_performance(..., join_key_mode="instance" | "group")`
- 유일한 자동 보정:
  - `instance JOIN`이 0건이고
  - active anchor에 이미 `pjt_no`가 있으며
  - explicit `pjt_id` / hard axis lock이 없는 경우
  - `group` retry 1회를 수행한다.
- 금지:
  - invented key 사용
  - free-text `pjt_id` / `pjt_no` 재해석
  - relation 변경
  - 1회 초과 retry

---

## Trace와 runtime meta

### `execution_trace`

L2는 step마다 다음 정보를 남긴다.

- `policy`
- `tool_name`
- `status`
- `reason`
- `observation_codes`
- `diagnostics`

### `retrieval_runtime_meta`

state에는 최소한 아래 경계를 남긴다.

- `runtime_owner`
- `policy_name`
- `orchestrator_owned`
- `legacy_retry_allowed`
- `execution_kind`
- `base_route`
- `observation_only`

JOIN 보정이 개입한 경우에는 아래 필드도 남긴다.

- `group_recovery_attempted`
- `group_recovery_applied`
- `recovery_join_axis`
- `recovery_source`

---

## 사용자 노출 경계

- `selected_answer_meta`에는 다음 요약만 남긴다.
  - `recovery_applied`
  - `recovery_policy`
  - `recovery_steps`
  - `recovery_user_notice`
- full trace, raw observation dump, runtime diagnostics 전체를 prompt나 사용자 응답에 직접 노출하지 않는다.
