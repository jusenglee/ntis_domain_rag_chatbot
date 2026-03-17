# 계약 (CONTRACT)

이 문서는 planner, executor, runtime의 현재 실행 계약을 기록합니다.

## 현재 기준선

- Runtime entry point: `apps/api/main.py`
- App assembly / workflow DI: `apps/api/app_factory.py`
- Workflow graph definition: `apps/api/services/workflow_builder.py`
- Planner, intent, executor의 기준 구현: `apps/core/*`
- Runtime baseline: staged-only + strict

## Stagewise Planner 계약

### Stage 1 output

허용 필드:
- `action`
- `head`
- `relation_candidate`
- `referential_followup`
- `confidence`

금지 필드:
- `mode`
- `join_key_mode`
- `target_cols`
- `ids_map`
- `filters`
- `retrieval_query`
- `limit`

### Deterministic gate

Deterministic gate가 소유하는 필드:
- `mode`
- `relation`
- `join_key_mode`
- `target_cols`
- `output_type`

규칙:
- `action=topic`이면 기본 `SEARCH`
- `action in {list, detail, stats, download}`이면 기본 `LOOKUP`
- `JOIN`은 relation intent와 join seed가 모두 있을 때만 허용

### Stage 2 output

허용 필드:
- `ids_map`
- `filters`
- `retrieval_query`
- `limit`
- `confidence`

금지 필드:
- `mode`
- `head`
- `action`
- `relation`
- `join_key_mode`
- `target_cols`
- `strategy_version`

### Final assembly

- Executor가 받는 입력은 최종 조립된 strategy object 하나만 유지합니다.
- Executor는 하나의 final strategy만 소비해야 합니다.
- 하위 레이어는 assembly 이후 planner strategy를 다시 추론하면 안 됩니다.

## Strategy 소유권

- `apps/api/services/planner_service.py`가 planner merge와 final planner application을 담당합니다.
- `apps/core/planner_contract.py`가 planner contract validation을 담당합니다.
- `apps/core/query_intent.py`는 heuristic을 파생할 수 있지만 planner contract 규칙을 우회하면 안 됩니다.
- `apps/api/services/request_facade.py`가 request understanding assembly와 explicit-precheck gating을 담당합니다.
- Planner skip은 `ids_map`에 explicit identifier seed가 이미 있을 때만 허용합니다. org/person term, year, perf type, title term 같은 non-id heuristic hint는 planner input으로 남아야 하며 planner skip 조건이 되면 안 됩니다.
- `apps/core/rag_pipeline.py`는 final plan을 실행해야 하며 새 plan을 발명하면 안 됩니다. 세부 흐름은 다음 모듈을 따라야 합니다.
  - runtime prelude normalization / planner-runtime contract enforcement / pre-dispatch compile output: `apps/core/rag_runtime_prelude.py`
  - 기본 SEARCH/LOOKUP orchestration: `apps/core/rag_base_orchestration.py`
  - collection-level LOOKUP server filter: `apps/core/rag_filter_policy.py`
  - JOIN hop / follow-up orchestration: `apps/core/rag_join_orchestration.py`
  - rerank text scoring / title-soft helper: `apps/core/rag_rerank_support.py`
  - runtime logging / timing / request context / code fingerprint: `apps/core/rag_runtime_observability.py`
  - dense retrieval / hybrid-call compatibility / dense-threshold filtering / dense-score weighting: `apps/core/rag_dense_runtime_support.py`
  - payload/meta merge / filter-log serialization / join-key support: `apps/core/rag_executor_support.py`
  - dispatcher helper / request-local embedding cache / join/base runtime bundle wiring: `apps/core/rag_dispatch_runtime.py`
  - shared runtime integrity helper: `apps/core/rag_runtime_safety.py`
- Runtime prelude output은 명시적 `RuntimePreludeResult` 계약으로만 소비해야 하며, `rag_pipeline.py`는 prelude 시절의 로컬 helper나 반환되지 않은 값을 직접 참조하면 안 됩니다.

## Join Key 규칙

- 같은 lookup/join seed에 `pjt_id`와 `pjt_no`를 동시에 넣으면 안 됩니다.
- `join_key_mode=instance`는 `pjt_id` 같은 instance identifier를 사용합니다.
- `join_key_mode=group`는 `pjt_no` 같은 group identifier를 사용합니다.
- 필수 key를 해석할 수 없으면 JOIN은 fail-close 해야 합니다.

## Runtime 구조 메모

- `apps/api/main.py`는 얇게 유지해야 합니다.
- Workflow routing 변경은 `apps/api/services/workflow_builder.py`에 둡니다.
- 기존 `join_analysis` pass-through 단계는 제거되었습니다. 분기는 이제 knowledge sufficiency 직후에 직접 일어납니다.

## Canonical Evidence 와 Rendering

- Retrieval payload는 이후 rendering, answer generation, debugging 전에 canonical evidence로 정규화될 수 있습니다.
- Canonical evidence는 `pjt_id`, `pjt_no`, `rst_id` 같은 identifier의 source semantics를 보존해야 합니다.
- Canonical evidence는 `lead_org_name`, `participant_org_name`, `people_affiliation_org_name` 같은 role-bearing organization/person field semantics를 보존해야 합니다.
- Output shaping은 raw retrieval payload 구조를 레이어 사이에 누수시키는 대신 `output_type`과 route/mode hint에서 render profile을 결정해야 합니다.
- Answer generation은 canonical evidence text만 사용해야 합니다. 요청 시점 문서만 있고 canonical evidence가 없다면 prompting 전에 runtime이 canonicalize 해야 합니다.
- Knowledge sufficiency도 cached context document에서 재구성할 수 있다면 canonicalized previous context를 우선 사용해야 합니다.
- Conversation memory는 canonical evidence와 render profile만 저장합니다. previous-context loading은 더 이상 legacy cached context snapshot을 읽지 않습니다.
- Stagewise planner는 previous-context prompting과 previous-anchor seed extraction에서 canonical evidence가 있으면 그것을 사용해야 합니다.

## JOIN head 의미

- `action=relation`은 유효한 runtime strategy dialect가 아닙니다. relation query는 `action=list`와 `output_type=relation`으로 정규화해야 합니다.
- `mode=join`일 때 `head`는 relation target, 즉 `relation[1]`과 같아야 합니다.
- Stagewise gate, planner merge, runtime validator는 동일한 JOIN head semantics를 강제해야 합니다.

## Runtime 실패 의미

- Retrieval 실행 실패를 `context=[]` 형태의 성공 payload로 바꾸면 안 됩니다.
- `/health`는 compiled graph 기준 readiness를 보고합니다. Redis/KV degradation은 payload에 계속 노출해야 하지만, graph-ready 인스턴스를 그 이유만으로 readiness failure로 만들면 안 됩니다.
- Direct-answer streaming은 하나의 answer stream만 내보내야 하며, 같은 답을 여러 model label로 중복 송출하면 안 됩니다.

