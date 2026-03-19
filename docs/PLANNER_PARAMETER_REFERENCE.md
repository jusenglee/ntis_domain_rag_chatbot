# 검색 플래너 파라미터 레퍼런스

이 문서는 NTIS Domain RAG의 검색 플래너와 실행 전략에서 사용되는 핵심 파라미터를 설명하는 통합 레퍼런스입니다.
신규 참여자가 소스코드를 처음부터 모두 따라가지 않아도 `질의 해석 -> 전략 조립 -> 실행 -> 근거 정규화` 흐름에서 어떤 값이 언제 만들어지고 어떻게 소비되는지 이해할 수 있도록 구성합니다.

## 문서 역할

- 이 문서는 파라미터 정의와 아티팩트 소유권(artifact ownership)의 기준 문서입니다.
- 단계별 흐름은 [SYSTEM_FLOW_RETRIEVAL_FIRST.md](/D:/Project/python_project/ntis_domain_rag_chatbot/docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md)를 우선합니다.
- strict contract와 fail-close 규칙은 [CONTRACT.md](/D:/Project/python_project/ntis_domain_rag_chatbot/docs/CONTRACT.md)를 우선합니다.
- SEARCH / LOOKUP / JOIN 결정 규칙은 [MODE_DECISION_GUIDE.md](/D:/Project/python_project/ntis_domain_rag_chatbot/docs/MODE_DECISION_GUIDE.md)를 우선합니다.

## 먼저 읽기

신규 참여자는 아래 순서로 읽는 것을 권장합니다.

1. [ARCHITECTURE_RETRIEVAL_FIRST.md](/D:/Project/python_project/ntis_domain_rag_chatbot/docs/ARCHITECTURE_RETRIEVAL_FIRST.md)
2. [SYSTEM_FLOW_RETRIEVAL_FIRST.md](/D:/Project/python_project/ntis_domain_rag_chatbot/docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md)
3. [MODE_DECISION_GUIDE.md](/D:/Project/python_project/ntis_domain_rag_chatbot/docs/MODE_DECISION_GUIDE.md)
4. 이 문서
5. [CONTRACT.md](/D:/Project/python_project/ntis_domain_rag_chatbot/docs/CONTRACT.md)

## 핵심 아티팩트(artifact) 요약

| 아티팩트(artifact) | 생성 단계 | 소유 레이어 | 아래 레이어의 source of truth |
|---|---|---|---|
| `question_analysis` | Query Understanding / Planner | planner artifact | 아님. fallback 힌트 전용 |
| `intent_payload.normalized_intent` | Query Understanding | retrieval intent canonical input | 예 |
| `strategy` | Strategy Assembly / Runtime Prelude | execution strategy | 예 |
| `QueryPlan` | Runtime query planning | execution plan | 실행 시 예 |
| `canonical_evidence` | Evidence Assembly | retrieval result normalization | 예 |
| `render_profile` | Evidence Assembly | evidence presentation contract | 예 |

## 플래너 페이로드(planner payload) 파라미터

이 섹션은 `apps/api/contracts/workflow_models.py::QuestionAnalysisV2` 기준입니다.

### `mode`

- 정의: planner가 제안한 검색 모드(retrieval mode)
- 허용값: `SEARCH | LOOKUP | JOIN`
- 생성 단계: planner payload normalize 이후
- 주의:
  - planner artifact의 값이며, execution truth는 final `strategy.mode`입니다.
  - non-JOIN인데 `join_key_mode`를 같이 확정하려 하면 contract 위반입니다.

### `head`

- 정의: 질의의 주 대상 엔터티(primary target entity)
- 허용값: `project | perf | people | org | support`
- 생성 단계: stagewise planner stage1
- 사용 위치:
  - planner assemble
  - JOIN head / relation target consistency 검증
- 주의:
  - JOIN에서는 `relation[1]`과 같아야 합니다.

### `action`

- 정의: 검색 행동 유형(retrieval action)
- 허용값: `topic | list | detail | stats | download`
- 생성 단계: stagewise planner stage1
- mode 결정과의 관계:
  - `topic`은 기본적으로 SEARCH 계열
  - `list | detail | stats | download`는 기본적으로 LOOKUP 계열
  - relation과 join seed가 있으면 JOIN으로 승격될 수 있습니다.

### `relation`

- 정의: 관계 질의(relation query)의 방향
- planner payload 표현: `project_perf | perf_project | null`
- canonical 표현: `("project", "perf") | ("perf", "project") | None`
- 생성 단계: planner stage1 candidate -> deterministic gate normalize
- 주의:
  - people/org relation은 strict 기본 경로에서 금지됩니다.
  - relation alone이 아니라 relation + join seed가 있어야 JOIN 실행이 허용됩니다.

### `join_key_mode`

- 정의: JOIN이 어떤 과제 키 의미(key semantics)를 사용하는지 나타내는 값
- 허용값: `instance | group | null`
- 생성 단계: planner normalize / deterministic gate
- 의미:
  - `instance`: `pjt_id` 계열
  - `group`: `pjt_no` 계열
- 주의:
  - `pjt_id`와 `pjt_no`는 같은 값처럼 취급하면 안 됩니다.
  - `mode != JOIN`이면 반드시 `null`이어야 합니다.

### `target_cols`

- 정의: planner가 의도한 검색 대상 컬렉션(collection) 후보
- 예시: `["ntis_project_v1"]`, `["ntis_project_v1", "ntis_perf_v1"]`
- 생성 단계: deterministic gate / relation resolution
- 주의:
  - execution-layer에서는 `target_collections`가 최종 실행용 표현입니다.
  - relation query면 relation target collections와 모순되면 안 됩니다.
  - `people/org` 질의는 기본적으로 `project + perf` 양쪽 탐색을 전제로 합니다.

### `ids_map`

- 정의: 식별자 시드(identifier seed)를 key별로 정규화한 맵(map)
- 생성 단계: planner payload normalize
- 대표 key:
  - `pjt_id`
  - `pjt_no`
  - `rst_id`
  - `doi`
  - `patent_no`
- 주의:
  - `pjt_id`는 과제 instance key입니다.
  - `pjt_no`는 과제 group key입니다.
  - 같은 lookup/join seed에 둘을 동시에 넣으면 안 됩니다.
  - 사람 이름, 기관 이름을 `ids_map`에 임의로 넣지 않습니다.

### `filters`

- 정의: planner가 stage2에서 채우는 구조화 제약 묶음(structured constraint bundle)
- 생성 단계: stage2
- 포함될 수 있는 값:
  - 기관 역할 필드
  - 사람 이름 필드
  - 연도, 성과유형, title term 기반 constraint
- 주의:
  - planner payload의 filter는 실행 compile 전 상태입니다.
  - Qdrant filter와 동일한 개념이 아닙니다.

### `limit`

- 정의: planner가 제안한 retrieval limit
- 생성 단계: stage2
- execution-layer 대응값: `planner_limit`
- 주의:
  - execution 단계에서는 다른 policy와 함께 재해석될 수 있으나, planner artifact 자체는 그대로 보존됩니다.

### `retrieval_query`

- 정의: retrieval을 위해 planner가 제안한 정규 질의 문자열(canonical query text)
- 생성 단계: stage2
- 실행 우선순위:
  - `knowledge_sufficiency.retrieval_query`
  - `normalized_intent.retrieval_query`
  - `question_analysis.retrieval_query`
- 주의:
  - planner artifact의 값이지만 retrieval workflow에서 중요한 fallback 입력입니다.

### `confidence`

- 정의: planner output confidence
- 생성 단계: stage1 / stage2
- 사용 위치:
  - planner quality 관측
  - 일부 runtime policy 보조 힌트
- 주의:
  - execution truth를 직접 대체하지 않습니다.

## 정규화된 의도(normalized intent) 파라미터

이 섹션은 `apps/core/pipeline_steps.py::NormalizedIntent`와 `apps/core/schemas.py::ExecutionContext.from_intent()` 기준입니다.

### `base_route`

- 정의: retrieval이 기본적으로 시작할 엔터티 경로(entity route)
- 대표값: `project | perf | people | org | support`
- 사용 위치:
  - default target collections
  - render profile / output shaping
- 주의:
  - `base_route=people|org`는 질의 중심 엔터티를 뜻할 뿐 데이터 존재 범위를 `project` 하나로 제한하지 않습니다.

### `output_type`

- 정의: 답변 문체(answer wording)가 아니라 근거 표현 형태(evidence presentation shape)
- 대표값: `summary | detail | list | stats | relation`
- 사용 위치:
  - context shaping
  - render profile resolution
- 금지 해석:
  - raw retrieval payload shape와 같은 개념으로 보면 안 됩니다.

### `planner_limit`

- 정의: normalized intent가 carry하는 planner 유래 제한값(planner-derived limit)
- source: planner payload `limit`
- 사용 위치:
  - retrieval workflow
  - search execution limit

### `planner_confidence`

- 정의: normalized intent가 carry하는 planner 신뢰도(confidence)
- 사용 위치:
  - 보조 policy / observability

### 사람/기관 힌트 계열 필드

- `people_terms`
- `org_terms`
- `lead_org_terms`
- `participant_org_terms`
- `people_affiliation_org_terms`

의미:

- 사람/기관 힌트를 역할별로 분리 정규화한 값입니다.
- 수행기관, 참여기관, 참여인력 소속기관은 서로 다른 의미입니다.
- 이름 기반 signal은 mode 결정에서 LOOKUP 쪽 신호로 작동할 수 있습니다.

### 연도/성과 힌트 계열 필드

- `years`
- `year_from`
- `year_to`
- `perf_types`
- `perf_tag_filters`
- `project_tag_filters`

의미:

- 연도 조건과 성과 중심 힌트를 구조화한 값입니다.
- filter compile과 retrieval emphasis 판단에 사용됩니다.

### `is_id_query`

- 정의: explicit id query 여부
- mode 결정에 미치는 영향:
  - true면 LOOKUP 계열 강한 신호입니다.

## 실행 전략(execution strategy) 파라미터

이 섹션은 `apps/core/schemas.py::StrategySpec` 기준입니다.
즉, 최종 execution strategy 타입은 `StrategySpec`입니다.

### 기본 전략 필드

| 필드 | 의미 | 비고 |
|---|---|---|
| `mode` | final execution mode | execution truth |
| `action` | final action | planner action normalize 결과 |
| `relation` | canonical relation tuple | `("project", "perf")` 등 |
| `join_key_mode` | JOIN key semantics | `instance | group | None` |
| `join_key_source` | join seed source | observability 용도 |
| `hop1_mode` | JOIN hop1 실행 mode | observability 용도 |
| `target_collections` | 최종 실행 대상 collection | planner `target_cols`와 구분 |

### JOIN 실행 메타(metadata)

| 필드 | 의미 |
|---|---|
| `join_compile_selection` | Hop2 compile path |
| `hop2_key_strategy` | 실제 Hop2 filter key shape |
| `resolved_runtime_key_kind` | materialized runtime key kind |
| `join_keys_used_count` | 실제 실행에 사용한 join key 수 |

JOIN 메타 주의:

- planner artifact가 아니라 실행 관측 메타(execution observability)입니다.
- `/query/debug.strategy_summary`와 `REQ.SUMMARY`에 노출될 수 있습니다.
- planner seed와 같은 레벨의 truth로 읽으면 안 됩니다.

### 필터/런타임 정책(filter/runtime policy) 필드

| 필드 | 의미 | 대표값 |
|---|---|---|
| `search_filter_enabled` | SEARCH server-side filter 활성 여부 | `true | false` |
| `lookup_filter_enabled` | LOOKUP filter 활성 여부 | `true | false` |
| `relation_lookup_enforce` | relation lookup constraint 강화 여부 | `true | false` |
| `lookup_filter_policy` | LOOKUP filter 정책 | `hard | off | must_one_then_should` |
| `lookup_filter_min_should` | LOOKUP 최소 should 개수 | 정수 |
| `lookup_filter_gate` | LOOKUP gate mode | 구현별 문자열 |
| `lookup_filter_promote_one_must` | should -> must 승격 여부 | `true | false` |
| `lookup_title_filter_policy` | LOOKUP title 정책 | 현재 기본 `soft` |
| `title_match_mode` | title match mode | 현재 기본 `CONTAINS` |
| `search_filter_server_policy` | SEARCH filter server policy | 구현별 문자열 |

## 실행 계획/실행 문맥(QueryPlan / ExecutionContext) 파라미터

이 섹션은 `apps/core/schemas.py::QueryPlan`, `ExecutionContext` 기준입니다.
최종 실행 계획 타입은 `QueryPlan`입니다.

### `QueryPlan`

- 정의: retrieval runtime이 직접 소비하는 실행 계획(execution plan)
- 주요 필드:
  - `mode`
  - `base_route`
  - `action`
  - `relation`
  - `join_key_mode`
  - `output_type`
  - `target_collections`
  - `filters`
  - `stats_metric`, `window_years`, `candidate_n`, `top_k`, `tie_break`

### `ExecutionContext`

- 정의: request 1건 동안 runtime이 carry하는 가변 문맥(mutable context)
- source:
  - normalized intent에서 시작
  - 이후 `plan`, `strategy`, `target_collections`가 채워짐
- 신규 참여자가 먼저 봐야 할 필드:
  - `base_route`
  - `mode`
  - `relation`
  - `join_key_mode`
  - `planner_limit`
  - `retrieval_query`
  - `ids_map`
  - `target_collections`
  - `plan`
  - `strategy`

## 필드 생성/소비 단계 표(matrix)

| 필드 | 생성 단계 | 주요 소비 단계 |
|---|---|---|
| `question_analysis.mode` | planner stage | planner drift 진단 |
| `normalized_intent.base_route` | query understanding | query plan, render profile |
| `normalized_intent.output_type` | query understanding | evidence assembly, answer generation |
| `normalized_intent.ids_map` | query understanding | planner contract, runtime prelude |
| `strategy.mode` | strategy assembly | runtime prelude, orchestration, debug/summary |
| `strategy.target_collections` | query plan build | retrieval execution |
| `strategy.join_compile_selection` | join runtime compile | debug/summary |
| `canonical_evidence` | evidence assembly | answer generation, memory |
| `render_profile` | evidence assembly | context shaping, answer generation |

## 자주 헷갈리는 항목

### `question_analysis.mode` vs `strategy.mode`

- `question_analysis.mode`는 planner artifact입니다.
- `strategy.mode`는 final execution truth입니다.
- debug 응답에서도 execution 판단은 `strategy_summary.mode`를 우선합니다.

### `target_cols` vs `target_collections`

- `target_cols`: planner payload의 collection 후보
- `target_collections`: 최종 실행용 collection tuple
- `people/org`의 `target_collections` 기본값은 `project + perf`입니다.

### `filters` vs compiled filter

- `filters`: planner가 stage2에서 채운 구조화 힌트
- compiled filter: runtime compile 이후 실제 retrieval에 쓰는 필터(filter)

### `output_type` vs answer style

- `output_type`은 근거 표현 형태(evidence presentation shape)입니다.
- 답변 문체나 tone을 직접 결정하는 값이 아닙니다.

## 신규 참여자 체크리스트

- 먼저 `strategy`와 `QueryPlan`을 보고 execution truth를 파악합니다.
- 그 다음 `normalized_intent`를 보고 왜 그 전략이 조립됐는지 봅니다.
- planner 이상 여부가 궁금할 때만 `question_analysis`를 봅니다.
- JOIN이면 `join_key_mode`, `ids_map`, `join_compile_selection`, `hop2_key_strategy`를 함께 봅니다.
- evidence/answer 문제를 볼 때는 `canonical_evidence`와 `render_profile`을 우선합니다.
