# MODE_DECISION_GUIDE


이 문서는 NTIS Domain RAG에서 `SEARCH`, `LOOKUP`, `JOIN` 모드를 어떻게 선택하는지 정리한다.
실행 source of truth는 다음 코드와 계약 문서다.

- 스키마: `apps/core/schemas.py`, `apps/core/pipeline_steps.py`
- 실행 계약: `docs/CONTRACT.md`
- 실행 조립: `apps/core/rag_pipeline.py`, `apps/core/rag_runtime_prelude.py`

## select_mode_policy() 입력 신호

`select_mode_policy()`는 아래 입력을 함께 본다.

- `relation`
- `explicit id`
- `people/org name`
- `candidate exact key`
- `action`
- `output_type`
- planner hint

## 기본 원칙

- `SEARCH`는 넓은 주제 탐색에 사용한다.
- `LOOKUP`은 explicit id, exact key, 사람/기관 이름 기반 조회에 사용한다.
- `JOIN`은 relation이 명확하고 2-hop retrieval이 필요할 때 사용한다.
- `과제번호`는 즉시 `pjt_no`로 고정하지 않고 `candidate_keys.project_key`와 `project_key_policy=ambiguous_or`로 남긴다.
- `people/org name` 질의는 기본적으로 `LOOKUP` 우선이다.
- `JOIN`은 relation 신호와 join seed가 함께 있을 때만 엄격하게 유지한다.

## 모드 결정 표

| 입력 신호 | 권장 mode | 비고 |
|---|---|---|
| `action == relation` and `relation exists` | `JOIN` | relation query |
| relation이 있고 join ids가 명확함 | `JOIN` | resolved relation ids |
| `people/org name` 중심 lookup | `LOOKUP` | people/org name |
| `explicit id` 또는 candidate exact key | `LOOKUP` | exact id or ambiguous exact key |
| `action in (list, detail, stats, download)` | `LOOKUP` | list-like action |
| `action in (topic, search)` | `SEARCH` | broad topic query |
| 기타 | `SEARCH` | default |

## SEARCH

`SEARCH`는 broad topic query에 사용한다.

- explicit id가 없다.
- 강한 lookup signal이 없다.
- relation이 없다.

## LOOKUP

`LOOKUP`은 다음 경우에 사용한다.

- explicit id
- `candidate_keys.project_key` 또는 `candidate_keys.perf_key`
- `people/org name` lookup
- action이 `list`, `detail`, `stats`, `download`

### project key 처리

- `과제고유번호`, `PJT_ID`, `project id` label은 resolved `pjt_id`다.
- `동일과제번호`, `과제그룹번호`, `PJT_NO` label은 resolved `pjt_no`다.
- `과제번호`는 unresolved key로 남기고 runtime이 `ambiguous_or` exact lookup을 실행한다.

## JOIN

`JOIN`은 relation query를 처리할 때 사용한다.

- project -> perf
- perf -> project
- `perf_to_project_to_perf`
- `multi_hop_bundle`

### join_key_mode

- `instance`: resolved `pjt_id`
- `group`: resolved `pjt_no`
- `deferred`: hop1 discovery 뒤 runtime이 `instance`, `group`, `dual_branch` 중 하나를 결정한다.

`join_key_mode=deferred`에서는 discovery 0-hit가 normal no-result이며, resolved `instance/group`에서만 strict `JOIN_KEYS_MISSING`을 쓴다.

## target collections

- relation query는 relation target collections를 우선 사용한다.
- relation이 없으면 `base_route`의 default collection을 사용한다.
- `people`/`org` 질의는 통상 project lookup 후 perf follow-up으로 이어진다.

## output_type

`output_type`은 evidence rendering shape를 정하며 모드를 대체하지 않는다.
지원 값은 다음과 같다.

- `summary`
- `detail`
- `list`
- `stats`
- `relation`
- `comparison`
- `series`

## 점검 체크

- `SEARCH / LOOKUP / JOIN` 의미가 섞이지 않는가
- `과제번호`를 `pjt_no`로 단정하지 않는가
- `candidate_keys.project_key`가 runtime까지 유지되는가
- `join_key_mode=deferred`가 normal no-result와 strict failure를 구분하는가
