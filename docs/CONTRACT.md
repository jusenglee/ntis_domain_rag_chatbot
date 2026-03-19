# 계약 (CONTRACT)

이 문서는 NTIS Domain RAG의 retrieval-first 실행 계약을 기록합니다.

파라미터 정의는 `docs/PLANNER_PARAMETER_REFERENCE.md`,
SEARCH / LOOKUP / JOIN 결정 규칙은 `docs/MODE_DECISION_GUIDE.md`,
단계별 시스템 흐름은 `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`를 함께 봅니다.

## 현재 기준선

- 시스템 정체성: `retrieval-first search system with chat UX`
- Runtime baseline: staged-only + strict
- planner invalid / contract violation: fail-close
- lower layer fallback / promotion re-execution: 비활성

## 1. Retrieval Intent Contract

### 목적

retrieval intent는 answer 스타일보다 먼저 확정되어야 하며, 이후 레이어는 이 intent를 재해석하지 않고 소비해야 합니다.

### 허용 / 소유 필드

- `mode`: `search | lookup | join`
- `action`: `topic | list | detail | stats | download`
- `relation`: `('project', 'perf') | ('perf', 'project') | None`
- `join_key_mode`: `instance | group | None`
- `target_cols`
- `ids_map`
- `output_type`

### MUST

- `SEARCH`는 탐색과 recall 우선 모드여야 합니다.
- `LOOKUP`은 identifier 또는 강한 제약 기반 정확 조회 모드여야 합니다.
- `JOIN`은 관계형 2-hop retrieval 모드여야 합니다.
- `output_type`은 answer wording이 아니라 evidence presentation shape를 뜻해야 합니다.

### MUST NOT

- `pjt_id`와 `pjt_no`를 같은 의미로 취급하면 안 됩니다.
- 같은 lookup/join seed에 `pjt_id`와 `pjt_no`를 동시에 넣으면 안 됩니다.

### Fail-close 조건

- identifier semantics가 충돌하면 fail-close 해야 합니다.

## 2. Strategy Assembly Contract

### 목적

planner와 gate는 하나의 최종 strategy만 만들어야 하며, assembly 이후 하위 레이어는 이를 재추론하면 안 됩니다.

### Stage 1 허용 필드

- `action`
- `head`
- `relation_candidate`
- `referential_followup`
- `confidence`

### Stage 1 금지 필드

- `mode`
- `join_key_mode`
- `target_cols`
- `ids_map`
- `filters`
- `retrieval_query`
- `limit`

### Deterministic gate 소유 필드

- `mode`
- `relation`
- `join_key_mode`
- `target_cols`
- `output_type`

### Stage 2 허용 필드

- `ids_map`
- `filters`
- `retrieval_query`
- `limit`
- `confidence`

### Stage 2 금지 필드

- `mode`
- `head`
- `action`
- `relation`
- `join_key_mode`
- `target_cols`
- `strategy_version`

### MUST

- planner 전단 explicit hint는 구조적 신호만 소유해야 합니다.
- `과제번호` 단독 표현은 `pjt_id`/`pjt_no` 중 하나로 즉시 확정하지 않고 모호 표현으로 다뤄야 합니다.
- 영문+숫자 project key는 slot 라벨 또는 slot별 강한 패턴이 있을 때만 seed로 승격해야 합니다.
- 사람 이름, 기관명, 기관 역할 의미는 regex/keyword heuristic로 복원하지 않고 planner가 해석해야 합니다.
- `target_cols` 기본값은 공용 policy source와 문서 기준선을 따라야 합니다.
- executor가 받는 입력은 최종 strategy object 하나여야 합니다.

### MUST NOT

- stagewise gate가 별도 기본 collection 규칙을 들고 있으면 안 됩니다.
- parser, merge, runtime prelude가 전략 의미를 자동 보정하면 안 됩니다.
- assembly 이후 하위 레이어가 planner strategy를 다시 추론하거나 보정하면 안 됩니다.

### Fail-close 조건

- planner contract를 위반한 strategy는 fail-close 해야 합니다.

## 3. Runtime Enforcement Contract

### 목적

runtime은 planner가 확정한 strategy를 검증하고 실행할 뿐, 새 전략을 만들지 않습니다.

### 소유 레이어

- `apps/api/services/planner_service.py`
- `apps/core/planner_contract.py`
- `apps/core/rag_runtime_prelude.py`
- `apps/core/rag_pipeline.py`

### MUST

- planner invalid / contract violation은 `StrategyViolation`으로 종료해야 합니다.
- Runtime prelude output은 명시적 `RuntimePreludeResult` 계약으로만 소비해야 합니다.
- evidence assembly는 raw retrieval payload를 canonical evidence로 정규화해야 합니다.
- chat UX는 canonical evidence와 render profile를 우선 사용해야 합니다.

### MUST NOT

- 하위 runtime layer는 fallback plan을 만들면 안 됩니다.
- compat branch나 silent correction은 운영 기본 경로에 포함되면 안 됩니다.
- `rag_pipeline.py`는 prelude 시절의 로컬 helper나 반환되지 않은 값을 직접 참조하면 안 됩니다.

### JOIN MUST

- `mode=join`이면 `join_key_mode`가 존재해야 합니다.
- `join_key_mode`와 `ids_map`은 일치해야 합니다.
- executor는 planner intent를 다시 해석하지 않고 runtime key materialization만 검증해야 합니다.

### JOIN Fail-close 조건

- `mode=join`인데 `join_key_mode`가 비어 있으면 fail-close 합니다.
- `mode!=join`인데 `join_key_mode`가 존재하면 fail-close 합니다.
- `join_key_mode`와 `ids_map`이 불일치하면 fail-close 합니다.
- 필수 key를 해석할 수 없으면 JOIN은 fail-close 해야 합니다.

## 4. Evidence / Render Contract

### 목적

raw retrieval payload는 canonical evidence와 render profile로 분리 정규화되어야 합니다.

### MUST

- canonical evidence는 `pjt_id`, `pjt_no`, `rst_id`의 source semantics를 보존해야 합니다.
- canonical evidence는 `lead_org_name`, `participant_org_name`, `people_affiliation_org_name`의 역할 의미를 보존해야 합니다.
- output shaping은 `output_type`과 route/mode hint에서 render profile을 결정해야 합니다.
- `summary`, `detail`, `list`, `stats`, `relation`은 같은 context shape를 재사용하면 안 됩니다.
- `mode=join`일 때 `head`는 `relation[1]`과 같아야 합니다.

### MUST NOT

- raw retrieval payload를 prompt에 그대로 넣으면 안 됩니다.
- retrieval view와 prompt view를 같은 것으로 취급하면 안 됩니다.
- `action=relation`을 runtime dialect로 쓰면 안 됩니다.

### Fail-close 조건

- JOIN head semantics가 깨지면 fail-close 해야 합니다.

## 5. Chat UX Boundary

### 목적

chat UX는 retrieval result를 표현하고 대화 흐름을 관리하지만 retrieval contract를 바꾸지 않습니다.

### chat UX가 할 수 있는 것

- canonical evidence를 기반으로 답변을 생성합니다.
- memory와 follow-up UX를 제공합니다.
- streaming과 answer merge를 수행합니다.

### chat UX가 하면 안 되는 것

- retrieval contract를 바꾸지 않습니다.
- canonical evidence 없이 raw payload를 prompt에 dump하지 않습니다.
- retrieval failure를 `context=[]` 성공 payload처럼 숨기지 않습니다.
- `question_analysis`를 final execution strategy처럼 노출하지 않습니다.
- retrieval workflow와 answer generation은 `question_analysis`를 direct truth로 사용하지 않습니다.
- `planner_runtime`의 stagewise 로그와 `PLANNER.ASSEMBLE`은 execution truth가 아닙니다.

### memory 규칙

목적:
- conversation memory의 저장 범위와 retrieval anchor source of truth를 분리합니다.

저장 범위:
- conversation memory는 chat history와 canonical context snapshot을 함께 저장할 수 있습니다.
- canonical context snapshot은 `canonical_evidence`와 `render_profile`입니다.
- memory load contract는 `(history, canonical_evidence, render_profile)` 3-tuple입니다.

- planner가 `mode=join`, `join_key_mode=instance`를 제안하더라도 sanitize 이후 `ids_map.pjt_id` seed가 비고 사람/기관 gate도 없으면 runtime merge 단계에서 `lookup`으로 downgrade한다. 이 경우 relation은 유지해 followup 경로를 열어 둔다.
- `과제번호`처럼 instance/group이 모호한 표현만 있을 때는 `pjt_id`/`pjt_no` 어느 쪽도 seed로 고정하지 않고 post-sanitize re-gate를 다시 태운다.
- `lookup`/`join`에서 `reranked=0`과 `reason=no_reranked`는 strict exception 대신 정상 no-result outcome으로 내리고, observability에는 `info.contract_fail_reason`, `info.empty_result_policy`, `info.reranked_count`를 함께 남긴다.

MUST:
- previous-context loading은 legacy cached context snapshot을 읽지 않아야 합니다.
- stagewise planner는 previous-context prompting과 previous-anchor seed extraction에서 canonical evidence와 render profile를 우선 사용해야 합니다.

MUST NOT:
- history만으로 retrieval anchor를 재구성하면 안 됩니다.
- history가 canonical evidence / render profile source of truth를 대체하면 안 됩니다.

## 6. Runtime 실패 의미

### MUST

- Retrieval 실행 실패를 `context=[]` 형태의 성공 payload로 바꾸면 안 됩니다.
- `/health`는 compiled graph 기준 readiness를 보고해야 합니다.
- Redis/KV degradation은 payload에 계속 노출해야 합니다.
- direct-answer streaming은 하나의 answer stream만 내보내야 합니다.

### MUST NOT

- graph-ready 인스턴스를 Redis/KV degradation만으로 readiness failure로 만들면 안 됩니다.
- 같은 답을 여러 model label로 중복 송출하면 안 됩니다.

### Diagnostic Probe Boundary

MUST:
- `FILTER_MISS_SUSPECTED`는 raw nested payload 검증이 가능할 때만 발생해야 합니다.

MUST NOT:
- `prtcp_mp[]` 같은 raw array object를 직접 읽지 못하면 사람 이름 miss를 추정하면 안 됩니다.
- `payload_get("prtcp_mp[].hm_nm")`처럼 flatten/space-join helper를 거친 값은 diagnostic miss 판정의 근거로 쓰면 안 됩니다.

Fail-close / downgrade:
- raw nested를 볼 수 없으면 diagnostic 결과는 `unknown`으로 남겨야 하며 warning으로 승격하면 안 됩니다.

## 7. Query Graph / Extended Output Contract

### MUST

- Runtime must preserve `query_graph_kind`, `anchor_summary`, `aggregation_kind`, and `series_kind` alongside the existing strategy fields.
- `comparison` and `series` are evidence presentation shapes, not answer style hints.
- Extended output types must still respect the raw -> canonical -> prompt boundary.

### MUST NOT

- Runtime must not re-infer query-graph metadata from scratch after planner/prelude already fixed the plan.
- Aggregation or series intent must not be silently delegated to answer wording logic.

## Comparison Runtime Baseline

- `output_type=comparison` now has active runtime behavior, not metadata-only reservation.
- `action=stats` may coexist with `relation=(project, perf)` when the question asks for project-level perf counts or ranking.
- Runtime aggregation is computed before answer generation and must not be delegated to prompt-only reasoning.
- `AggregationPlan.threshold` is sourced from `min_metric_count` when the query includes threshold language such as `2+ results`.
- Current supported comparison metrics are `paper_count`, `patent_count`, `report_count`, and `perf_total_count`.
- Current supported comparison groupings are `project` and `project_group`.

## Series Runtime Baseline

- `output_type=series` now has active runtime behavior and must not be treated as metadata-only.
- Current supported series axes are `pjt_no` and year-window expansion.
- Series runtime may combine project-group expansion with project-to-performance follow-up, but it must preserve `pjt_id` and `pjt_no` semantics without auto-conversion.
- Series payloads must expose `series_key_kind`, `series_key`, `instance_projects`, `linked_perf`, and `year_buckets` before answer generation.
- Summary/debug observability must record `series_result_count`, `series_bucket_count`, and `failed_step` when series runtime is active.

## Anchor Summary Baseline

- `anchor_summary` must distinguish generic organization anchors from role-specific organization anchors.
- `anchor_summary.org_role_counts.generic` and `anchor_summary.generic_org_count` are observability fields only; they must not erase `lead` / `participant` / `affiliation` semantics.
- `anchor_summary.ambiguities` may include labels such as `org_role_unspecified` or `researcher_org_pair_unresolved` when planner truth contains name-level anchors without a fully fixed role pairing.
- Anchor summaries must not invent new ids or reinterpret `pjt_id` as `pjt_no`.


## Reverse Trace Contract

- `QueryGraphPlan.kind=perf_to_project_to_perf` means runtime executes `lookup_perf -> join_perf_to_project -> followup_project_to_perf`.
- Reverse trace activation must come from planner truth, not query-text regex extraction.
- Runtime preserves `origin_project_count`, `followup_perf_count`, and `reverse_trace_hop_count` in summary/debug metadata.
- Hop3 empty is partial success, not a strict join failure.
## Anchor Resolution Contract

- `ResolvedAnchorSet` is the planner-first execution contract for researcher/org anchors after normalization and contract checks.
- Runtime prelude must compile people/org filters from `ResolvedAnchorSet`, not by re-interpreting raw planner fields downstream.
- Generic organization anchors remain observable as generic anchors. They may only be routed into a role-specific filter when planner truth already fixed `org_role`.
- `anchor_resolution_status` may be `none`, `resolved`, `partial`, or `ambiguous`.
- `ambiguity_codes` may include `researcher_name_only`, `org_role_unspecified`, or `researcher_org_pair_unresolved`.
- Seedless `JOIN(instance)` with only an unresolved researcher/org pair must downgrade to `lookup` instead of forcing strict join execution.
