# PLANNER_PARAMETER_REFERENCE

이 문서는 planner artifact, normalized intent, runtime prelude, execution layer에서 사용하는 핵심 파라미터를 정리한다.

실행 source of truth:
- schema: `apps/core/schemas.py`
- planner merge/runtime prelude: `apps/api/services/planner_service.py`, `apps/core/rag_runtime_prelude.py`
- 계약 문서: `docs/CONTRACT.md`

## 기본 원칙

- planner artifact가 execution truth를 직접 대체하지 않는다.
- `ids_map`에는 resolved identifier만 들어간다.
- unresolved exact key는 `candidate_keys`에 들어간다.
- `IntentPayloadV3`는 transport contract이다.
- `strategy_version="v3"`는 semantic contract이다.

## 주요 artifact

| artifact | 역할 | source of truth |
|---|---|---|
| `question_analysis` | planner와 deterministic gate가 조립한 질의 해석 | planner/runtime assemble |
| `intent_payload.normalized_intent` | retrieval가 소비하는 canonical intent | request facade |
| `strategy` | executor가 실제로 실행하는 전략 | runtime prelude |
| `QueryPlan` | planner/runtime이 공유하는 plan metadata | pipeline steps |
| `canonical_evidence` | raw retrieval를 renderer 친화 형태로 정규화한 결과 | result assembly |
| `render_profile` | `output_type`별 evidence rendering shape | context builder |

## planner stage 1

stage 1은 초기 구조 해석만 수행한다.

- `action`
- `head`
- `relation_candidate`
- `referential_followup`
- `confidence`

stage 1은 다음 값을 확정하지 않는다.

- `mode`
- `join_key_mode`
- `target_cols`
- `ids_map`
- `candidate_keys`
- `filters`

## deterministic gate

deterministic gate는 다음 값을 고정한다.

- `mode`
- `action`
- `relation`
- `join_key_mode`
- `target_cols`
- `output_type`

planner stage 2와 lower layer는 이 값을 바꾸지 않는다.

## planner stage 2

stage 2는 실행에 필요한 가변 슬롯만 채운다.

- `ids_map`
- `candidate_keys`
- `project_key_policy`
- `join_resolution_policy`
- `filters`
- `retrieval_query`
- `limit`
- `confidence`

### `ids_map`

- resolved identifier만 담는다.
- 주요 key:
  - `pjt_id`
  - `pjt_no`
  - `rst_id`
  - `doi`
  - `issn`
- `pjt_id`와 `pjt_no`를 동시에 resolved seed로 올리면 안 된다.

### `candidate_keys`

- unresolved exact key를 담는다.
- 주요 key:
  - `candidate_keys.project_key`
  - `candidate_keys.perf_key`
- `과제번호`는 기본적으로 `candidate_keys.project_key`로 보낸다.

### `project_key_policy`

- `resolved_pjt_id`
- `resolved_pjt_no`
- `ambiguous_or`

`ambiguous_or`는 exact OR exact discovery를 의미하며 free-text fallback을 허용하지 않는다.

### `join_key_mode`

- `instance`
- `group`
- `deferred`

`deferred`는 runtime이 hop1 discovery 뒤 key kind를 결정한다.

### `join_resolution_policy`

지원 값:
- `auto_resolve`
- `dual_branch`

`resolved_join_key_mode`는 planner 필드가 아니라 runtime 산출물이다.

## normalized intent

normalized intent는 retrieval가 소비하는 canonical 질의 객체다.
주요 필드:

- `base_route`
- `action`
- `relation`
- `output_type`
- `planner_limit`
- `planner_confidence`
- `people_terms`
- `org_terms`
- `lead_org_terms`
- `participant_org_terms`
- `people_affiliation_org_terms`
- `years`, `year_from`, `year_to`
- `perf_types`
- `perf_tag_filters`, `project_tag_filters`
- `ids_map`
- `candidate_keys`
- `project_key_policy`
- `join_resolution_policy`
- `ids_flat`
- `is_id_query`
- `has_project_candidate_key`
- `has_perf_candidate_key`
- `is_exact_key_query`

## transport contract

- `IntentPayloadV3`는 transport version과 semantic version을 함께 실어 나른다.
- `question_analysis`와 `strategy_meta`는 transport에서 유지된다.
- payload가 존재하면 `intent_payload_version="v3"`가 필수다.

## runtime contracts

- `StrategySpec`은 runtime prelude가 조립하는 실행 계약이다.
- `QueryPlan`은 planner/runtime이 공유하는 계획 메타데이터다.
- `mode`, `action`, `relation`, `join_key_mode`, `ids_map`, `target_cols`, `retrieval_query`, `planner_limit`, `output_type`는 transport와 runtime에서 함께 유지한다.

## 점검 체크

- planner artifact가 execution truth를 덮어쓰지 않는가
- resolved key와 candidate key가 섞이지 않는가
- planner-first 원칙을 지키고 raw text를 다시 해석하지 않는가
- observability 필드가 summary/debug에 그대로 노출되는가
