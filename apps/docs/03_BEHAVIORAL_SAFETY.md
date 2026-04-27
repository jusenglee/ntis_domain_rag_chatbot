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
- Shock Absorber는 L2가 L1을 고치는 권한이 아니라, 부수 파라미터와 오류 처리 경계를 안전하게 흡수하는 원칙이다.
- `action=detail` 또는 `output_type=detail`은 count를 `1/1`로 normalize하더라도 단일 후보 guard를 통과하기 전에는 실행하지 않는다.

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
  - detail-like 요청의 broad search 확장
  - 단일 후보 미확정 상태에서 임의 첫 후보 상세 조회
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

## Shock Absorber 적용 규칙 (ADR-0016)

### Smart Coercion

L2에서 허용되는 Smart Coercion은 실행 의미를 바꾸지 않는 표시/개수 파라미터에 한정한다. 세부 원칙은 [ADR-0016](./ADR/ADR-0016_Agent_Contract_Shock_Absorber.md)을 따른다.

- `limit` / `display_limit` 누락 또는 LLM 부수 오류는 planner-validated count contract로 normalize할 수 있다.
- `action=detail` 또는 `output_type=detail`은 `limit=1`, `display_limit=1`로 normalize한다.
- **교정 금지:** 대상 식별, `pjt_id`/`pjt_no`, `target_cols`, 기관 역할 필터, `SEARCH/LOOKUP/JOIN` 모드는 normalize하지 않는다.

### Detail Guard

Detail guard는 count normalization과 별개로 동작하는 안전장치다.

- 단일 후보가 explicit ID, current context, active scope, visible manifest 중 하나에서 확인되면 detail lookup으로 진행한다.
- 후보가 여러 개이면 Agent에게 compact observation 또는 사용자 clarification으로 닫는다.
- 후보가 없으면 no-result 또는 clarification으로 닫는다.
- **Fail-closed:** 어떤 경우에도 detail-like 요청을 `SEARCH_RECOVERY` list 결과로 확장하여 임의의 결과를 노출하지 않는다.

### Internal Error Loop

Tool backend, planner, schema validation 오류는 사용자 모호성이 아니다.

- 오류 payload는 raw dump가 아니라 compact observation으로 Agent에 최대 1회 반환한다.
- Agent가 corrected tool call을 만들면 같은 guarded pipeline으로 재시도한다.
- 재시도 후에도 guarded intent가 없으면 `agent_internal_error`로 닫는다.
- 실제 사용자 대상이 복수라서 모호한 경우에만 clarification으로 닫는다. (ADR-0015 준수)

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
- contract-invalid 상태에서는 LLM 답변 스트리밍을 시작하지 않는다.
- detail guard 실패는 무관 후보를 context에 넣는 대신 deterministic terminal message 또는 Agent clarification observation으로 닫는다.
