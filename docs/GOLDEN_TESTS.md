# 골든 테스트 (GOLDEN TESTS)

이 문서는 현재 runtime의 최소 golden-query invariant를 retrieval-first 기준으로 기록합니다.
입문 문서가 아니라 regression / invariant 기준 문서로 사용합니다.

## 현재 기준선

현재 baseline은 다음을 전제로 합니다.
- retrieval correctness가 answer wording보다 우선합니다.
- `apps/core/query_intent.py`의 explicit precheck는 구조적 신호만 제공합니다.
- planner contract enforcement는 `apps/core/planner_contract.py`에 유지됩니다.
- execution mode selection과 runtime enforcement는 final assembled strategy 기준으로 검증됩니다.

## Golden Queries

| ID | Query | Expected mode | Notes |
|---|---|---|---|
| G001 | `AI related projects` | `search` | broad topic query |
| G002 | `1711015550 project detail` | `lookup` | exact project id lookup |
| G003 | `PJT-2020-1234-5678 related outputs` | `join` | group project seed plus perf/output cue should stay join-classified |
| G004 | `Kim researcher projects` | `lookup` | English researcher cue should still become person-constrained lookup/list behavior |
| G005 | `ETRI papers stats 2021 2023` | `lookup` | stats-style perf lookup |

## Invariants

### 1. Strategy Invariants

- planner는 질의마다 하나의 strategy를 확정합니다.
- executor는 확정된 strategy를 재해석하거나 재결정하지 않습니다.
- `SEARCH`, `LOOKUP`, `JOIN`의 계약 경계는 실행 레이어에서 바뀌지 않습니다.
- 사용자 질의 이후 단계별 흐름은 `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`의 artifact handoff와 모순되면 안 됩니다.
- 비어 있는 heuristic precheck payload가 planner 실행을 막으면 안 됩니다.
- org term, year, perf type, title term 같은 non-id heuristic hint도 planner를 거쳐야 합니다.
- explicit identifier precheck는 불필요한 planner round-trip을 건너뛸 수 있습니다.
- `과제고유번호`/`PJT_ID` 라벨이 붙은 영문+숫자 project key는 explicit identifier precheck로 승격될 수 있습니다.
- `과제번호` 단독 표현은 `pjt_id`/`pjt_no`로 고정하지 않고 planner로 넘겨야 합니다.
- 사람 이름, 기관명, 기관 역할 의미를 regex로 전단 복원하지 않아야 합니다.
- planner normalize/merge가 질의 문구만 보고 `participant_org_name` 같은 역할 필드를 자동 승격하면 안 됩니다.

### 2. Join / Filter Invariants

- JOIN은 relation intent와 유효한 join seed를 모두 요구합니다.
- stage2가 잘못 넣은 project key seed가 sanitize에서 제거되면, assemble 단계는 strict JOIN을 유지하지 않고 `lookup`으로 다시 낮춰야 합니다.
- `lookup`/`join`의 `no_reranked`는 exception이 아니라 정상 no-result outcome이어야 합니다.
- relation query는 별도 `action=relation` dialect가 아니라 `action=list` + `output_type=relation`으로 정규화되어야 합니다.
- JOIN에서 `head`는 relation target과 같아야 합니다.
- `join_key_mode=instance`와 `join_key_mode=group`은 상호 배타적이어야 합니다.
- `group + pjt_no 없음`은 자동 보정 없이 계약 위반으로 처리되어야 합니다.
- planner contract 통과 뒤 group JOIN hop2는 `pjt_no` 또는 group expansion으로 얻은 resolved `pjt_id`가 있으면 실행 가능해야 합니다.
- `hop2_key_strategy`는 실제 Hop2 filter key 사용을 반영해야 하며, group fallback 실행 시 `pjt_id_in`으로 기록되어야 합니다.
- group fallback 실행 시 `resolved_runtime_key_kind`는 `pjt_id`로 기록되어야 합니다.
- group fallback 실행 시 strategy / response에도 `join_compile_selection=group_perf_pjt_id_fallback`가 반영되어야 합니다.
- `/query/debug`의 `strategy_summary`는 같은 JOIN 실행 메타를 그대로 보여줘야 하며, `question_analysis`와 혼동되면 안 됩니다.
- 문서의 다이어그램 vocabulary는 `question_analysis`, `normalized_intent`, `strategy`, `canonical_evidence`, `render_profile`를 서로 다른 artifact로 유지해야 합니다.
- SEARCH는 org/person filter를 server-side hard requirement로 두면 안 됩니다.
- LOOKUP의 명시적 identifier는 server-side lookup filter로 전환되어야 합니다.
- JOIN hop2는 무관한 org/title hard gate를 추가하면 안 됩니다.

### 3. Canonical Evidence Invariants

- source payload에 둘 다 있으면 `pjt_id`와 `pjt_no`를 각각 독립적으로 보존해야 합니다.
- lead/participant/affiliation organization semantics를 분리해서 유지해야 합니다.
- retrieval view와 prompt view는 같지 않아야 합니다.
- answer context building은 canonical evidence만 사용해야 하며, 필요 시 raw retrieved document를 요청 시점에 canonicalize 해야 합니다.
- knowledge sufficiency의 previous-context reasoning은 canonicalized context text만 사용해야 합니다.
- planner previous-context prompting과 previous-anchor seed extraction은 canonical evidence snapshot만 사용해야 합니다.

### 4. Render / Output Invariants

- render profile resolution은 people cue가 있는 project lookup을 generic project context가 아닌 people-oriented context로 분류해야 합니다.
- `summary`, `detail`, `list`, `stats`, `relation`은 같은 context shape를 재사용하면 안 됩니다.
- `output_type`은 answer wording이 아니라 evidence presentation shape를 결정해야 합니다.

### 5. Streaming Invariants

- reasoning chunk가 최종 사용자 노출 content에 섞이면 안 됩니다.
- `ttft_any_ms`와 `ttft_content_ms`는 별도로 추적되어야 합니다.
- direct-answer mode는 하나의 답변을 여러 model stream으로 중복 송출하면 안 됩니다.
- 비어 있는 streamed content가 조용히 성공한 답변처럼 처리되면 안 됩니다.

## Validation 방향

권장 테스트:
- mode, relation, join-key invariant를 검증하는 contract test
- planner assembly immutability regression check
- runtime prelude strict contract regression check
- canonical evidence와 render-profile regression check
- streaming과 route behavior를 검증하는 runtime test

### Health

- `/health`는 compiled graph가 있으면 ready를 반환해야 하며, Redis/KV degradation만으로 실패하면 안 됩니다.
- `/health/details`는 degraded dependency 상태를 payload에 계속 보여줘야 합니다.

### Diagnostic Invariants

- 사람 이름 miss diagnostic은 raw nested `prtcp_mp[]` 기준으로만 판정해야 합니다.
- `payload_get("a[].b")`의 flatten 결과나 `prtcp_mp_hm_nm` 같은 집계 preview는 diagnostic evidence로 승격하면 안 됩니다.
- raw nested evidence가 없으면 `FILTER_MISS_SUSPECTED` warning 대신 `unknown` 처리되어야 합니다.

### 6. Query Graph / Extended Render Invariants

- `comparison` and `series` must survive as valid `output_type` values.
- `project -> perf` relation queries must report `query_graph_kind=project_to_perf`.
- `perf -> project` relation queries must report `query_graph_kind=perf_to_project`.
- Stats or comparison requests must not leave `aggregation_kind` empty.
- Project-series questions must not leave `series_kind` empty.

## Comparison Output Invariants

- Comparison aggregation payloads must expose `metric`, `group_by`, `candidate_docs`, `threshold`, `sort_order`, and `rank_items`.
- Each comparison `rank_item` must preserve `group_key`, `pjt_id`, `pjt_no`, `project_title`, `metric_value`, and `supporting_perf_count` when available.
- Comparison rendering must present aggregation payloads without re-counting inside the answer layer.
- Thresholded comparison queries such as `papers >= 2` must record the threshold in both runtime payload and summary observability.

## Series Output Invariants

- `output_type=series` must produce runtime evidence, not planner metadata only.
- Series payloads must preserve `series_key_kind`, `series_key`, `instance_projects`, `linked_perf`, and `year_buckets`.
- `pjt_no`-based series expansion must keep group-key meaning separate from instance-level `pjt_id` keys.
- Series rendering must use the precomputed runtime payload and must not rebuild year buckets inside the answer layer.


## Reverse Trace Invariants

- `perf -> project -> perf` questions must keep the relation chain in runtime payloads and logs.
- Hop3 follow-up performance evidence must de-duplicate the origin performance hit from hop1.
- Reverse trace meaning must come from planner truth, not regex extraction from the raw user query.
## Anchor Resolution Golden Cases

- Researcher + generic org + topic queries must preserve the ambiguity in summary/debug metadata instead of silently coercing the org into a lead/participant role.
- Researcher + org-role queries may compile role-specific filters from resolved anchors without inventing new ids.
- Seedless `JOIN(instance)` with only an unresolved researcher/org pair must downgrade to `lookup`.
