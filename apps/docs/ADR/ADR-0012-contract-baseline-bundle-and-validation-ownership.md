# ADR-0012: Contract Baseline Bundle and Validation Ownership

## Status
Proposed

## Date
2026-04-15

## Context

현재 저장소는 runtime semantics 자체보다도 "현재 기준선이 저장소에 보존되는 방식"에서 더 큰 구조 리스크를 안고 있다.

이 pass에서 확인한 사실:

- `apps/docs/README.md`는 새 `01-05` 문서 세트와 `reference/`를 현행 source of truth로 선언한다.
- `apps/docs/reference/PRODUCT_BASELINE.md`는 active gate를 `py_compile + create_app() + targeted pytest subset`이라고 설명한다.
- `apps/conversation/request_facade.py`는 `turn_trigger`, `turn_interpreter`, `context_router` prompt asset과 follow-up 회귀를 현재 런타임의 일부로 사실상 사용한다.
- `apps/api/contracts/repo_manifest.py`는 현재 `planner_prompt_defaults`만 노출하고, 새 docs/prompt/tests/validation lane ownership은 담지 않는다.
- 현재 worktree의 `git status`는 tracked legacy docs 삭제와 함께, 새 `apps/docs/01-05`, `apps/docs/reference/*`, watcher report, `apps/prompts/context_router_v1.md`, `apps/prompts/turn_trigger_v1.md`, `apps/prompts/turn_interpreter_v1.md`, 다수의 `tests/` 파일이 아직 untracked 상태임을 보여준다.
- `pytest.ini`는 tracked deletion 상태라서, 현행 validation posture가 repo-owned executable entrypoint로 고정돼 있지 않다.

즉, 현재 런타임/문서/회귀 기준선은 로컬 dirty worktree에서는 존재하지만 clean checkout이나 부분 커밋에서는 보존되지 않을 수 있다.

이 ADR은 retrieval semantics나 planner contract를 바꾸지 않는다. 아래 불변 조건은 유지한다.

- planner strategy는 요청마다 하나이며 immutable이다.
- `SEARCH`는 hidden hard must를 추가하지 않는다.
- `LOOKUP`과 `JOIN`은 hard gating을 유지한다.
- people/org default lookup 성격을 흔들지 않는다.
- hidden fallback chat escape hatch를 만들지 않는다.
- `LOOKUP`/`JOIN`에 BM25-only shortcut을 열지 않는다.

## current_state_summary

- `L1 의도 계약 + L2 행동안전` 구조는 문서와 코드 양쪽에서 상당 부분 안정화됐다.
- answer-stage, follow-up, visible manifest, prompt projection 경계도 최근 ADR과 watcher에서 비교적 명시적으로 다뤄지고 있다.
- 그러나 이 기준선을 구성하는 핵심 artifact가 하나의 repo-owned bundle로 묶여 있지 않다.
- 사람 기준 source of truth, runtime prompt dependency, executable regression lane, bootstrap note가 서로 다른 위치에 흩어져 있고 일부는 아직 untracked다.
- 결과적으로 "현재 구조가 무엇인가"와 "clean checkout이 그 구조를 재현하는가"가 같은 질문이 아니다.

## pain_points

### 1. source-of-truth docs가 clean checkout에서 사라질 수 있다

README가 가리키는 새 문서 세트와 reference 자료가 아직 tracked ownership으로 굳지 않으면, 저장소는 스스로의 현재 architecture baseline을 보존하지 못한다.

### 2. runtime-critical prompt assets와 focused regressions가 하나의 contract surface로 묶여 있지 않다

`context_router_v1.md`, `turn_trigger_v1.md`, `turn_interpreter_v1.md`는 follow-up behavior의 실제 runtime dependency다. 하지만 이 asset들과 이를 잠그는 tests가 같은 ownership bundle로 관리되지 않아 drift가 review 전에 드러나지 않을 수 있다.

### 3. validation posture가 prose-only다

`PRODUCT_BASELINE.md`와 watcher 보고서는 active gate를 설명하지만, repo 자체가 "무엇이 current validation lane인지"를 machine-readable 또는 reviewable artifact로 충분히 노출하지 않는다.

### 4. refactor state가 reviewable artifact로 고정되지 않는다

현재 tree는 docs, prompts, tests, runtime code가 함께 움직이는 대형 refactor 상태다. 그런데 baseline ownership이 느슨해 reviewer와 automation이 "실험 흔적"과 "현행 계약"을 구분하기 어렵다.

### 5. architecture work가 runtime semantics보다 저장소 ownership에서 먼저 막힌다

앞으로 projection contract, manifest split, execution ownership 같은 설계를 더 진행해도, 현재 기준선을 먼저 repo-owned bundle로 만들지 않으면 이후 ADR과 구현이 모두 로컬 맥락 의존적으로 남는다.

## proposed_target_state

### 1. `ContractBaselineBundle`을 현재 구조의 상위 artifact로 둔다

이 bundle은 runtime semantics를 새로 정의하지 않는다. 이미 존재하는 current contract surface를 "저장소가 보존해야 하는 최소 묶음"으로 선언한다.

구성:

- `docs_bundle`
  - `apps/docs/README.md`
  - 현행 `01-05` 코어 문서
  - 현행 ADR
  - 필요한 reference/handoff/golden 문서
- `prompt_bundle`
  - active planner/follow-up/system prompt assets
  - 각 asset의 owner와 path contract
- `regression_bundle`
  - current gate에 포함되는 targeted pytest subset
  - prompt path regression
  - contract/golden regression
- `validation_bundle`
  - smoke lane
  - optional lane
  - required vs optional dependency note
  - 실패가 의미하는 바
- `ownership_metadata`
  - current / legacy / reference / generated 구분
  - human-facing source of truth와 runtime dependency를 분리 표기

규칙:

- README나 baseline 문서가 current source of truth라고 부르는 파일은 tracked이거나 명시적 generated artifact여야 한다.
- runtime dependency prompt asset은 owner와 regression 없이 local-only 상태로 남지 않는다.
- docs/prompt/tests/validation lane은 같은 contract surface로 다뤄진다.

### 2. `repo_manifest` 또는 동등한 static manifest가 baseline bundle을 노출한다

현재 `repo_manifest.py`는 `planner_prompt_defaults`만 제공한다. 목표 상태에서는 다음까지 포함해야 한다.

- active prompt asset inventory
- current validation lane inventory
- current docs bundle inventory
- optional dependency capability note

중요한 점은 manifest가 runtime policy를 결정하는 것이 아니라, 이미 합의된 current baseline을 노출하는 read-only registry라는 점이다.

### 3. clean checkout parity를 architecture contract로 승격한다

현행 runtime이 맞는지뿐 아니라, clean checkout이 같은 docs/prompt/tests/validation surface를 재현하는지도 architecture quality의 일부로 본다.

규칙:

- untracked local docs/prompt/tests에만 의존하는 current-state 설명을 금지한다.
- watcher/architect가 보고하는 현행 기준선은 repo가 재현 가능해야 한다.
- legacy archive와 current source of truth는 모델/문서 수준에서 구분된다.

### 4. validation ownership을 smoke와 capability profile로 분리한다

현재 환경에서는 `fastapi`, `langgraph` 같은 dependency availability가 불투명하다. 따라서 validation bundle은 한 줄 gate만 적는 대신 아래를 분리해 표현해야 한다.

- always-expected smoke
- dependency-complete lane
- optional observability lane
- unavailable dependency일 때의 해석

이렇게 해야 bootstrap 실패가 실제 regression인지, 미설치 capability인지 구분 가능하다.

## staged_migration_plan

### Phase 0. Inventory and Tagging

- 현재 README, PRODUCT_BASELINE, watcher report를 기준으로 active docs/prompt/tests/validation surface를 inventory한다.
- 각 항목에 `current`, `legacy`, `reference`, `generated`, `runtime_dependency`, `regression_owner` 태그를 부여한다.
- 새 docs tree와 legacy reference tree의 대응 관계를 "현재 기준선" 관점에서 다시 적는다.

Exit criteria:

- 현재 baseline을 구성하는 파일 묶음이 문서로 명시된다.
- clean checkout에서 빠지면 안 되는 asset이 무엇인지 합의된다.

### Phase 1. Ownership Patch Without Runtime Change

- 새 `apps/docs/01-05`
- `apps/docs/reference/*`
- 현행 ADR/watcher report
- active prompt assets
- 현재 유지 중인 targeted tests
- `pytest.ini` 또는 의도된 대체 validation entrypoint

위 묶음을 하나의 non-production patch로 tracked ownership 아래 둔다.

규칙:

- runtime behavior는 바꾸지 않는다.
- docs/prompt/tests ownership만 정리한다.
- legacy archive 삭제와 current bundle 추적을 같은 reviewable change set으로 묶는다.

Exit criteria:

- clean checkout이 현재 문서/프롬프트/회귀 기준선을 잃지 않는다.
- README가 가리키는 current source of truth가 모두 repo에 존재한다.

### Phase 2. Manifest Publication

- `repo_manifest.py`를 확장하거나 별도 static manifest를 추가해 bundle inventory를 노출한다.
- baseline consumer가 README만 파싱하지 않고 한 곳에서 current bundle을 확인할 수 있게 한다.
- automation/watcher가 이 inventory를 기반으로 drift를 판단하게 한다.

Exit criteria:

- current docs/prompt/tests/validation inventory가 machine-readable하다.
- planner prompt default 외의 active contract surface도 registry에 반영된다.

### Phase 3. Validation Capability Profiles

- smoke lane와 dependency-complete lane를 분리 문서화한다.
- `create_app()` failure, `langgraph` absence, prompt asset missing, pytest collection miss를 서로 다른 capability outcome으로 분류한다.
- baseline 문서와 actual validation entrypoint를 동기화한다.

Exit criteria:

- bootstrap 실패 해석이 일관된다.
- "설치 누락"과 "계약 회귀"가 다른 failure class로 남는다.

### Phase 4. Drift Guards

- prompt asset path coverage
- bundle completeness check
- docs/reference current-vs-legacy mismatch check
- validation lane presence check

같은 lightweight guard를 추가한다.

Exit criteria:

- current contract surface 누락이 runtime patch review 이전에 드러난다.
- future refactor가 local-only baseline으로 굴러가지 않는다.

## first_safe_step

가장 안전한 첫 단계는 runtime을 건드리지 않는 ownership 정리다.

구체적으로:

1. 이 ADR로 current problem statement와 target artifact를 고정한다.
2. follow-up patch에서 새 docs/reference/ADR/watcher, active prompt assets, 현재 유지 중인 targeted tests, validation entrypoint를 tracked ownership으로 옮긴다.
3. 그 다음에야 manifest 확장이나 validation capability profile을 설계한다.

이 단계가 안전한 이유:

- planner semantics를 바꾸지 않는다.
- SEARCH/LOOKUP/JOIN gate를 건드리지 않는다.
- hidden fallback chat이나 mode drift를 만들지 않는다.
- clean checkout parity를 먼저 확보해야 이후 architecture ADR이 evidence-based가 된다.

하지 말아야 할 것:

- runtime refactor와 ownership patch를 같은 변경에서 섞지 않는다.
- untracked local tree를 사실상의 source of truth로 계속 전제하지 않는다.
- validation lane을 정리하기 전에 broad CI/full pytest 복구를 먼저 약속하지 않는다.

## files_to_create_or_update

### This ADR run

- `apps/docs/ADR/ADR-0012-contract-baseline-bundle-and-validation-ownership.md`

### Recommended first implementation wave

- track current `apps/docs/01-05` set
- track current `apps/docs/reference/*`
- track current watcher reports under `apps/docs/reports/watcher/`
- track `apps/prompts/context_router_v1.md`
- track `apps/prompts/turn_trigger_v1.md`
- track `apps/prompts/turn_interpreter_v1.md`
- track the existing targeted `tests/` surface that now defines the follow-up/execution baseline
- restore `pytest.ini` or replace it with the intended validation entrypoint
- update `apps/docs/reference/PRODUCT_BASELINE.md`
- update `apps/docs/reference/CODEX_CONTEXT.md`
- update `apps/docs/reference/README_NEXT_STEPS.md`
- extend `apps/api/contracts/repo_manifest.py` or add a static baseline manifest file

## risks_and_unknowns

- 현재 dirty worktree가 진행 중인 refactor를 포함하므로, inventory를 성급히 고정하면 임시 실험 파일까지 current bundle에 잘못 편입될 수 있다.
- dependency manifest가 repo 밖에서 관리되는 것인지 실제 누락인지 아직 확정할 수 없다. 따라서 bootstrap profile은 package manager 결정을 강제하기보다 capability note부터 시작해야 한다.
- bundle completeness guard를 너무 빨리 강제하면 active refactor 중 변경 세트가 커지고 유지보수자가 형식 작업으로 느낄 수 있다.
- watcher report 자체를 current source of truth로 올릴지, reference artifact로만 둘지는 운영 방식에 따라 추가 판단이 필요하다.
- `repo_manifest.py` 확장이 적절한지, 혹은 docs-owned static manifest가 더 나은지는 아직 열린 선택지다.

## Trade-Offs

### Positive

- 저장소가 현재 architecture baseline을 스스로 보존하게 된다.
- docs, prompts, regressions, validation lane이 하나의 review surface로 묶인다.
- clean checkout parity를 architecture 품질 기준으로 다룰 수 있다.
- 이후 projection contract, manifest split, execution ownership 설계가 local-only 맥락에 덜 의존하게 된다.

### Costs

- 단기적으로는 추적/분류해야 할 파일 수가 늘어난다.
- manifest 또는 inventory 유지 비용이 생긴다.
- "runtime change 없는 non-production patch"를 별도 change set으로 운영해야 하므로 속도가 약간 느려질 수 있다.

## Prerequisites

- current docs tree와 legacy/reference tree의 경계 확정
- active prompt asset 목록 확정
- targeted pytest subset의 최소 owner 세트 확정
- validation entrypoint를 prose-only에서 reviewable artifact로 옮기려는 팀 합의

## Rollback

bundle manifest나 ownership registry가 과도한 ceremony만 늘리고 drift 방지 효과를 못 주면, 이 ADR은 documentation-only guidance로 남기고 manifest 구현은 보류한다.

되돌릴 때도 아래 원칙은 유지한다.

- planner immutability를 느슨하게 만들지 않는다.
- SEARCH에 hidden must를 넣지 않는다.
- LOOKUP/JOIN hard gating을 완화하지 않는다.
- people/org default lookup 성격을 흔들지 않는다.
- hidden fallback chat 경로를 만들지 않는다.
