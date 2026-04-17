# ADR-0011: Evidence Projection Bundle and Visible Manifest Boundary

## Status
Proposed

## Date
2026-04-14

## Context
이 저장소는 이미 planner strategy immutability, SEARCH/LOOKUP/JOIN gate, bounded orchestrator ownership을 강하게 유지하고 있다. 현재 기준선에서 retrieval 의미 자체는 비교적 안정적이지만, retrieval 이후의 evidence projection 경계는 아직 transitional 상태다.

현행 구조는 아래 흐름을 갖는다.

- `apps/evidence/context_build_policy.py`는 `prompt_units`, serialized `context`, `refs`, `fieldset`, `render_profile`, `canonical_evidence`를 함께 만든다.
- `apps/evidence/rag_result_assembly.py`는 이 bundle을 `RagResult`에 싣고 retrieval 쪽 observability를 기록한다.
- `apps/retrieval/retrieval_workflow.py`는 다시 `DisplaySnapshot`, detail cache, `answer_context_text`, `debug_answer_context_text`, `render_profile`, `canonical_evidence`를 workflow state에 병합한다.
- `apps/chat/answer_generation.py`는 pipeline이 미리 준 `answer_context_text`를 우선 사용하지만, 없으면 `canonical_evidence`를 다시 렌더링해 context를 복원한다.
- `apps/conversation/view_state.py`는 `DisplaySnapshot`을 active scope truth로 쓰는 동시에 `visible_answer_manifest`도 같은 구조로 저장한다.

이 ADR은 retrieval semantics를 바꾸지 않는다. 아래 기존 불변 조건은 그대로 유지한다.

- planner strategy는 요청마다 하나이며 immutable이다.
- `SEARCH`는 recall-first이며 hidden hard-must를 추가하지 않는다.
- `LOOKUP`과 `JOIN`은 precision-first hard gating을 유지한다.
- people/org 기본 처리 축은 lookup-oriented다.
- hidden fallback chat escape hatch는 허용하지 않는다.
- `LOOKUP`/`JOIN`에 BM25-only shortcut을 열지 않는다.

## current_state_summary

- prompt-facing truth는 이미 `prompt_units -> serialized context` 흐름으로 상당 부분 정리돼 있다.
- canonical truth는 `canonical_evidence`와 compressed raw payload memory에 분산돼 있다.
- visible follow-up truth는 `DisplaySnapshot`, `active_scope`, `visible_answer_manifest`에 걸쳐 존재한다.
- answer stage는 groundedness/state-consistency gate 뒤에 manifest publication 여부를 결정한다.
- 하지만 prompt, display, visible-output surfaces가 하나의 typed projection contract로 묶여 있지 않아, 같은 retrieval 결과가 서로 다른 경로로 재조립될 수 있다.

## pain_points

### 1. projection truth가 여러 surface에 중복된다

현재 한 번의 retrieval 결과에서 최소 아래 artifact가 병렬로 생긴다.

- `prompt_units`
- serialized `context`
- `canonical_evidence`
- `render_profile`
- `answer_context_text`
- `DisplaySnapshot`
- `visible_answer_manifest`

이 중 일부는 source-of-truth이고 일부는 projection인데, 코드상에서 typed ownership이 명확히 구분되지 않는다.

### 2. `output_type` / `fieldset` 보장이 primary path에만 강하다

`context_build_policy.py`는 `output_type`별 fieldset을 엄격히 선택하지만, `apps/evidence/canonical_context.py`의 fallback renderer는 `render_profile`을 받아도 사실상 거의 모든 핵심 필드를 다시 풀어 쓴다. 즉, primary prompt path와 fallback prompt path가 같은 projection contract를 강제하지 않는다.

### 3. display truth와 published visible truth가 같은 모델에 묶여 있다

`DisplaySnapshot`은 conversation follow-up, active scope, ordered visible items를 위한 state artifact다. 그런데 같은 구조가 `visible_answer_manifest` publication에도 재사용된다. 이로 인해 "현재 retrieval list truth"와 "사용자에게 publish 승인된 visible truth"의 경계가 모델 수준에서 분리되지 않는다.

### 4. detail/short-circuit 경로가 projection invalidation 규칙을 숨긴다

detail cache hit, clarification short-circuit, deterministic visible list, degraded final answer는 모두 합법적인 경로다. 하지만 어떤 경로가 기존 prompt/display projection을 그대로 재사용하고, 어떤 경로가 재조립해야 하는지에 대한 invalidation 규칙은 코드에 흩어져 있다.

### 5. 회귀 기준이 projection parity를 직접 증명하지 않는다

현재 테스트는 groundedness, state consistency, follow-up candidate, execution policy를 부분적으로 검증한다. 그러나 "같은 retrieval 결과가 prompt/display/visible output에서 같은 ordering, same ids, same context_kind를 유지하는가"를 직접 증명하는 contract lane은 약하다.

## proposed_target_state

### 1. `EvidenceProjectionBundle`을 retrieval/evidence 경계의 유일한 projection source로 둔다

이 artifact는 retrieval semantics가 아니라 retrieval 이후 projection truth를 고정한다.

구성:

- `profile_contract`
  - `mode`
  - `base_route`
  - `output_type`
  - `context_kind`
  - `fieldset`
- `canonical_projection`
  - canonical evidence items
  - source ordering
  - source counts
- `prompt_projection`
  - `prompt_units`
  - serialized `context`
  - `refs`
  - token/budget diagnostics
- `display_projection`
  - ordered display items
  - requested/visible/raw counts
  - list/detail follow-up seed surface
- `diagnostics`
  - additive only
  - execution truth나 user-visible truth로 승격되지 않음

규칙:

- `context`, `DisplaySnapshot`, answer-stage manifest candidate는 모두 같은 `EvidenceProjectionBundle`에서 파생된다.
- `output_type`과 `fieldset`은 profile contract에서 한 번만 결정되고 downstream에서 재발명하지 않는다.
- canonical truth와 display truth는 분리하되 ordering lineage는 공유한다.

### 2. `VisibleAnswerManifestPublication`을 별도 artifact로 분리한다

answer stage는 retrieval list state를 직접 publish하지 않고, projection bundle의 `display_projection`을 입력으로 받아 publication record를 만든다.

구성:

- `publication_status`
  - `approved`
  - `withheld_partial`
  - `blocked_groundedness`
  - `blocked_state_consistency`
  - `not_applicable`
- `published_manifest`
  - transport/UI에 노출 가능한 visible snapshot
- `gate_reason_codes`
- `required_visible_count`
- `accepted_visible_count`

규칙:

- `DisplaySnapshot`은 conversation-owned follow-up truth다.
- `VisibleAnswerManifestPublication`은 answer-owned transport truth다.
- 둘은 같은 source ordering을 공유할 수 있지만 같은 artifact를 재사용하지 않는다.

### 3. fallback renderer는 projection contract를 따라야 한다

fallback path는 없어질 필요는 없지만, primary prompt path와 다른 semantics를 만들면 안 된다.

허용 범위:

- `prompt_projection.context`가 없을 때만 fallback rendering 허용
- fallback renderer도 `profile_contract.fieldset`과 `context_kind`를 따라야 함
- debug rendering은 더 자세할 수 있지만 user/model-facing rendering과 같은 truth surface를 덮어쓰지 않음

### 4. invalidation 규칙을 artifact 기준으로 명시한다

아래 이벤트는 additive diagnostics가 아니라 projection invalidation event로 다뤄야 한다.

- detail cache hit
- clarification short-circuit
- deterministic visible list
- degraded final answer
- active scope refresh

각 이벤트는 "어떤 projection을 재사용하고", "어떤 projection을 폐기하며", "어떤 publication만 다시 계산하는지"를 명시해야 한다.

## staged_migration_plan

### Phase 0. Contract Inventory

- 현행 producer/consumer를 inventory한다.
- `prompt_units`, `context`, `canonical_evidence`, `answer_context_text`, `DisplaySnapshot`, `visible_answer_manifest`를 각각 source / derived / publication / diagnostics로 태깅한다.
- 문서에 현재 invalidation 규칙과 예외 경로를 먼저 적는다.

Exit criteria:

- 모든 projection surface가 owner와 consumer를 가진다.
- `output_type`/`fieldset`/`context_kind`를 누가 확정하는지 문서로 고정된다.

### Phase 1. Typed Mirror Models

- `EvidenceProjectionBundle`
- `PromptProjection`
- `DisplayProjection`
- `VisibleAnswerManifestPublication`

위 네 typed mirror를 추가한다.

규칙:

- 기존 dict/state surface는 그대로 두고 pure adapter만 추가한다.
- runtime behavior는 바꾸지 않는다.

Exit criteria:

- 현재 workflow state만으로 typed projection mirror를 재구성할 수 있다.
- import-light tests에서 legacy surface와 typed mirror의 parity를 비교할 수 있다.

### Phase 2. Fieldset Parity Enforcement

- fallback renderer가 `fieldset`을 따르도록 정렬한다.
- `render_profile` 없는 ad hoc rendering을 줄인다.
- prompt/debug/display 경로의 projection parity를 테스트로 고정한다.

Exit criteria:

- 동일 input에 대해 primary prompt path와 fallback prompt path가 같은 profile contract를 따른다.
- list/relation/comparison/series/detail별 visible field drift가 줄어든다.

### Phase 3. Manifest Publication Split

- `visible_answer_manifest`를 `DisplaySnapshot` 직접 재사용에서 분리한다.
- answer stage는 publication record를 만들고 route는 그것만 transport truth로 사용한다.
- follow-up resolution은 계속 conversation-owned `DisplaySnapshot`을 사용한다.

Exit criteria:

- follow-up truth와 transport truth가 모델 수준에서 분리된다.
- groundedness/state-consistency gate 결과가 publication artifact에 직접 남는다.

### Phase 4. Consumer Narrowing

- answer generation, route, follow-up helper가 각자 필요한 projection만 읽도록 줄인다.
- diagnostics surface가 projection truth를 대체하지 못하게 한다.
- 불필요해진 compatibility surface를 단계적으로 제거한다.

Exit criteria:

- prompt/display/visible truth는 typed projection으로 추적 가능하다.
- debug field 추가가 runtime truth drift를 만들지 않는다.

## first_safe_step

가장 안전한 첫 단계는 typed mirror model과 inventory 문서화다.

구체적으로:

1. `EvidenceProjectionBundle`과 `VisibleAnswerManifestPublication` mirror model을 추가한다.
2. `retrieval_workflow.py`와 `answer_generation.py`에서 현재 state를 이 mirror로 투영하는 pure adapter만 만든다.
3. projection parity를 확인하는 import-light tests를 추가한다.

이 단계가 안전한 이유:

- planner semantics를 건드리지 않는다.
- SEARCH/LOOKUP/JOIN gate를 바꾸지 않는다.
- hidden fallback chat이나 mode drift를 만들지 않는다.
- 이후 삭제/정리 작업이 evidence 기반이 된다.

하지 말아야 할 것:

- `retrieval_workflow.py`를 한 번에 분해하지 않는다.
- `DisplaySnapshot`을 바로 삭제하지 않는다.
- prompt packing과 visible manifest gating을 같은 변경에서 동시에 재작성하지 않는다.

## files_to_create_or_update

### This ADR run

- `apps/docs/ADR/ADR-0011-evidence-projection-bundle-and-visible-manifest-boundary.md`

### Recommended first implementation wave

- create `apps/evidence/projection_contract.py`
- create `apps/api/contracts/visible_answer_manifest_publication.py`
- update `apps/evidence/context_build_policy.py`
- update `apps/evidence/canonical_context.py`
- update `apps/retrieval/retrieval_workflow.py`
- update `apps/chat/answer_generation.py`
- update `apps/conversation/view_state.py`
- update `apps/api/routes.py`
- update `apps/docs/03_행동안전(L2)_정책과_보정.md`
- update `apps/docs/04_도구화_규격과_상태_격리.md`
- update `apps/docs/05_단계적_리팩토링_로드맵.md`
- add tests such as:
  - `tests/test_evidence_projection_contract.py`
  - `tests/test_visible_answer_manifest_publication.py`
  - `tests/test_projection_fieldset_parity.py`

## risks_and_unknowns

- 현재 `DisplaySnapshot`은 follow-up resolution, recent mentions, active scope promotion에 이미 깊게 연결돼 있어, manifest split 시 conversation features가 예상보다 많이 영향을 받을 수 있다.
- detail cache hit 경로와 deterministic visible list 경로는 retrieval 결과 없이 answer-stage projection을 재조립할 수 있으므로, inventory 없이 contract split을 시작하면 회귀가 나기 쉽다.
- 일부 route/debug path는 아직 legacy state surface를 기대할 수 있어, typed projection 도입 초기에 surface duplication이 일시적으로 늘어난다.
- `prompt_units`가 internal truth라는 문서 기준은 이미 존재하지만, current worktree의 docs tree는 과거 handoff/ADR 흔적과 일부 분리돼 있어 implementation 전에 문서 동기화가 필요할 수 있다.

## Trade-Offs

### Positive

- `output_type -> fieldset -> prompt/display/manifest` 흐름이 하나의 projection contract로 추적된다.
- visible follow-up truth와 transport-visible truth를 분리해 future follow-up regressions를 줄일 수 있다.
- fallback renderer가 secondary truth source가 되는 문제를 줄인다.
- observability와 regression docs가 artifact 기준으로 정리된다.

### Costs

- mirror model 단계에서는 surface duplication이 일시적으로 늘어난다.
- typed contract를 도입해도 당장은 legacy state 병행이 필요하다.
- `retrieval_workflow.py`, `view_state.py`, `answer_generation.py` 사이의 경계가 더 엄격해져 초기 마이그레이션 비용이 생긴다.

## Prerequisites

- projection surface inventory
- list/detail/clarification/degraded path 샘플 회귀 정리
- current docs tree와 legacy handoff 흔적 간 불일치 확인

## Rollback

typed mirror 단계에서 complexity만 늘고 ownership clarity가 개선되지 않으면, 이 ADR은 documentation-only로 남기고 Phase 2 이상은 진행하지 않는다.

되돌릴 때도 아래 원칙은 유지한다.

- planner immutability를 느슨하게 만들지 않는다.
- SEARCH에 hidden must를 넣지 않는다.
- LOOKUP/JOIN hard gating을 완화하지 않는다.
- people/org default lookup 성격을 흔들지 않는다.
- hidden fallback chat 경로를 만들지 않는다.
