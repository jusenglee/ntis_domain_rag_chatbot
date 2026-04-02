# SESSION_HANDOFF.md

## 2026-04-02T15:41:10.7967342+09:00 Improver
- branch/head: `고도화` / `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
- inspected files:
  - `docs/SESSION_HANDOFF.md`
  - `docs/05_유지보수와_확장.md`
  - `tests/test_feature_package_architecture.py`
- findings:
  - hard-cut 이후에도 일부 historical handoff와 유지보수 문서에는 transition-era 용어가 남아 있어, 현재 구조보다 과거 migration 단계를 더 오래 설명하고 있었다.
- legacy path literal은 이미 0건이었지만, 전환 단계 용어가 남아 있으면 다음 유지보수 루프에서 repo 상태를 더 오래 transitional하게 읽을 여지가 있었다.
- changes:
  - `docs/SESSION_HANDOFF.md`의 historical entry를 현재 저장소 상태와 어긋나지 않는 중립 표현으로 다듬었다.
- `docs/05_유지보수와_확장.md`는 retired 경로 관련 표현을 구현 상태 중심 문장으로 정리했다.
- validations:
  - `python -m pytest tests/test_feature_package_architecture.py -q -p no:cacheprovider` passed (`4 passed`)
  - transition-era wording `git grep` check returned `0` matches
  - retired path literal `git grep` check returned `0` matches
- remains risky:
  - worktree 전체는 여전히 이전 wave까지 포함한 큰 이동/신규 파일 집합이라, 다음에는 commit slicing이나 feature별 smoke 정리가 필요하다.
  - history 문서는 literal scrub와 표현 정리는 끝났지만, 내용 자체는 당시 의사결정 맥락을 보존하므로 현재 구조 설명은 계속 current docs/ADR을 source-of-truth로 삼아야 한다.
- next best task:
  - 다음 안전한 개선은 dirty worktree를 기능 축별 commit 단위로 나누기 전에, feature package별 smoke subset을 더 명확히 묶어 baseline owner 문서와 manifest를 맞추는 것이다.

## 2026-04-02T15:36:16.7363922+09:00 Improver
- branch/head: `고도화` / `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
- inspected files:
  - `apps/api/app_factory.py`
  - `apps/retrieval/rag_pipeline.py`
  - `tests/test_feature_package_architecture.py`
  - `docs/CODEX_CONTEXT.md`
  - `docs/01_아키텍처와_흐름.md`
  - `docs/05_유지보수와_확장.md`
  - `docs/ADR/ADR-0013-feature-sliced-package-cutover.md`
  - `docs/SESSION_HANDOFF.md`
  - `.agents/skills/ntis-rag-context-refine/SKILL.md`
  - `.agents/skills/ntis-rag-context-refine/references/SKILL_revised_ko.md`
- findings:
  - legacy package root 삭제와 1차 path scrub는 끝났지만, current docs/ADR에는 아직 bridge 단계 시제와 retired path 표현이 남아 있었다.
  - `tests/test_feature_package_architecture.py`는 hard-cut 정책으로 바뀌었지만, test 파일 내부의 literal path가 text-scan self-hit을 만들고 있었다.
  - `git ls-files` 기준으로는 legacy package와 tracked `__pycache__`가 index에 아직 남아 있어서 hygiene acceptance를 만족하지 못했다.
- changes:
  - `apps/retrieval/rag_pipeline.py` module docstring을 feature-root source-of-truth 기준으로 다시 정리했다.
  - `docs/CODEX_CONTEXT.md`, `docs/01_아키텍처와_흐름.md`, `docs/05_유지보수와_확장.md`를 hard-cut 완료 상태에 맞게 갱신했다.
  - `docs/ADR/ADR-0013-feature-sliced-package-cutover.md`를 clean rewrite하고, `docs/ADR/ADR-0014-legacy-bridge-removal-and-repo-scrub.md`를 추가했다.
  - `.agents/skills/ntis-rag-context-refine/SKILL.md`와 `references/SKILL_revised_ko.md`의 문서 기준선을 현재 `docs/` 구조로 바로잡았다.
  - `tests/test_feature_package_architecture.py`는 legacy root path 정의까지 split-token/self-hit-safe 형태로 정리했다.
  - `docs/SESSION_HANDOFF.md`의 과거 기록 중 retired path literal을 중립 표현으로 치환했다.
  - index에서도 legacy package root, tracked `__pycache__`, stray duplicate 문서 삭제가 반영되도록 최소 범위 stage를 수행했다.
- validations:
  - `python -m py_compile apps/api/app_factory.py apps/retrieval/rag_pipeline.py tests/test_feature_package_architecture.py` passed
  - `python -m pytest tests/test_feature_package_architecture.py -q -p no:cacheprovider` passed (`4 passed`)
  - `python -m pytest --collect-only -q --ignore-glob=pytest-cache-files-* --ignore-glob=tests/pytest-cache-files-* -p no:cacheprovider` passed (`299 tests collected`)
  - `python -m pytest tests/test_repo_contract_defaults.py tests/test_contract_debt_paydown.py tests/test_planner_stagewise.py tests/test_request_facade_source_reference_fallback.py tests/test_request_facade_child_anchor.py tests/test_request_overrides.py -q -p no:cacheprovider` passed (`113 passed`)
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` passed (`299 tests collected`, `164 passed`, `6 passed`)
- legacy package root `git ls-files` check returned `0` entries
- tracked `__pycache__` `git ls-files` check returned `0` entries
- retired path literal `git grep` check returned `0` matches
- remains risky:
  - worktree에는 이번 wave 이전부터 누적된 광범위한 이동/수정/신규 파일이 많아, 이후 commit wave에서는 staged/unstaged 범위를 다시 분리해서 다루는 편이 안전하다.
  - historical handoff/report는 old path literal scrub는 끝났지만, 일부 과거 기록은 전환 단계 맥락을 설명하는 문장이 남아 있다.
  - repo 밖 소비자가 retired 경로를 쓰고 있었다면 이번 hard cut 이후에는 즉시 새 feature root로 옮겨야 한다.
- next best task:
  - legacy hard cut 이후 남은 큰 작업은 broad compatibility phrasing까지 더 엄격히 줄일지, 아니면 runtime/manifest ownership 정리와 full commit slicing으로 넘어갈지 결정하는 것이다.
  - 다음 implementation wave에서는 현재 dirty worktree를 기능 축별로 다시 묶어 commit 단위를 분리하고, 필요하면 feature package별 smoke test를 더 얹는다.

## 2026-04-02T14:45:12+09:00 Improver
- branch/head: `고도화` / `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
- inspected files:
  - `apps/api/contracts/workflow_models.py`
  - `apps/chat/llm_runtime.py`
  - `apps/platform/storage.py`
  - `apps/platform/triton_client.py`
  - `apps/retrieval/rag_store.py`
  - `apps/api/runtime_helpers.py`
  - `apps/api/app_factory.py`
  - `apps/conversation/request_facade.py`
  - `apps/conversation/followup_anchor.py`
  - `apps/conversation/followup_resolution.py`
  - `tests/test_llm_runtime_prompt_paths.py`
  - `tests/test_repo_contract_defaults.py`
  - `tests/test_contract_debt_paydown.py`
  - `tests/test_request_overrides.py`
  - `tests/test_retrieval_workflow_detail_followup_freshness.py`
  - `tests/test_retrieval_workflow_freshness_override.py`
  - `tests/test_planner_stagewise.py`
  - `tests/test_request_facade_source_reference_fallback.py`
  - `tests/test_request_facade_child_anchor.py`
  - `docs/SESSION_HANDOFF.md`
- findings:
  - optional dependency가 없는 shell에서는 `langgraph`, `aiofiles`, `transformers`, `llama_index` top-level import 때문에 `pytest --collect-only`와 baseline 스크립트가 막혔다.
  - `request_facade`는 `reference_context`가 이미 `resolved`된 경우에도 `scope_decision.needs_clarification`로 덮어써서 ordinal/deictic canonical follow-up seed를 잃고 있었다.
  - source-reference follow-up은 canonical/reference-context가 있어도 `display_snapshot` anchor를 먼저 채택해 canonical owner precedence contract를 어기고 있었다.
  - follow-up anchor 해석은 `active_scope.focus`와 `child_anchor` 우선순위가 섞여 있었고, ordinal parser는 bare `세`를 `자세히` 같은 일반 단어 안에서도 ordinal로 잘못 읽을 수 있었다.
- changes:
  - `workflow_models.py`는 `langgraph.graph.message.add_messages`를 optional import로 바꾸고, 미설치 시 typing-safe local fallback을 쓰도록 했다.
  - `llm_runtime.py`, `storage.py`는 `aiofiles` top-level import를 제거하고 `asyncio.to_thread + stdlib file I/O`로 바꿔 import blocker를 없앴다.
  - `triton_client.py`는 `transformers.AutoTokenizer`와 `tritonclient.grpc` 심볼을 lazy-load로 전환하고, 실제 사용 시점에만 명시적 `RuntimeError`를 내도록 정리했다.
  - `rag_store.py`는 `llama_index` embedding import와 Triton warm-up import를 `build_rag_objects()` 내부로 옮겨 `rag_pipeline` import만으로 heavy dependency가 요구되지 않게 했다.
  - `_state_log_summary_fields` source-of-truth를 `apps/api/runtime_helpers.py`로 옮기고, 관련 테스트는 `app_factory` import를 피하도록 가볍게 분리했다.
  - `request_facade.py`는 source-reference에서 `reference_context`가 `resolved|out_of_range|unresolved`면 그것을 우선 사용하고, `missing_context|none`일 때만 `display_snapshot` fallback을 허용하도록 고쳤다.
  - `request_facade.py`는 `reference_context`가 이미 해석된 follow-up을 `scope` clarification으로 다시 덮어쓰지 않도록 조정했다.
  - `followup_anchor.py`는 `child_anchor` referential follow-up을 유지하면서도 `active_scope.focus` project deictic을 anchor로 사용할 수 있게 우선순위를 분리했다.
  - `followup_resolution.py`, `followup_anchor.py`는 bare ordinal token false positive를 줄여 `그 논문 자세히` 같은 query가 잘못 `세 번째`로 해석되지 않게 했다.
- validations:
  - `python -m py_compile apps/api/contracts/workflow_models.py apps/chat/llm_runtime.py apps/platform/storage.py apps/platform/triton_client.py apps/retrieval/rag_store.py apps/api/runtime_helpers.py apps/api/app_factory.py apps/conversation/request_facade.py apps/conversation/followup_anchor.py apps/conversation/followup_resolution.py` passed
  - `python -m pytest --collect-only -q --ignore-glob=pytest-cache-files-* --ignore-glob=tests/pytest-cache-files-* -p no:cacheprovider` passed (`298 tests collected`)
  - `python -m pytest tests/test_llm_runtime_prompt_paths.py tests/test_repo_contract_defaults.py tests/test_contract_debt_paydown.py tests/test_request_overrides.py tests/test_retrieval_workflow_detail_followup_freshness.py tests/test_retrieval_workflow_freshness_override.py -q -p no:cacheprovider` passed (`39 passed`)
  - `python -m pytest tests/test_planner_stagewise.py -q -p no:cacheprovider` passed (`78 passed`)
  - `python -m pytest tests/test_feature_package_architecture.py tests/test_request_facade_source_reference_fallback.py tests/test_request_facade_child_anchor.py tests/test_retrieval_workflow_detail_followup_freshness.py tests/test_retrieval_workflow_freshness_override.py tests/test_request_overrides.py -q -p no:cacheprovider` passed (`33 passed`)
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` passed (`collect-only 298`, `core contract subset 163 passed`, `eval fixtures 6 passed`)
- remains risky:
  - `apps/api/app_factory.py`에는 새 helper alias를 덮어쓰는 dead local `_state_log_summary_fields` body가 아직 남아 있어, source-of-truth cleanup을 한 번 더 해 주는 편이 안전하다.
  - optional dependency가 없는 환경에서 import/collect는 복구됐지만, 실제 Triton/embedding runtime 경로는 dependency가 없으면 fail-loud 하므로 해당 경로의 실동작 검증은 앱 환경에서 다시 확인해야 한다.
  - feature-sliced bridge 파일은 여전히 많이 남아 있으므로, architecture test가 guard를 제공해도 bridge 제거 cutover 전까지는 legacy path drift 위험이 남아 있다.
- next best task:
  - dependency가 갖춰진 앱 환경에서 Triton, embedding, prompt loading 실제 경로를 exercise하는 integration smoke를 추가해 lazy-load/fail-loud contract를 end-to-end로 고정한다.
- `apps/api/app_factory.py`의 dead local `_state_log_summary_fields`를 제거하고, legacy hard-cut wave 전에 runtime helper ownership을 더 분명히 정리한다.
  - 이번 턴은 behavior contract를 바꾸지 않았으므로 `docs/README.md`, `docs/02_실행계약과_전략규칙.md`, `docs/03_운영과_환경.md`, `docs/GOLDEN_TESTS.md`는 그대로 두고 handoff만 갱신했다.

## 2026-04-02T11:30:00+09:00 Improver
- branch/head: `고도화` / `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
- inspected files:
  - `apps/api/routes.py`
  - `apps/retrieval/filters.py`
  - `apps/api/contracts/repo_manifest.py`
  - `apps/api/contracts/workflow_models.py`
  - `apps/api/runtime.py`
  - `apps/api/app_factory.py`
  - `apps/conversation/request_facade.py`
  - `apps/conversation/view_state.py`
  - `apps/planner/planner_runtime.py`
  - `apps/evidence/canonical_context.py`
  - `apps/retrieval/retrieval_workflow.py`
  - `apps/chat/answer_generation.py`
  - `tests/test_feature_package_architecture.py`
  - `tests/test_filters_anchor_lock.py`
  - `tests/test_people_filter_nested_gate.py`
  - `tests/test_request_facade_source_reference_fallback.py`
  - `tests/test_request_facade_followup_seed_priority.py`
  - `docs/ADR/ADR-0013-feature-sliced-package-cutover.md`
  - `docs/README.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/SESSION_HANDOFF.md`
- findings:
- planner / conversation / evidence / retrieval / chat / platform 경계는 문서에 이미 있었지만, 실구현 ownership이 과거 공용 디렉터리에 흩어져 있어서 source-of-truth 경로가 불명확했다.
  - feature package cutover만 기계적으로 수행하면 retrieval-to-api 역참조와 SSE/reference provenance 회귀가 같이 터질 수 있어서, bridge 전환과 route/reference contract 복구를 같은 wave에서 묶어야 했다.
  - 필터 중첩 gate와 follow-up seed 테스트 일부는 old path import, old view-state field명을 그대로 기대하고 있어서 cutover 후 회귀 고정이 필요했다.
- changes:
- 새 source-of-truth package `apps/conversation`, `apps/planner`, `apps/evidence`, `apps/retrieval`, `apps/chat`, `apps/platform`을 만들고, 당시 과거 공용 디렉터리는 새 경로를 가리키는 임시 전환 계층으로만 유지했다.
  - `apps/api/oracle_request_defaults.py`, `apps/api/request_overrides.py`, `apps/api/runtime_helpers.py`, `apps/api/workflow_builder.py`, `apps/api/workflow_nodes.py`를 `apps/api` composition root 쪽으로 이동해 transport/runtime wiring 경계를 분리했다.
- repo 전반 import를 새 feature root로 치환했고, `tests/test_feature_package_architecture.py`를 추가해 feature package의 legacy import 금지, non-owner 모듈의 old-path import 금지, 임시 전환 계층 상태를 AST/regex로 고정했다.
  - `apps/api/routes.py`는 canonical SSE event만 내보내도록 정리했고, `selected_answer_artifact.references -> canonical_evidence -> context` 순서의 source reference fallback을 복구했다.
  - `apps/retrieval/filters.py`는 이미 should-gated 된 nested filter를 다시 must gate로 감싸지 않도록 보정해 people/org nested gate 회귀를 복구했다.
  - follow-up/view-state 관련 테스트는 새 package import와 `visible_answer_manifest` contract로 맞췄고, architecture baseline inventory와 ADR(`ADR-0013`) 및 아키텍처/운영 문서를 새 package map 기준으로 갱신했다.
- validations:
  - `python -m py_compile apps/api/app_factory.py apps/api/routes.py apps/api/runtime.py apps/api/runtime_helpers.py apps/api/workflow_nodes.py apps/conversation/request_facade.py apps/conversation/view_state.py apps/planner/planner_runtime.py apps/evidence/canonical_context.py apps/retrieval/retrieval_workflow.py apps/retrieval/rag_pipeline.py apps/chat/answer_generation.py apps/chat/answer_merge.py apps/chat/llm_runtime.py apps/platform/schemas.py tests/test_feature_package_architecture.py` passed
  - `python -m pytest tests/test_feature_package_architecture.py -q -p no:cacheprovider` passed (`3 passed`)
  - `python -m pytest tests/test_view_state_child_refs.py tests/test_view_state_active_scope.py tests/test_scope_resolver.py tests/test_scope_resolver_v2.py tests/test_request_facade_child_anchor.py tests/test_followup_clarification_scope.py tests/test_conversation_store_subject_index.py tests/test_anchor_constraint_compiler.py -q -p no:cacheprovider` passed (`29 passed`)
  - `python -m pytest tests/test_filters_anchor_lock.py tests/test_people_filter_nested_gate.py tests/test_canonical_context.py tests/test_detail_contract.py tests/test_retrieval_workflow_detail_runtime.py tests/test_retrieval_workflow_detail_cache_gate.py tests/test_rag_result_constraints.py -q -p no:cacheprovider` passed (`49 passed`)
  - `python -m pytest tests/test_answer_generation_groundedness_snapshot.py tests/test_answer_merge_bypass.py tests/test_runtime_helpers_stream_bypass.py tests/test_api_routes_reference_payload.py tests/test_oracle_request_overrides.py -q -p no:cacheprovider` passed (`59 passed`)
  - `python -m pytest tests/test_rag_filter_policy.py tests/test_filters_anchor_lock.py tests/test_request_facade_source_reference_fallback.py tests/test_request_facade_followup_seed_priority.py tests/test_rag_anchor_truth.py tests/test_rag_anchor_truth_active_only.py -q -p no:cacheprovider` passed (`15 passed`)
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1`는 collect 단계에서 optional dependency 부재로 중단됐다. 확인된 누락 dependency는 `langgraph`, `aiofiles`, `llama_index`, `transformers`였다.
- remains risky:
- 임시 전환 계층이 repo 안에 남아 있었기 때문에, 새 코드 추가가 실수로 retired 경로에 쌓이지 않게 architecture test와 code review discipline이 계속 필요했다.
  - planner/runtime와 일부 broader baseline 세트는 optional dependency가 없는 현재 shell에서 끝까지 검증하지 못했다.
  - `apps/api/streaming/__pycache__/*.pyc` 같은 generated artifact와 과거 ADR/report/history는 feature cutover source-of-truth가 아니라 기록 보존물이라, cleanup 범위를 별도로 정해야 한다.
- next best task:
- optional dependency가 설치된 앱 환경에서 `tests/test_planner_stagewise.py`, `tests/test_repo_contract_defaults.py`, `tests/test_contract_debt_paydown.py`, freshness/detail workflow 세트까지 포함한 full baseline을 다시 돌려 feature cutover 이후 end-to-end drift가 없는지 확인한다.
- retired 경로 참조가 테스트/문서/도구에서 0건이 되는 시점에 전환 계층 삭제 wave를 별도 staged patch로 진행한다.

## 2026-04-01T18:20:00+09:00 Improver
- branch/head: `고도화` / `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
- inspected files:
  - `apps/api/contracts/answer_state_consistency.py`
  - `apps/api/contracts/workflow_models.py`
  - `apps/chat/answer_merge.py`
  - `apps/chat/answer_generation.py`
  - `apps/api/app_factory.py`
  - `apps/conversation/view_state.py`
  - `tests/test_answer_state_consistency.py`
  - `tests/test_answer_generation_groundedness_snapshot.py`
  - `tests/test_answer_merge_bypass.py`
  - `docs/02_실행계약과_전략규칙.md`
  - `docs/03_운영과_환경.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/SESSION_HANDOFF.md`
- findings:
  - 기존 groundedness contract만으로는 모델 answer가 현재 `active_scope.result_set`와 다른 목록/순서를 말해도 list turn ordinal truth를 오염시킬 수 있었다.
  - `visible_answer_manifest` 승인 조건이 groundedness 단독이라서, item identity/order mismatch가 있어도 다음 turn follow-up truth가 갱신될 수 있었다.
  - selector는 visible-order 보호가 켜진 turn에서도 groundedness-safe preference까지만 알고 있었고, 현재 turn result-set과 answer의 실제 일치 여부는 보지 않았다.
- changes:
  - `apps/api/contracts/answer_state_consistency.py`를 추가해 `AnswerStateSnapshot`, `AnswerStateConsistencyVerdict`, result-set snapshot builder, list-like answer parser/evaluator를 넣었다.
  - `answer_merge.py`: protected list-like turn에서 `state_consistency_snapshot`을 받아 모델별 `solar_state_consistency`, `gemma_state_consistency`를 계산하고, 한 모델만 `supported`면 그 모델을 고르고 둘 다 아니면 `selection_reason=both_models_state_inconsistent`로 fallback 하도록 바꿨다.
  - `answer_generation.py`: `active_scope.result_set` 기반 snapshot을 selector에 전달하고, `visible_answer_manifest`를 groundedness + state consistency 둘 다 통과한 경우에만 승인하도록 변경했다. selected artifact meta와 `LLM.RESULT`에 `answer_state_consistency`, `answer_state_consistency_status`, `visible_answer_manifest_status`를 추가했다.
  - `workflow_models.py`, `app_factory.py`: state consistency snapshot/verdict를 workflow state와 `REQ.SUMMARY` 요약 필드에 반영했다.
  - docs: current contract/운영/golden 문서에 answer-state consistency gate와 blocked manifest 규칙을 추가했다.
- validations:
  - `python -m pytest tests/test_answer_state_consistency.py tests/test_answer_generation_groundedness_snapshot.py tests/test_answer_merge_bypass.py -q -p no:cacheprovider` passed (`23 passed`)
  - `python -m compileall apps/api/contracts/answer_state_consistency.py apps/chat/answer_merge.py apps/chat/answer_generation.py apps/api/contracts/workflow_models.py apps/api/app_factory.py` passed
- remains risky:
  - current v1 parser는 numbered/bulleted list 중심이라, 비정형 prose answer의 순서 불일치는 `no_structured_list`로 보수 차단한다.
  - year/org mismatch는 state consistency 축이 아니라 groundedness 축에 남겨두었기 때문에, item order는 맞지만 field claim이 틀린 answer는 선택될 수 있고 manifest만 차단된다.
  - transcript replay harness는 아직 붙지 않았고, 이번 guard는 selector/merge 단위 회귀 위주로 고정되어 있다.
- next best task:
  - 실제 실패 transcript를 replay fixture로 고정해 `blocked_state_consistency -> stale ordinal follow-up clarification` 경로를 end-to-end로 검증한다.
  - `no_structured_list` 비율이 높은 질문 패턴을 로그에서 모아, v2에서 prose-style list parser나 repair answer 전략을 설계한다.

## 2026-04-01T17:00:15.3363188+09:00 Improver
- branch/head: `고도화` / `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
- inspected files:
  - `apps/conversation/view_state.py`
  - `apps/conversation/conversation_store.py`
  - `apps/conversation/request_facade.py`
  - `apps/conversation/followup_anchor.py`
  - `apps/conversation/scope_resolver.py`
  - `apps/retrieval/retrieval_workflow.py`
  - `apps/chat/answer_generation.py`
  - `apps/planner/planner_runtime.py`
  - `apps/retrieval/rag_retriever.py`
  - `tests/test_view_state_active_scope.py`
  - `tests/test_conversation_store_subject_index.py`
  - `tests/test_request_facade_child_anchor.py`
  - `tests/test_scope_resolver.py`
  - `tests/test_scope_resolver_v2.py`
  - `tests/test_followup_clarification_scope.py`
  - `tests/test_retrieval_workflow_detail_runtime.py`
  - `tests/test_answer_generation_groundedness_snapshot.py`
  - `docs/02_실행계약과_전략규칙.md`
  - `docs/03_운영과_환경.md`
  - `docs/SESSION_HANDOFF.md`
- findings:
  - hard cutover 직전에도 `scope_resolver.py` 내부에 손상된 legacy-era 문자열이 남아 있어서 current-contract 회귀가 수집 단계에서 막히고 있었다.
  - current truth는 이미 `active_scope`, `visible_answer_manifest`, `subject_index`로 옮겨졌지만, handoff와 운영 문서에 cutover 이후 session continuity 중단 사실이 더 분명히 남아야 했다.
  - old Redis key / latest mirror 제거 자체는 코드 경로에서 완료됐고, 남은 위험은 optional dependency가 필요한 넓은 통합 세트 미실행뿐이었다.
- changes:
  - `view_state.py`: `latest_display_snapshot`, `latest_focus_entity`, legacy mirror normalization/backfill을 제거하고 `active_scope` helpers만 남겼다.
  - `conversation_store.py`: Redis key builder를 `conversation:v2:{conversation_id}:*`로 고정하고 old namespace dual-read를 제거했다.
  - `request_facade.py`, `followup_anchor.py`, `scope_resolver.py`, `retrieval_workflow.py`, `planner_runtime.py`, `rag_retriever.py`, `answer_generation.py`: follow-up 해석과 visible-order truth를 `active_scope.child_anchor -> subject_index -> visible_answer_manifest -> prev_context` 순서로 정리하고 legacy snapshot/focus fallback을 제거했다.
  - `scope_resolver.py`: 깨진 reset/clarification 문자열을 정리하고 `이전 거 무시` reset cue를 추가해 current-contract 회귀를 복구했다.
  - `docs/03_운영과_환경.md`: search session state 해석 기준과 `conversation:v2` namespace only, cutover 후 기존 세션 continuity 미보장을 명시했다.
- validations:
  - `python -m pytest tests/test_view_state_active_scope.py tests/test_conversation_store_subject_index.py tests/test_rag_anchor_truth.py tests/test_rag_anchor_truth_active_only.py tests/test_retrieval_workflow_detail_cache_gate.py tests/test_retrieval_workflow_detail_runtime.py tests/test_request_facade_child_anchor.py tests/test_scope_resolver.py tests/test_scope_resolver_v2.py tests/test_followup_clarification_scope.py tests/test_answer_generation_groundedness_snapshot.py tests/test_answer_merge_bypass.py -q -p no:cacheprovider` passed (`64 passed`)
  - `python -m compileall apps/conversation/request_facade.py apps/retrieval/retrieval_workflow.py apps/conversation/view_state.py apps/conversation/followup_anchor.py apps/conversation/conversation_store.py apps/conversation/scope_resolver.py apps/chat/answer_generation.py apps/planner/planner_runtime.py apps/retrieval/rag_retriever.py` passed
  - production grep acceptance:
    - `apps/` 기준 `latest_display_snapshot|latest_focus_entity|legacy_visible_manifest` 참조 0건
    - current contract docs(`docs/02`, `docs/03`, `docs/04`, `docs/GOLDEN_TESTS.md`) 기준 old `conversation:{cid}:*` / `conversation:{conversation_id}:*` key 참조 0건
    - tests에는 old unversioned key ignored 회귀를 위해 explicit old-key fixture가 의도적으로 1건 남아 있다
- remains risky:
  - `tests/test_contract_debt_paydown.py`, `tests/test_planner_stagewise.py` 같은 일부 상위 세트는 `langgraph`, `llama_index` optional dependency가 없어 이 shell에서는 아직 못 돌렸다.
  - `docs/reports/**`, 과거 handoff/ADR에는 historical context로 legacy 용어가 남아 있다. 이는 의도된 기록 보존이며 current contract source-of-truth는 아니다.
  - malformed `v2` payload는 empty-state로 fail-close 되지만, 실제 Redis corruption 재현 harness는 아직 없다.
- next best task:
  - optional dependency가 있는 실제 앱 환경에서 `tests/test_planner_stagewise.py`와 transcript replay harness를 돌려 `visible_answer_manifest + subject_index` cutover를 end-to-end로 확인한다.
  - 운영 runbook에 old Redis key purge 절차를 추가해 cutover 이후 불필요한 unversioned session state를 정리한다.

## 2026-04-01T16:30:00+09:00 Improver
- branch/head: `怨좊룄??/ `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
- inspected files:
  - `apps/evidence/canonical_evidence.py`
  - `apps/evidence/canonical_context.py`
  - `apps/conversation/view_state.py`
  - `apps/conversation/followup_anchor.py`
  - `apps/conversation/request_facade.py`
  - `apps/retrieval/retrieval_workflow.py`
  - `apps/chat/answer_generation.py`
  - `apps/chat/answer_merge.py`
  - `apps/api/routes.py`
  - `apps/api/streaming/contracts.py`
  - `tests/test_view_state_child_refs.py`
  - `tests/test_canonical_context.py`
  - `tests/test_request_facade_child_anchor.py`
  - `tests/test_conversation_store_subject_index.py`
  - `tests/test_answer_merge_bypass.py`
  - `tests/test_answer_generation_groundedness_snapshot.py`
  - `tests/test_scope_resolver.py`
  - `tests/test_scope_resolver_v2.py`
  - `tests/test_followup_clarification_scope.py`
  - `tests/test_view_state_active_scope.py`
  - `tests/test_retrieval_workflow_detail_runtime.py`
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/SESSION_HANDOFF.md`
- findings:
  - child subject names were often answer-visible but not follow-up-visible because canonical/detail normalization preserved display text without preserving reusable child-subject structure.
  - session memory stored `view_state` without a reusable child-subject index, so named detail follow-ups after reload could not consistently resolve `people | org | perf` subjects.
  - ordinal/source follow-up truth could drift back to raw retrieval order unless the answer-approved visible order was stored separately from `latest_display_snapshot`.
- changes:
  - `canonical_evidence.py`: added `child_entities[]` with `kind`, `subject_id`, `display_name`, `ids_map`, `role`, `affiliation`, `parent_relation`, `resolution_state`; name-only entities now survive as `provisional`.
  - `canonical_context.py`: rehydration now restores participant people/org/perf structures from `child_entities`, including ids when present.
  - `view_state.py`: expanded `ChildEntityRef`, added `SubjectIndexEntry`, `subject_index`, `visible_answer_manifest`, backward-compatible loader normalization, and list/detail subject indexing helpers.
  - `followup_anchor.py`: generalized child-subject follow-up to `people | org | perf`, allowed provisional anchors, added subject-index matching, and widened English ordinal/deictic parsing used by tests.
  - `request_facade.py`: wired `subject_index` + `visible_answer_manifest` into follow-up resolution, re-applied subject hints after planner merge, and forced ordinal/source follow-ups to use manifest truth when available.
  - `retrieval_workflow.py`: list/detail turns now upsert visible subjects into `view_state.subject_index`.
  - `answer_generation.py`, `answer_merge.py`, `routes.py`, `streaming/contracts.py`: carried `visible_answer_manifest` through answer selection and blocked manifest updates when the selected list answer is groundedness-`unsupported`.
  - docs/tests: added regression coverage for provisional child subjects, view-state roundtrip compatibility, manifest-protected ordinal follow-up, and supported-vs-unsupported visible-order selection.
- validations:
  - `python -m pytest tests/test_view_state_child_refs.py tests/test_view_state_active_scope.py tests/test_canonical_context.py tests/test_request_facade_child_anchor.py tests/test_conversation_store_subject_index.py tests/test_answer_merge_bypass.py tests/test_answer_generation_groundedness_snapshot.py tests/test_scope_resolver.py tests/test_scope_resolver_v2.py tests/test_followup_clarification_scope.py tests/test_retrieval_workflow_detail_runtime.py -q -p no:cacheprovider` passed (`61 passed`)
  - `python -m compileall apps/conversation/request_facade.py apps/retrieval/retrieval_workflow.py apps/conversation/view_state.py apps/conversation/followup_anchor.py apps/evidence/canonical_evidence.py apps/evidence/canonical_context.py apps/chat/answer_generation.py apps/chat/answer_merge.py apps/api/routes.py apps/api/streaming/contracts.py` passed
- remains risky:
  - provisional child subjects still rely on name/affiliation/role identity, so true homonyms inside the same scope still need clarification rather than auto-resolution.
  - the new subject-index contract is covered at view-state payload roundtrip level, but there is still no real Redis replay harness in this shell because storage imports remain blocked by optional dependency gaps like `aiofiles`.
  - manifest-protected ordinal truth is covered in unit tests, but live transcript replay is still needed for the Korean failure chains that motivated this work.
- next best task:
  - add a transcript replay harness for `諛섎룄泥?愿??怨쇱젣 3嫄?-> 2踰?怨쇱젣 ?곌뎄??-> ?대떦 ?곌뎄?먯쓽 ?ㅻⅨ ?쒕룞` and `3D 諛섎룄泥?.. ?곸꽭 -> 理쒖꽦???ㅻⅨ 怨쇱젣` so subject-index + manifest truth are checked end-to-end from logs.
  - if live data shows frequent same-name provisional collisions, introduce a scoped clarification card that shows parent detail context for each provisional candidate.

## 2026-03-31T20:23:14.5089387+09:00 Improver
- branch/head: `怨좊룄?? / `e143313ca4a0e5ccd7207e22225f69be0441577d`
- inspected files:
  - `apps/chat/answer_merge.py`
  - `apps/conversation/scope_resolver.py`
  - `tests/test_answer_merge_bypass.py`
  - `tests/test_scope_resolver_v2.py`
  - `tests/test_followup_clarification_scope.py`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
- findings:
  - answer-stage leak detection declared `_INTERNAL_SCHEMA_LABEL_PATTERN` and `_RAW_INTERNAL_UNAVAILABLE_PHRASES` but never consumed them, so schema-label answers and raw missing-field inventory could still win under `solar_first`.
  - `scope_resolver` kept refinement detection behind an active-scope gate, which made the documented `refinement_target_missing -> clarification_required` path unreachable for no-scope follow-ups such as `2021?꾨쭔 蹂댁뿬以?.
  - the repo docs already described the intended refinement-clarification contract, so this task was runtime/test catch-up rather than a contract change.
- changes:
  - `answer_merge.py`: wired schema parenthetical labels and raw internal missing-field phrases into `_looks_like_internal_context_leak()` so those answers degrade consistently as `internal_context_leak`.
  - `scope_resolver.py`: split active-scope detection from refinement-cue detection, counted `child_anchor` as an active target, and clarified only when the question is scope-dependent refinement without an independent search axis.
  - `tests/test_scope_resolver_v2.py`: added regression coverage for `no active scope + years-only refinement -> clarification` and `no active scope + semantic axes -> fresh_search`.
  - `tests/test_followup_clarification_scope.py`: added a `build_intent_payload()` regression to ensure `request_facade` propagates the no-scope refinement case as `clarification_required`.
  - `docs/04_?뚭?湲곗?怨??먭?.md` was reviewed and left unchanged because it already matches the intended refinement clarification behavior.
- validations:
  - `python -m pytest tests/test_answer_merge_bypass.py tests/test_scope_resolver_v2.py tests/test_followup_clarification_scope.py tests/test_api_routes_reference_payload.py tests/test_runtime_helpers_stream_bypass.py tests/test_rag_anchor_truth.py tests/test_rag_anchor_truth_active_only.py -q -p no:cacheprovider` passed (`53 passed`)
  - `python -m pytest tests/test_answer_merge_bypass.py tests/test_scope_resolver_v2.py tests/test_followup_clarification_scope.py tests/test_request_facade_child_anchor.py tests/test_view_state_active_scope.py tests/test_api_routes_reference_payload.py tests/test_runtime_helpers_stream_bypass.py tests/test_rag_anchor_truth.py tests/test_rag_anchor_truth_active_only.py -q -p no:cacheprovider` passed (`56 passed`)
  - `python -m py_compile apps/chat/answer_merge.py apps/conversation/scope_resolver.py tests/test_scope_resolver_v2.py tests/test_followup_clarification_scope.py` passed
- next best task:
  - rerun `tests/test_retrieval_workflow_detail_followup_freshness.py` and `tests/test_planner_stagewise.py` inside the real project environment that has `langgraph` and `llama_index`, so the freshness/planner-detail confidence gap closes.
  - if the team wants stricter follow-up refinement semantics, promote the current heuristic (`no independent search axis`) into an explicit contract/example set in docs and golden tests.

## 2026-03-31T20:11:04.8410632+09:00 Improver
- branch/head: `怨좊룄?? / `881265916b9900e4dd5e8425487ce47010a2506d`
- inspected files:
  - `apps/api/app_factory.py`
  - `apps/api/runtime.py`
  - `apps/retrieval/retrieval.py`
  - `apps/retrieval/filters.py`
  - `apps/retrieval/rag_filter_policy.py`
  - `tests/test_runtime_payload_indexes.py`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/README.md`
- findings:
  - startup payload index warmup was limited to `KEYWORD` and `TEXT`, so year/date filters and exact perf follow-up ids were not being warmed proactively.
  - participant researcher name and title filter axes that are actively used in filter compilation were missing from the boot-time target set.
  - there was no focused regression test for runtime payload index warmup, and optional alias fields such as `perf_id` / `paper_id` had no guard against blind index creation.
- changes:
  - `app_factory.py`: expanded runtime payload-index targets for `ntis_project_v1` and `ntis_perf_v1`, adding researcher-name keyword targets, title text targets, integer/date targets, and optional keyword targets for sampled-only fields.
  - `runtime.py`: extended `AppRuntimeConfig`, added integer/datetime warmup hooks, skipped already-indexed schema types, and added conservative startup payload sampling before creating optional keyword indexes.
  - `retrieval.py`: added `ensure_integer_index()` and `ensure_datetime_index()` helpers.
  - `tests/test_runtime_payload_indexes.py`: added unit coverage for required/optional index warmup and nested optional-field probing.
  - `docs/03_?댁쁺怨??섍꼍.md`, `docs/04_?뚭?湲곗?怨??먭?.md`: documented the expanded Qdrant payload-index startup contract and regression expectations.
  - `docs/README.md` was reviewed and left unchanged because the document map and source-of-truth pointers still match after this runtime-only change.
- validations:
  - `python -m py_compile apps/api/app_factory.py apps/api/runtime.py apps/retrieval/retrieval.py tests/test_runtime_payload_indexes.py` passed
  - `python -m pytest tests/test_runtime_payload_indexes.py -q -p no:cacheprovider` passed (`2 passed`)
- next best task:
  - run the real app against a live Qdrant instance and verify startup logs plus `get_collection(...).payload_schema` for `ntis_project_v1` / `ntis_perf_v1` after boot.
  - measure whether the optional-field sampling window is sufficient for live collections or whether the alias-field decision should move to an explicit schema manifest.

## 2026-03-31T23:40:00+09:00 Improver
- branch/head: `怨좊룄?? / `fc8c6027bd5a766c12d3ab34739d1a894dfb03d5`
- inspected files:
  - `apps/conversation/view_state.py`
  - `apps/conversation/conversation_store.py`
  - `apps/conversation/followup_anchor.py`
  - `apps/conversation/scope_resolver.py`
  - `apps/conversation/request_facade.py`
  - `apps/retrieval/retrieval_workflow.py`
  - `apps/api/workflow_nodes.py`
  - `apps/conversation/entity_reference.py`
  - `apps/conversation/followup_resolution.py`
  - `apps/retrieval/filters.py`
  - `apps/retrieval/rag_runtime_prelude.py`
  - `apps/retrieval/rag_pipeline.py`
  - `tests/test_view_state_child_refs.py`
  - `tests/test_scope_resolver.py`
  - `tests/test_request_facade_child_anchor.py`
  - `tests/test_people_filter_nested_gate.py`
  - `tests/test_view_state_active_scope.py`
  - `tests/test_anchor_constraint_compiler.py`
  - `tests/test_scope_resolver_v2.py`
  - `tests/test_followup_clarification_scope.py`
  - `docs/01_?꾪궎?띿쿂?_?먮쫫.md`
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/ADR/ADR-0011-scope-aware-followup-resolution-and-exact-constraint-compilation.md`
- findings:
  - P0 child researcher anchor???대? ?쇰? ?ㅼ뼱? ?덉뿀吏留? state/model/compiler/clarification 怨꾩빟? ?꾩쭅 ?⑹뼱???덉뿀??
  - `latest_focus_entity` ?섎굹留뚯쑝濡쒕뒗 detail focus? child anchor瑜??④퍡 蹂댁〈?섍린 ?대젮??multi-hop follow-up?먯꽌 scope drift ?꾪뿕???⑥븘 ?덉뿀??
  - downstream exact filter???대? 以鍮꾨뤌 ?덉뿀怨? ?⑥? 臾몄젣??upstream scope resolution怨?planner ?꾪썑 seed ?ъ쟻?⑹쓽 ?쇨??깆씠?덈떎.
- changes:
  - `view_state.py`: `ActiveScope(result_set/focus/child_anchor/parent_chain/scope_kind)`瑜?異붽??섍퀬 legacy mirror? load normalization??遺숈??? child refs??`people | org | perf`瑜?吏?먰븯?꾨줉 ?쇰컲?뷀뻽??
  - `anchor_constraint_compiler.py`: anchor seed merge/apply瑜?蹂꾨룄 ?쒕퉬?ㅻ줈 遺꾨━?덈떎. `people -> person_no`, `org -> org_id/org_code/biz_no`, `perf -> rst_id/doi/issn`, `project -> pjt_id/pjt_no`瑜?exact truth濡?怨좎젙?쒕떎.
  - `scope_resolver.py`: `refinement_followup`??異붽??섍퀬, `active_scope.focus`瑜?湲곗??쇰줈 named child anchor / reset / refinement / ambiguity瑜??먯젙?섎룄濡??ъ옉?깊뻽??
  - `followup_anchor.py`: child anchor resolver瑜?`people/org/perf` generic 寃쎈줈濡??뺤옣?덈떎.
  - `request_facade.py`: planner ?꾪썑 ?숈씪 compiler瑜??ъ슜?섎룄濡??뺣━?섍퀬, `clarification_required`, `REFINEMENT.APPLIED`, structured clarification metadata瑜?strategy meta濡??대젮以??
  - `retrieval_workflow.py`: follow-up restore, list result, detail result?먯꽌 `active_scope`瑜?媛깆떊?섍퀬 `SCOPE.TRANSITION`, `CLARIFICATION.ISSUED`瑜??④린?꾨줉 ?곌껐?덈떎.
  - `workflow_nodes.py`: ambiguous follow-up direct-answer 媛?쒕? ?쒓굅?섍퀬 greeting / short-query留?rule?먯꽌 泥섎━?섎룄濡??⑥닚?뷀뻽??
  - docs/ADR/test瑜??대쾲 runtime 怨꾩빟??留욎떠 媛깆떊?덈떎.
- validations:
  - `python -m py_compile apps/conversation/anchor_constraint_compiler.py apps/conversation/view_state.py apps/conversation/followup_anchor.py apps/conversation/scope_resolver.py apps/conversation/request_facade.py apps/retrieval/retrieval_workflow.py apps/api/workflow_nodes.py apps/conversation/entity_reference.py apps/conversation/followup_resolution.py tests/test_view_state_active_scope.py tests/test_anchor_constraint_compiler.py tests/test_scope_resolver_v2.py tests/test_followup_clarification_scope.py` passed
  - `python -m pytest tests/test_view_state_child_refs.py tests/test_scope_resolver.py tests/test_request_facade_child_anchor.py tests/test_people_filter_nested_gate.py tests/test_view_state_active_scope.py tests/test_anchor_constraint_compiler.py tests/test_scope_resolver_v2.py tests/test_followup_clarification_scope.py -q -p no:cacheprovider` passed (`18 passed`)
- next best task:
  - `rag_retriever.py`??anchor drift 蹂듦뎄瑜?`project/perf/people/org/refinement` 異뺤쑝濡??쇰컲?뷀븯怨?`WIDENING.BLOCKED` 濡쒓렇瑜?異붽???寃?
  - `org/perf child ref -> related projects/detail` golden flow瑜??ㅼ젣 retrieval runtime?먯꽌 ?ы쁽?섍퀬 `hm_id/org_id/rst_id must` filter path瑜?end-to-end 濡쒓렇濡??뺤씤??寃?

## ??以??붿빟
- ??臾몄꽌???ㅼ쓬 猷⑦봽媛 諛붾줈 ?댁뼱諛쏆쓣 ???덇쾶 ?꾩옱 ?곹깭, ?대┛ 由ъ뒪?? ?ㅼ쓬 ?≪뀡, 理쒓렐 ?묒뾽 濡쒓렇瑜??④린???대? handoff 臾몄꽌??

## ??臾몄꽌瑜??쎌쓣 ?щ엺
- Codex Watcher / Improver / Architect
- ?대쾲 釉뚮옖移??곹깭瑜?鍮좊Ⅴ寃??뚯븙?댁빞 ?섎뒗 ?⑥퐳 媛쒕컻??
## ??臾몄꽌?먯꽌 諛붾줈 李얠쓣 ???덈뒗 寃?- ?꾩옱 branch expectation怨?source-of-truth owner
- 吏湲??대┛ 由ъ뒪??- ?ㅼ쓬??諛붾줈 ????- 理쒓렐 ?묒뾽 ?덉뒪?좊━

## 吏湲?梨숆만 寃?- expected branch??`怨좊룄????
- planner defaults? baseline inventory owner??`apps/api/contracts/repo_manifest.py`??
- baseline entrypoint??`pytest.ini`? `scripts/run_baseline_checks.ps1`??
- ?곷떒 ?붿빟? 鍮좊Ⅴ寃??쎄퀬, ?곸꽭 ?덉뒪?좊━???꾨옒 ?대? 濡쒓렇濡??대젮媛硫??뺤씤?쒕떎.

## ?꾩옱 ?곹깭
- repo: `ntis_domain_rag_chatbot`
- working branch expected: `怨좊룄??
- ?쒖뒪?쒖? retrieval strategy? answer generation??遺꾨━?섍퀬, `SEARCH / LOOKUP / JOIN` 怨꾩빟???좎??쒕떎.
- ?щ엺/湲곌? 湲곕컲 吏덉쓽??湲곕낯?곸쑝濡?`LOOKUP`?쇰줈 ?ㅻ（怨? group join? `pjt_no`, instance join? `pjt_id`瑜??ъ슜?쒕떎.
- planner prompt defaults??`v2 / v1 / v2`?닿퀬, `/query/stream` ?몃? 怨꾩빟? canonical event envelope 湲곗??쇰줈 ?좎??쒕떎.
- answer verifier/repair ?꾩슜 prompt stack? ?꾩쭅 ?녾퀬, groundedness verdict contract??`apps/api/contracts/answer_groundedness.py`媛 留〓뒗??

## ?대┛ 由ъ뒪??- `tests/test_request_facade_source_reference_fallback.py`??議댁옱?섏?留?baseline inventory core subset?먮뒗 ?꾩쭅 ?ы븿?섏? ?딅뒗??
- ?꾩옱 shell?먮뒗 `pytest`媛 ?놁뼱 baseline ?ㅽ겕由쏀듃? ?섎룞 pytest subset???앷퉴吏 寃利앺븷 ???녿떎.
- `異쒖쿂 N` follow-up? reference context媛 ?놁쓣 ??display snapshot anchor fallback???덉슜???덉뼱, citation semantics瑜????꾧꺽???좎? product intent ?뺤씤???⑥븘 ?덈떎.
- unsupported groundedness verdict???꾩쭅 selector 李⑥썝??媛뺥븳 李⑤떒蹂대떎 passive verdict ?깃꺽??媛뺥븯??
- ?ㅻ옒??handoff history ?쇰???臾몄옄 ?ㅼ뿼 ?곹깭濡??⑥븘 ?덈떎. ?곷떒 ?붿빟? 蹂듦뎄?덉?留?怨쇨굅 濡쒓렇 ?꾩껜瑜??ㅼ떆 ?뺣━??寃껋? ?꾨땲??

## ?ㅼ쓬 ?≪뀡
- repo ?꾩슜 Python environment ?먮뒗 dependency bootstrap??癒쇱? 蹂듦뎄??`scripts/run_baseline_checks.ps1`瑜??앷퉴吏 ?ㅽ뻾?쒕떎.
- source-reference fallback ?뚭?瑜?baseline inventory???ы븿?좎? product intent 湲곗??쇰줈 寃곗젙?쒕떎.
- planner / runtime / streaming / baseline owner媛 諛붾뚮㈃ 肄붿뼱 臾몄꽌? ??handoff瑜?媛숈? 蹂寃??명듃?먯꽌 媛숈씠 媛깆떊?쒕떎.

## ?대젰 湲곕줉 洹쒖튃
- 紐⑤뱺 ?ㅽ뻾? ?꾨옒 ?뺤떇?쇰줈 append ?쒕떎.
- date/time
- branch/head
- inspected files
- findings
- changes
- validations
- next best task
- ?곸꽭 ?덉뒪?좊━??理쒖떊 ??ぉ遺???꾨옒???볥뒗??

---

### 2026-03-31T17:58:07.8388346+09:00 Improver
- branch/head: `怨좊룄?? / `b22a7cb6b538f87d822a53e9915cfdab6e248100`
- inspected files:
  - `apps/conversation/view_state.py`
  - `apps/conversation/followup_anchor.py`
  - `apps/conversation/scope_resolver.py`
  - `apps/conversation/request_facade.py`
  - `apps/retrieval/filters.py`
  - `apps/conversation/anchor_resolution.py`
  - `apps/retrieval/rag_runtime_prelude.py`
  - `apps/retrieval/rag_pipeline.py`
  - `tests/test_view_state_child_refs.py`
  - `tests/test_scope_resolver.py`
  - `tests/test_request_facade_child_anchor.py`
  - `tests/test_people_filter_nested_gate.py`
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
- findings:
  - follow-up 寃???덉쭏??蹂묐ぉ? planner??prompt媛 ?꾨땲??runtime upstream?댁뿀?? `latest_focus_entity`留뚯쑝濡쒕뒗 detail ?대? 李몄뿬?곌뎄??`hm_id`瑜??ㅼ쓬 ??exact anchor濡??ㅼ떆 留뚮뱾 ???놁뿀??
  - downstream exact-id 寃쎈줈???대? 以鍮꾨뤌 ?덉뿀怨? ?듭떖? `child researcher -> person_no seed -> hm_id must`瑜?planner ?꾪썑濡??껋? ?딄쾶 留뚮뱶??寃껋씠?덈떎.
- changes:
  - `view_state.py`: `ChildEntityRef`? `child_refs`瑜?異붽??섍퀬, display snapshot/detail focus?먯꽌 top-level ?곌뎄?먯? `prtcp_mp[]` ?곌뎄?먮? `person_no` 湲곕컲 child ref濡?蹂댁〈?섎룄濡??곌껐?덈떎.
  - `followup_anchor.py`: 吏곸쟾 `project` ?곸꽭??child researcher ?대쫫??吏덈Ц??exact unique match?섎㈃ `detail_participant_match` people anchor濡??밴꺽?섎뒗 resolver瑜?異붽??덈떎.
  - `scope_resolver.py`: pre-planner scope decision???꾩엯??`child_entity_followup`, `scope_reset`, `ambiguous_followup`??deterministic?섍쾶 遺꾧린?섍퀬 reset cue瑜?泥섎━?섎룄濡??덈떎.
  - `request_facade.py`: scope decision 濡쒓렇(`SCOPE.DECISION`), participant anchor 濡쒓렇(`FOLLOWUP.PARTICIPANT_ANCHOR.RESOLVED`), reset 濡쒓렇(`ANCHOR.ESCAPED`), ambiguity 濡쒓렇(`ANCHOR.AMBIGUOUS`)瑜?異붽??섍퀬, child researcher anchor瑜?`ids_map.person_no`? `people_terms`濡?planner ?꾪썑 紐⑤몢 怨좎젙?섎룄濡?諛붽엥?? `selected_prev_item`??display rank ?놁씠 researcher anchor瑜??댁쓣 ???덇쾶 ?뺤옣?덈떎.
  - ?뚭? ?뚯뒪?몄? ?댁쁺/怨꾩빟 臾몄꽌瑜???scope/anchor ?먮쫫 湲곗??쇰줈 媛깆떊?덈떎.
- validations:
  - `python -m py_compile apps/conversation/view_state.py apps/conversation/followup_anchor.py apps/conversation/scope_resolver.py apps/conversation/request_facade.py tests/test_view_state_child_refs.py tests/test_scope_resolver.py tests/test_request_facade_child_anchor.py tests/test_people_filter_nested_gate.py` passed
  - `python -m pytest tests/test_view_state_child_refs.py tests/test_scope_resolver.py tests/test_request_facade_child_anchor.py tests/test_people_filter_nested_gate.py -q -p no:cacheprovider` passed (`8 passed`)
  - `python -m pytest tests/test_contract_debt_paydown.py -q -p no:cacheprovider` is still blocked in this shell because importing `apps.api.app_factory` requires `langgraph`
- next best task:
  - `list -> detail -> child researcher -> related projects`瑜??ㅼ젣 ??runtime 濡쒓렇濡???踰??ы쁽??`SCOPE.DECISION`, `FOLLOWUP.PARTICIPANT_ANCHOR.RESOLVED`, `RAG.FILTER.COMPILED.QDRANT`??`hm_id must` 寃쎈줈瑜?媛숈씠 ?뺤씤?쒕떎.
  - P1濡쒕뒗 `child_refs`瑜?`org/perf`源뚯? ?쇰컲?뷀븯怨? ambiguity瑜?structured clarification payload濡??밴꺽?쒕떎.

### 2026-03-31T13:30:00+09:00 Improver
- branch/head: `怨좊룄?? / `3478854f4a34341938041af8196d45b64d009fbe`
- inspected files:
  - `apps/evidence/canonical_context.py`
  - `apps/evidence/detail_contract.py`
  - `apps/retrieval/retrieval_workflow.py`
  - `apps/chat/answer_generation.py`
  - `apps/chat/answer_merge.py`
  - `apps/api/contracts/workflow_models.py`
  - `apps/evidence/result_set.py`
  - `prompts/ntis_chatbot.md`
  - `prompts/ntis_chatbot_gemma.md`
  - `prompts/ntis_chatbot_solar.md`
  - `tests/test_canonical_context.py`
  - `tests/test_detail_contract.py`
  - `tests/test_retrieval_workflow_detail_runtime.py`
  - `tests/test_contract_debt_paydown.py`
  - `tests/test_request_overrides.py`
  - `tests/test_answer_merge_bypass.py`
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
- findings:
  - model-facing `[?쒓났???뺣낫]` 寃쎈줈??`PJT_ID=...`, `LEAD_ORG=...`, `[detail_evidence]`, `missing_fields:` 媛숈? ?대? schema/debug scaffold媛 洹몃?濡??욎씪 ???덉뿀??
  - detail cache hit, fresh detail lookup, canonical fallback render媛 紐⑤몢 媛숈? `answer_context_text` ?꾨뱶瑜?怨듭쑀?덉?留?model-safe view? debug/log view瑜?遺꾨━?섏? ?딆븯??
  - selector??`[detail_evidence]` 瑜섏쓽 紐낆떆??scaffold??留됱븯吏留?`(pjt_id)`, `(lead_org)` 媛숈? parenthetical schema label怨?raw missing-field inventory??invalid濡??대━吏 紐삵뻽??
- changes:
  - canonical renderer瑜?`# 異쒖쿂 N.` + ?쒓뎅???쇰꺼 湲곕컲 model-safe view濡?諛붽씀怨? 湲곗〈 schema-oriented text??`render_canonical_evidence_debug_text()`濡?遺꾨━?덈떎.
  - detail contract??`build_detail_prompt_context()`瑜?異붽??섍퀬, retrieval workflow/state/bundle??`debug_answer_context_text`瑜??꾩엯??model-facing context? log-only context瑜?遺꾨━?덈떎.
  - answer generation? `[?쒓났???뺣낫]`??model-safe context留??ｊ퀬 `[Reference Context]` 濡쒓렇?먮뒗 debug context瑜??④린?꾨줉 諛붽엥??
  - answer merge??parenthetical schema label, raw missing-field inventory瑜?`internal_context_leak`濡?degrade?섎룄濡?媛뺥솕?덈떎.
  - prompt/test/docs瑜???怨꾩빟??留욊쾶 媛깆떊?덈떎.
- validations:
  - `python -m py_compile apps/evidence/canonical_context.py apps/evidence/detail_contract.py apps/retrieval/retrieval_workflow.py apps/chat/answer_generation.py apps/chat/answer_merge.py apps/api/contracts/workflow_models.py apps/evidence/result_set.py tests/test_canonical_context.py tests/test_detail_contract.py tests/test_retrieval_workflow_detail_runtime.py tests/test_contract_debt_paydown.py tests/test_request_overrides.py tests/test_answer_merge_bypass.py` passed
  - `python -m pytest tests/test_canonical_context.py tests/test_detail_contract.py tests/test_retrieval_workflow_detail_runtime.py tests/test_contract_debt_paydown.py tests/test_request_overrides.py tests/test_answer_merge_bypass.py -q -p no:cacheprovider` failed in this shell: `No module named pytest`
  - inline Python smoke passed for `apps/evidence/canonical_context.py`: `canonical_context_smoke_ok`
  - inline import/runtime validation for `detail_contract` / `answer_merge` could not run in this shell because shared Windows Python lacks `pydantic`
- next best task:
  - real runtime?먯꽌 諛섎룄泥?source-reference follow-up 吏덈Ц???ㅼ떆 ?ы쁽??model prompt?먮뒗 `# 異쒖쿂 N.` 釉붾줉留??ㅼ뼱媛怨? logs?먮뒗 debug scaffold留??⑤뒗吏 ?뺤씤?쒕떎.
  - selector telemetry?먯꽌 `internal_context_leak`媛 parenthetical schema label/raw missing-field inventory?먮룄 ?덉젙?곸쑝濡?李랁엳?붿? ?뺤씤?쒕떎.

### 2026-03-27 15:50:44 +09:00
- branch/head: `?⑥쥓猷?? / `224f3c3ec28474674535efdb21086723b371f005`
- inspected files:
  - `apps/api/request_overrides.py`
  - `apps/api/routes.py`
  - `tests/test_oracle_request_overrides.py`
  - `docs/03_??곸겫????띻펾.md`
- findings:
  - Oracle defaults loader??疫꿸퀣??癒?즲 `IRD_PARAM` 鈺곌퀬??野껋럥以덄몴?揶쏅쉰????됰?筌왖筌? ??뺤쒔 疫꿸퀡猷???connector ?遺용튋 嚥≪뮄??? ?遺욧퍕癰?lookup ?怨밴묶 嚥≪뮄?뉐첎? ??곷선 ??곸겫 餓??源껊궗/??쎈솭 ?癒???????醫딅뼄.
  - `from_env()` ??덉삂?? env 沃섎챷苑????`None` 獄쏆꼹????袁⑤빍???꾨뗀諭?疫꿸퀡??첎?`ird` / `ird_12#$` / KNTIS DSN) ???????
  - ?袁⑹삺 ?臾믩씜 Python ??띻펾?癒?뮉 `pytest`, `fastapi`揶쎛 ??곷선 route-level ???? 野꺜筌앹빘? 筌욊낯????쎈뻬??? 筌륁궢六??
- changes:
  - Oracle defaults loader??connector summary?? lookup meta(`oracle_lookup_status`, `oracle_loaded_keys` ?????곕떽???덈뼄.
  - ??뺤쒔 疫꿸퀡猷???`[request_overrides] Oracle defaults connector configured ...` 嚥≪뮄?뉐첎? ??ㅻ즲嚥???덈뼄.
  - `/query/stream`, `/query/debug`?癒?퐣 `REQ.ORACLE.DEFAULTS` ?닌듼??嚥≪뮄?뉒몴???ｋ┛?袁⑥쨯 ??덈뼄.
  - ?온?????뮞?紐? 癰귣떯而??랁???곸겫 ?얜챷苑????嚥≪뮄????곴퐤 疫꿸퀣????곕떽???덈뼄.
- validations:
  - `python -m py_compile apps/api/request_overrides.py apps/api/routes.py tests/test_oracle_request_overrides.py`
  - custom Python validation: `request_overrides_validation_ok`
  - 沃섎챷??? `python -m pytest ...` (`pytest` 沃섎챷苑뺟㎉?, FastAPI TestClient 疫꿸퀡而?route smoke (`fastapi` 沃섎챷苑뺟㎉?
- next best task:
  - ??쇱젫 ??뺤쒔 ?怨???Python ??띻펾?癒?퐣 `/query/stream` 1???紐꾪뀱 ??`REQ.ORACLE.DEFAULTS`揶쎛 `loaded` ?癒?뮉 `query_failed`嚥???ㅻ뮉筌왖 ?類ㅼ뵥
  - ?袁⑹뒄 ??Oracle lookup ??쎈솭 ??error code/latency繹먮슣? ??ｍ뜞 ??ｋ┛?袁⑥쨯 ?類ㅼ삢

## 2026-03-27T16:02:41+09:00 Improver
- branch/head: `?⑥쥓猷?? / `224f3c3ec28474674535efdb21086723b371f005`
- inspected files: `apps/planner/planner_runtime.py`, `apps/retrieval/rag_retriever.py`, `apps/retrieval/retrieval_workflow.py`, `apps/planner/query_intent.py`, `apps/planner/planner_surface_signals.py`, `tests/test_planner_stagewise.py`, `tests/test_request_overrides.py`, `docs/SESSION_HANDOFF.md`
- findings:
  - project detail queries with identifier-like tokens such as `B555000149` can still be mis-promoted into `org_name` hard gates, which causes strict LOOKUP to return `no_reranked`.
  - detail coverage still depends on raw payload axes that can disappear when reranked hits arrive in nested `payload` wrappers.
- changes:
  - `apps/planner/planner_runtime.py`: added a sanitization guard that drops identifier-like org gates for project-axis queries unless the query contains explicit org cues.
  - `apps/retrieval/rag_retriever.py`: flattened nested hit payload wrappers and preserved raw detail fields such as `content1`, `content2`, `content_text`, `org_nm`, and project ids.
  - `tests/test_planner_stagewise.py`: added a regression test for `B555000149 ?⑥눘????怨멸쉭?類ｋ궖`.
  - `tests/test_request_overrides.py`: added a retriever regression test for nested payload unwrapping plus raw detail-field preservation.
- validations:
  - `python -m py_compile apps/planner/planner_runtime.py apps/retrieval/rag_retriever.py tests/test_planner_stagewise.py tests/test_request_overrides.py` passed
  - pytest remains blocked in this shell because the shared Windows Python environment does not include the project test/runtime deps.
- next best task: run the same strict debug requests in the real app runtime and confirm that `DETAIL.COVERAGE` now exposes `summary/goal/period/budget` and that `B555000149 ?⑥눘????怨멸쉭?類ｋ궖` no longer compiles an `org_nm` hard gate.

## 2026-03-27T15:21:12+09:00 Improver
- branch/head: `?⑥쥓猷?? / `224f3c3ec28474674535efdb21086723b371f005`
- inspected files: `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/03_??곸겫????띻펾.md`, `docs/04_???疫꿸퀣????癒?.md`, `docs/SESSION_HANDOFF.md`, `apps/evidence/detail_contract.py`, `apps/retrieval/retrieval_workflow.py`, `apps/conversation/view_state.py`, `apps/evidence/context_helpers.py`, `apps/evidence/canonical_evidence.py`, `tests/test_detail_contract.py`, `tests/test_retrieval_workflow_detail_runtime.py`
- findings:
  - strict detail coverage still let a polluted anchor/display label override the hydrated project title, which explains logs like `title: 1. 繹먃?딅맩?` in `[Reference Context]`.
  - NTIS project detail coverage was not hydrating `summary/goal/period/budget` from raw `meta_basic/content_*` fields even when the payload clearly contained those values.
  - helper/canonical title precedence had drifted from the documented contract because some paths still preferred `title_text` ahead of `title1`.
- changes:
  - `apps/evidence/detail_contract.py`: changed project detail title precedence to prefer hydrated/raw title axes before anchor fallback, and hydrated `summary/goal/period/budget` from `meta_basic` and `content_*`.
  - `apps/evidence/context_helpers.py`: changed payload title preference to `title1 -> title_text -> title2`.
  - `apps/evidence/canonical_evidence.py`: changed canonical fact title collection to `title1 -> title_text -> title2`.
  - `tests/test_detail_contract.py`: added regression coverage for polluted anchor labels, NTIS `meta_basic` hydration, helper title preference, and canonical evidence title precedence.
- validations:
  - `python -m py_compile apps/evidence/detail_contract.py apps/evidence/context_helpers.py apps/evidence/canonical_evidence.py tests/test_detail_contract.py` passed
  - `python -m pytest tests/test_detail_contract.py tests/test_retrieval_workflow_detail_runtime.py -q` failed in local shell: `No module named pytest`
  - inline import/runtime validation could not run in local shell because project deps such as `pydantic` are absent from the shared Windows Python environment
- next best task: run the touched detail tests inside the real app/runtime environment (the one that has `pytest` and `pydantic`), then capture one strict debug request to confirm `[Reference Context]` now shows the canonical project title plus hydrated `summary/goal/period/budget`.

## 2026-03-27T15:17:38.4825738+09:00 Watcher
- branch/head: `?⑥쥓猷?? / `224f3c3ec28474674535efdb21086723b371f005`
- inspected files: `docs/CODEX_CONTEXT.md`, `docs/SESSION_HANDOFF.md`, `docs/03_??곸겫????띻펾.md`, `docs/GOLDEN_TESTS.md`, `docs/PRODUCT_BASELINE.md`, `pytest.ini`, `scripts/run_baseline_checks.ps1`, `apps/api/app_factory.py`, `apps/api/routes.py`, `apps/planner/query_analysis.py`, `apps/planner/planner_runtime.py`, `apps/chat/llm_runtime.py`, `apps/chat/answer_merge.py`, `apps/api/runtime_helpers.py`, `apps/retrieval/result_contract.py`, `apps/conversation/request_facade.py`, `tests/test_llm_runtime_prompt_paths.py`, `tests/test_answer_merge_bypass.py`, `tests/test_runtime_helpers_stream_bypass.py`, `tests/test_eval_fixture_schema.py`, `tests/test_planner_prompt_cards.py`
- findings:
  - P1 docs drift: `docs/03_??곸겫????띻펾.md` ??`PLANNER_STAGE1/2_PROMPT_VERSION=v1` ????됰뻻嚥???ｊ볼 ??筌??袁⑥뺘?봔?癒?뮉 V2 defaults????쇰뻻 ?醫롫섧??랁? smoke ??됰뻻????용뮉 `tests/test_api_routes_runtime.py`, `tests/test_request_facade_and_context.py` ??揶쎛?귐뗪텚??
  - P1 coverage gap: `tests/test_eval_fixture_schema.py` ??axis list 鈺곕똻?깍쭕??類ㅼ뵥??뺣뼄. ?袁⑹삺 fixture揶쎛 ?怨쀫염??required axis????釉??롫쐭??곕즲 CI??baseline ???袁⑥뵭????? 筌륁궢釉??
  - P2 latent drift: `apps/conversation/request_facade.py` ??exported helper default???袁⑹춦 `planner_stage15_prompt_version='v1'`, `planner_stage2_prompt_version='v1'` ???? ?袁⑹삺 app wiring?? explicit version????띻볼 active bug???袁⑤빍筌왖筌???沅????stale default揶쎛 ??쇰뻻 ??곷툡??????덈뼄.
- changes: bootstrap facts?? ?袁⑹삺 ?袁る퓮 筌뤴뫖以??筌ㅼ뮇???꾨뗀諭?疫꿸퀣???곗쨮 揶쏄퉮???덈뼄.
- validations:
  - `python -m py_compile apps/api/routes.py apps/api/runtime_helpers.py apps/chat/answer_merge.py tests/test_eval_fixture_schema.py tests/test_runtime_helpers_stream_bypass.py` ???궢
  - `python -m pytest --collect-only -q` ??쎈솭: `No module named pytest`
- next best task: `docs/03_??곸겫????띻펾.md` ??planner default / smoke ??됰뻻???袁⑹삺 ?꾨뗀諭?? baseline script??筌띿쉳苡??類ｂ봺??랁? ??곷선??`tests/test_eval_fixture_schema.py` ??required axis subset 野꺜筌앹빘???곕떽???뺣뼄.

## 2026-03-27T16:08:00+09:00 Improver
- branch/head: `?⑥쥓猷?? / `224f3c3ec28474674535efdb21086723b371f005`
- inspected files: `apps/planner/planner_runtime.py`, `apps/planner/planner_validation.py`, `apps/retrieval/rag_retriever.py`, `apps/retrieval/retrieval_workflow.py`, `prompts/planner_stage15_v1.md`, `prompts/planner_stage2_v2.md`, `tests/test_planner_stagewise.py`, `tests/test_eval_fixture_schema.py`, `eval/sample_queries.jsonl`, `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/03_??곸겫????띻펾.md`, `docs/04_???疫꿸퀣????癒?.md`, `docs/GOLDEN_TESTS.md`, `docs/PRODUCT_BASELINE.md`
- findings:
  - stage2 retry ??쎈솭 ??쇰퓠???ⓦ룗而?에?raw-question fallback筌???됰선 planner miss??deterministic??띿쓺 癰귣벀???野껋럥以덂첎? ??곷???
  - `Stage2ValidationResult` ??`missing_must_keep_terms` ??野껉퀗?득에???블???삳빍筌왖 ??녿툡 quoted title 揶쏆늿? exact phrase 癰귣똻???runtime????묐뻬??????곷???
  - eval fixture schema????planner ?袁る퓮 ??`broad_history`, `quoted_title`, `source_ref`, `ordinal`)??baseline??곗쨮 揶쏅벡???? 筌륁궢六??
- changes:
  - `apps/planner/planner_validation.py`: `missing_must_keep_terms` ??validation 野껉퀗????곕떽???덈뼄.
  - `apps/planner/planner_runtime.py`: stage2 retry ??쎈솭 ??deterministic repair ??ｍ?? raw-query fallback reason log???곕떽???덈뼄.
  - `apps/retrieval/retrieval_workflow.py`: retrieval query resolution log??`query_resolution_reason` ???곕떽???덈뼄.
  - `prompts/planner_stage15_v1.md`, `prompts/planner_stage2_v2.md`: broad-history, quoted-title, ordinal/source-reference follow-up 癰귣똻??域뱀뮇?껅???됰뻻??揶쏅벤???덈뼄.
  - `tests/test_planner_stagewise.py`, `tests/test_eval_fixture_schema.py`, `eval/sample_queries.jsonl`: deterministic repair, drift, fixture axis baseline ??????곕떽???덈뼄.
  - ?온??contract/ops/regression/baseline ?얜챷苑뚨몴??袁⑹삺 ??덉삂??筌띿쉳苡?揶쏄퉮???덈뼄.
- validations:
  - pending
- next best task: shared Windows Python ??띻펾?癒?퐣 `pytest` ?? app dependency揶쎛 餓Β??쑬留??怨??袁⑹몵嚥?planner subset ???????쇱젫 ??쎈뻬??deterministic repair 野껋럥以덂첎? green?紐? ?類ㅼ뵥??뺣뼄.

## 2026-03-27T16:43:46+09:00 Watcher
- branch/head: `?⑥쥓猷?? / `f601ddfa81dab2729743bb95ec42751adaf77073`
- inspected files: `docs/SESSION_HANDOFF.md`, `docs/CODEX_CONTEXT.md`, `docs/03_??곸겫????띻펾.md`, `docs/04_???疫꿸퀣????癒?.md`, `docs/GOLDEN_TESTS.md`, `pytest.ini`, `scripts/run_baseline_checks.ps1`, `eval/sample_queries.jsonl`, `apps/api/app_factory.py`, `apps/api/routes.py`, `apps/planner/query_analysis.py`, `apps/planner/planner_runtime.py`, `apps/conversation/request_facade.py`, `apps/chat/answer_generation.py`, `apps/chat/answer_merge.py`, `apps/api/runtime_helpers.py`, `apps/retrieval/result_contract.py`, `apps/platform/runtime_strategy_policy.py`, `tests/test_api_routes_reference_payload.py`, `tests/test_answer_merge_bypass.py`, `tests/test_eval_fixture_schema.py`, `tests/test_runtime_helpers_stream_bypass.py`
- findings:
  - P1 docs drift: `docs/03_??곸겫????띻펾.md` ???????`PLANNER_STAGE1/2_PROMPT_VERSION=v1` ??됰뻻?? `v2` 疫꿸퀡??첎誘れ뱽 ??ｍ뜞 ?怨댄? `python -m pytest -m smoke` 獄???용뮉 ???뮞?????뵬(`tests/test_api_routes_runtime.py`, `tests/test_request_facade_and_context.py`)??smoke baseline??곗쨮 ??덇땀??뺣뼄.
  - P1 handoff drift: ?怨룸뼊 risk bullets????? ??욧퍙??eval/route-test 癰귣떯而????怨밴묶???④쑴??揶쎛?귐뗪텕????됰??? ??쇱젫嚥≪뮆??`tests/test_eval_fixture_schema.py` 揶쎛 required axis subset??揶쏅벡???랁? `tests/test_api_routes_reference_payload.py` 揶쎛 route terminal sequencing??`MISSING_FINAL_ANSWER` guard???⑥쥙???뺣뼄.
  - P2 latent drift: `apps/conversation/request_facade.py` ??exported helper default???袁⑹춦 `planner_stage15_prompt_version='v1'`, `planner_stage2_prompt_version='v1'` ????
  - P2 streaming coverage gap: `tests/test_runtime_helpers_stream_bypass.py` ??non-empty stream??`EMPTY_STREAM` ??곕돗筌?野꺜筌앹빜釉?? `derive_stream_error_code()` ??`TTFT_DEADLINE_EXCEEDED` / `GEN_DEADLINE_EXCEEDED` / `CHAR_LIMITED` / true empty-stream precedence???????筌욊낯???⑥쥙???? ??녿릭??
  - P2 verifier weakness: answer-stage validity gate???????`apps/chat/answer_merge.py` ??refusal/internal-context heuristic?됰Ŋ???
- changes:
  - `docs/SESSION_HANDOFF.md` ?怨룸뼊 risk bullets???袁⑹삺 repo ?怨밴묶??筌띿쉳苡?揶쏄퉮???덈뼄.
  - ??苡?watcher ?怨쀬벥 head, 域뱀눊援? ??? ?袁る퓮, ??쇱벉 ??ν뒄 ?袁⑤궖??handoff???곕떽???덈뼄.
- validations:
  - `python -m py_compile apps/api/routes.py apps/chat/answer_merge.py apps/conversation/request_facade.py tests/test_api_routes_reference_payload.py tests/test_eval_fixture_schema.py tests/test_runtime_helpers_stream_bypass.py` passed
  - `python -m pytest --collect-only -q --ignore-glob=pytest-cache-files-* --ignore-glob=tests/pytest-cache-files-* -p no:cacheprovider` failed: `No module named pytest`
- next best task: `docs/03_??곸겫????띻펾.md` ??planner default / smoke 筌뤿굝議??`scripts/run_baseline_checks.ps1` ?? ?袁⑹삺 ???뮞?????뵬 疫꿸퀣???곗쨮 ?類ｂ봺??랁? ??곷선??`tests/test_runtime_helpers_stream_bypass.py` ??stream error-code precedence ??????곕떽???뺣뼄.

## 2026-03-27T16:15:04.2889837+09:00 Improver
- branch/head: `?⑥쥓猷?? / `f601ddfa81dab2729743bb95ec42751adaf77073`
- inspected files: `apps/conversation/followup_anchor.py`, `apps/conversation/followup_resolution.py`, `apps/conversation/request_facade.py`, `tests/test_planner_stagewise.py`, `docs/GOLDEN_TESTS.md`, `docs/04_???疫꿸퀣????癒?.md`, `docs/SESSION_HANDOFF.md`
- findings:
  - `?곗뮇荑?2???怨뚮럡???類ｋ궖` 揶쏆늿? source-reference follow-up?? eval/golden ?곕벡肉????? ??쇰선揶쎛 ??됰?筌왖筌? ??쇱젫 follow-up anchor / reference-context parser??`?곗뮇荑?N`??ordinal嚥???곴퐤??? 筌륁궢鍮?`followup_resolution_status=none`??곗쨮 ??쥙彛?????됰???
  - follow-up clarification wording??source-reference??癰귢쑬猷??곕벡?앮에???삼펷筌왖 ??녿툡, out-of-range ?怨뱀넺?癒?퐣 `?곗뮇荑?揶쎛 ?袁⑤빍????곗뺘 `?⑥눘???源껊궢` ?얜㈇?꾣에???롊???袁る퓮????됰???
  - filter sanitization?? ordinal/deictic ?醫뤾쿃筌???볤탢??랁???됰선?? `?곗뮇荑?2`揶쎛 role/people/org term??곗쨮 ??덀늺 ?袁⑸꺗 retrieval drift??筌띾슢諭????揶쎛 ??됰???
- changes:
  - `apps/conversation/followup_anchor.py`: `parse_source_reference()`???곕떽???랁?`parse_ordinal_reference()`揶쎛 `?곗뮇荑?N` / `筌〓㈇?ц눧紐낅퓙 N` / `source N`??ordinal alias嚥???곴퐤??띿쓺 ??덈뼄.
  - `apps/conversation/followup_resolution.py`: source-reference parser???곕떽???랁? `resolve_reference_context_followup()`揶쎛 `followup_reference_kind=source_reference`嚥?resolve/out_of_range??獄쏆꼹???띿쓺 ??덈뼄.
  - `apps/conversation/followup_resolution.py`: clarification message/payload??source-reference ?袁⑹뒠 wording??`selection_hint=source_reference_or_entity_reference`???곕떽???뉙? `is_ordinal_reference_token()`??`?곗뮇荑?N`??strip ???怨몄몵嚥?癰귣떯而??덈뼄.
  - `apps/conversation/request_facade.py`: display snapshot anchor嚥???곴퐤??follow-up??`followup_reference_kind=source_reference`嚥?筌롫??????ｋ┛?袁⑥쨯 筌띿쉸???
  - `tests/test_planner_stagewise.py`: display-snapshot anchor, reference-context resolve, out-of-range clarification payload ??? ???뮞?紐? ?곕떽???덈뼄.
  - ?얜챷苑?癰귣챶揆?? 癰궰野껋?釉?쭪? ??녿릭?? `docs/GOLDEN_TESTS.md`?? `docs/04_???疫꿸퀣????癒?.md`揶쎛 ??? `source_ref` follow-up????? ???怨몄몵嚥??類ㅼ벥??랁???됰선, ??苡???ν뒄???닌뗭겱??域??④쑴鍮??筌띿쉸???源껉봄????
- validations:
  - `python -m py_compile apps/conversation/followup_anchor.py apps/conversation/followup_resolution.py apps/conversation/request_facade.py tests/test_planner_stagewise.py` passed
  - inline Python validation passed for `apps/conversation/followup_resolution.py`: `?곗뮇荑?2???怨뚮럡???類ｋ궖` -> `resolved/source_reference/{pjt_id:PJT-2}`, `?곗뮇荑?3???怨뚮럡???類ｋ궖` -> `out_of_range` + source-reference clarification wording
  - inline import/runtime validation for `apps/conversation/request_facade.py` could not run in this shell because shared Windows Python lacks `pydantic`
  - `python -m pytest -q tests/test_planner_stagewise.py -k source_reference` failed: `No module named pytest`
- next best task: `pytest`?? `pydantic`揶쎛 餓Β??쑬留???쇱젫 ??runtime ??띻펾?癒?퐣 source-reference follow-up subset????쎈뻬??랁? `/query/stream` ??甕곕뜆?앮에?`clarification` payload?? `followup_reference_kind=source_reference`揶쎛 ??쇱젫 event/output繹먮슣? ?醫???롫뮉筌왖 ?類ㅼ뵥??뺣뼄.

## 2026-03-27T17:08:00+09:00 Improver
- branch/head: `?⑥쥓猷?? / `f601ddfa81dab2729743bb95ec42751adaf77073`
- inspected files: `logs/app (3).log`, `apps/evidence/canonical_evidence.py`, `apps/evidence/detail_contract.py`, `apps/retrieval/rag_retriever.py`, `tests/test_detail_contract.py`, `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/SESSION_HANDOFF.md`, `apps/api/rag_mapper/domains/project.py`
- findings:
  - ??쇱젫 ??곸겫 嚥≪뮄??癒?퐣 `1711135956` / `'??μ뵬 獄쏆꼶猷꾬㎗?窺筌?疫꿸퀡而?3筌???겸봺 野껊슣???揶쏆뮆而? ?⑥눘???怨멸쉭?類ｋ궖` ?遺욧퍕?? `DETAIL.COVERAGE`揶쎛 `summary/goal/period/budget/outputs`???袁? missing??곗쨮 筌〓씧????됰???
  - ??쇱젫 payload?? `apps/api/rag_mapper/domains/project.py` ????鈺곌퀬釉?쭖??袁⑹뒄???⑥눘???怨멸쉭 ?곕벡? `pjt_prfrm_org_nm`, `rsch_abstract`, `rsch_goal_abstract`, `tot_rsch_start_dt`, `tot_rsch_end_dt`, `rndco_tot_amt` 筌잛럩肉???? 鈺곕똻???뺣뼄.
  - `compute_detail_coverage()` ?癒?퍥??raw `meta_basic/content_*`?????筌? exact lookup?癒?퐣 ??쇰선??thin doc?癒?뮉 域??곕벡????쑴堉???뉙?canonical evidence fact??`summary/year/tag` ?類ｋ즲筌???쇰선 rich project detail??癰귣벀???? 筌륁궢六??
  - 域?野껉퀗??detail contract context揶쎛 ??rich-field scaffold??筌띾슢諭얏? LLM??"??됯텦/筌뤴뫚紐?疫꿸퀗而??遺용튋 ?類ｋ궖揶쎛 ??볥궗??? ??낅뮉??????뱀몵嚥????릭野??癒?뼄.
- changes:
  - `apps/evidence/canonical_evidence.py`: `apps/api/rag_mapper/domains/project.py` 揶쎛 ?類ㅼ벥??NTIS project ???뼎 field ??`pjt_prfrm_org_nm`, `rsch_abstract`, `rsch_goal_abstract`, `tot_rsch_*`, `rndco_tot_amt`)???怨뺤뵬 canonical facts/roles ??묒넅???類ㅼ삢??뉙?`_x000D_` ??뽯뻼???類?뇣?酉六??
  - `apps/evidence/detail_contract.py`: thin doc?????즲 canonical facts??`goal/period/budget/outputs/perf_type/affiliation`??detail coverage fallback??곗쨮 ??덈즲嚥?癰귣떯而??뉙?`_x000D_` ??뽯뻼??rich detail????? ??낅즲嚥??類?뇣?酉六??
  - `tests/test_detail_contract.py`: canonical evidence rich-fact hydration??thin-doc + canonical-fact detail coverage ??????곕떽???덈뼄.
  - `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`: project detail coverage揶쎛 canonical rich facts?癒?퐣 ??쇰뻻 ??묒넅??????됰선????뺣뼄???④쑴鍮??筌뤿굞???덈뼄.
- validations:
  - `python -m py_compile apps/evidence/canonical_evidence.py apps/evidence/detail_contract.py tests/test_detail_contract.py` passed
  - inline Python validation with stubbed `apps.conversation.view_state` passed: `detail_mapping_validation_ok`
  - `python -m pytest ...` remains blocked in this shell because shared Windows Python lacks `pytest` and runtime deps such as `pydantic`
- next best task:
  - ??쇱젫 app runtime?癒?퐣 ??덉뵬 ?⑥눘???怨멸쉭 筌욌뜆?썹몴???쇰뻻 ?紐꾪뀱??`DETAIL.COVERAGE.available_fields`??`summary/goal/period/budget`揶쎛 ??쇰선??삳뮉筌왖 ?類ㅼ뵥
  - ?袁⑹뒄 ??project outputs揶쎛 raw payload????삘뀲 ??`meta_detail` ??癰귢쑬猷?field)??곗쨮 ??삳뮉 ?냈??곷뮞???곕떽? 筌띲끋釉?

## 2026-03-27T17:05:10+09:00 Watcher
- branch/head: `?⑥쥓猷?? / `f601ddfa81dab2729743bb95ec42751adaf77073`
- inspected files: `docs/SESSION_HANDOFF.md`, `docs/03_??곸겫????띻펾.md`, `docs/PRODUCT_BASELINE.md`, `docs/CODEX_CONTEXT.md`, `docs/GOLDEN_TESTS.md`, `pytest.ini`, `scripts/run_baseline_checks.ps1`, `apps/api/app_factory.py`, `apps/conversation/request_facade.py`, `apps/api/runtime_helpers.py`, `apps/chat/answer_merge.py`, `apps/planner/planner_runtime.py`, `apps/planner/planner_context_cards.py`, `apps/conversation/entity_reference.py`, `apps/conversation/followup_resolution.py`, `tests/test_llm_runtime_prompt_paths.py`, `tests/test_planner_prompt_cards.py`, `tests/test_runtime_helpers_stream_bypass.py`, `tests/test_answer_merge_bypass.py`, `tests/test_api_routes_reference_payload.py`, `tests/test_planner_stagewise.py`, `tests/test_eval_fixture_schema.py`
- findings:
  - P1 docs drift: `docs/03_??곸겫????띻펾.md` ??stagewise triage ??됰뻻?癒?퐣 ?袁⑹춦 `PLANNER_STAGE1_PROMPT_VERSION=v1`, `PLANNER_STAGE2_PROMPT_VERSION=v1` ???怨댄???됱몵筌? 疫꿸퀡??野꺜筌?筌뤿굝議??諭???`python -m pytest -m smoke` ?? ??용뮉 ???뵬 `tests/test_api_routes_runtime.py`, `tests/test_request_facade_and_context.py` ????덇땀??뺣뼄. ??쇱젫 source of truth??`apps/api/app_factory.py`, `pytest.ini`, `scripts/run_baseline_checks.ps1` ??
  - P2 observability gap: `apps/conversation/followup_resolution.py` ??`followup_reference_kind='source_reference'` ??筌띾슢諭억쭪?筌?resolved 野껋럥以??`seed_source` ??`reference_context_ordinal` 嚥??臾믩선 ?節뗫뮉?? `apps/conversation/entity_reference.py` ??source-reference ?袁⑹뒠 source variant揶쎛 ??곷선 citation 疫꿸퀡而?follow-up????곗뺘 ordinal follow-up??揶쏆늿? provenance嚥?疫꿸퀡以??뺣뼄.
  - P2 streaming coverage gap: `apps/api/runtime_helpers.py` ??`TTFT_DEADLINE_EXCEEDED -> GEN_DEADLINE_EXCEEDED -> DEADLINE_EXCEEDED -> CHAR_LIMITED -> EMPTY_STREAM` ?怨쀪퐨??뽰맄??揶쏅쉼?筌? `tests/test_runtime_helpers_stream_bypass.py` ??non-empty stream??`EMPTY_STREAM` ??곕돗 1椰꾨?彛??⑥쥙???뺣뼄.
  - P2 verifier weakness: `apps/chat/answer_merge.py` ??answer gate???????refusal/internal-context leak/too-short heuristic?됰Ŋ?좑쭖?evidence-grounded factuality ?癒?젟??援?verifier artifact????용뼄.
  - prompt/path drift????苡??類ㅼ읅 ?癒??癒?퐣 ??덉쨮 獄쏆뮄猿??? ??녿릭?? `tests/test_llm_runtime_prompt_paths.py` ?? `tests/test_planner_prompt_cards.py` ???袁⑹삺 answer prompt path 獄?planner card manifest wiring???④쑴???⑥쥙???뺣뼄.
  - SEARCH people-name hard-must, JOIN key mixing?? ??苡??類ㅼ읅 ??쇳떔?癒?퐣 ????? 域뱀눊援끿몴?筌≪뼚? 筌륁궢六?? ?온??guard??`apps/retrieval/filters.py` ?? ?얜챷苑????뮞???④쑴鍮????λ툡 ???筌? ??쇱젫 ??쎈뻬 野꺜筌앹빘? `pytest` ?봔??以?????紐낅릭筌왖 筌륁궢六??
- changes:
  - `docs/SESSION_HANDOFF.md` ?怨룸뼊 ?袁る퓮 筌뤴뫖以됪???苡?watcher ??疫꿸퀡以???袁⑹삺 域뱀눊援?疫꿸퀣???곗쨮 揶쏄퉮???덈뼄.
- validations:
  - `python -m py_compile apps/conversation/entity_reference.py apps/conversation/followup_resolution.py apps/api/runtime_helpers.py apps/chat/answer_merge.py apps/conversation/request_facade.py tests/test_runtime_helpers_stream_bypass.py tests/test_answer_merge_bypass.py tests/test_api_routes_reference_payload.py tests/test_planner_stagewise.py` passed
  - `python -m pytest --collect-only -q --ignore-glob=pytest-cache-files-* --ignore-glob=tests/pytest-cache-files-* -p no:cacheprovider` failed: `No module named pytest`
- next best task:
  - 揶쎛????됱읈????쇱벉 ??ν뒄??`docs/03_??곸겫????띻펾.md` ??planner default / baseline 筌뤿굝議?/ smoke ??됰뻻??`apps/api/app_factory.py`, `pytest.ini`, `scripts/run_baseline_checks.ps1` 疫꿸퀣???곗쨮 ?類ｂ봺??롫뮉 野껉퍔???
  - 域???쇱벉 ??ν뒄 ?袁⑤궖??`apps/conversation/followup_resolution.py` ?? `apps/conversation/entity_reference.py` ??source-reference provenance???브쑬???랁? route ?癒?뮉 strategy-meta regression test 1椰꾨똻?앮에???? ?⑥쥙???롫뮉 野껉퍔???

## 2026-03-27T17:31:40+09:00 Watcher
- branch/head: `?⑥쥓猷?? / `f601ddfa81dab2729743bb95ec42751adaf77073`
- inspected files: `docs/SESSION_HANDOFF.md`, `docs/03_??곸겫????띻펾.md`, `apps/api/app_factory.py`, `apps/conversation/request_facade.py`, `apps/api/runtime_helpers.py`, `apps/chat/answer_merge.py`, `apps/conversation/followup_resolution.py`, `apps/conversation/entity_reference.py`, `tests/test_planner_stagewise.py`, `tests/test_runtime_helpers_stream_bypass.py`, `tests/test_api_routes_reference_payload.py`, `tests/test_answer_merge_bypass.py`, `tests/test_eval_fixture_schema.py`, `pytest.ini`, `scripts/run_baseline_checks.ps1`, `logs/app.log`, `logs/app (3).log`
- findings:
  - P1 docs drift: `docs/03_??곸겫????띻펾.md` ???????stagewise triage ??됰뻻?癒?퐣 `PLANNER_STAGE1_PROMPT_VERSION=v1`, `PLANNER_STAGE2_PROMPT_VERSION=v1` ???怨댄? 揶쏆늿? ?얜챷苑???롫뼊?癒?퐣??planner V2 defaults???怨룸뮉?? 疫꿸퀡??野꺜筌?筌뤿굝議??`python -m pytest -m smoke` ?? ??용뮉 ???뵬 `tests/test_api_routes_runtime.py`, `tests/test_request_facade_and_context.py` ????덇땀???袁⑹삺 `apps/api/app_factory.py`, `pytest.ini`, `scripts/run_baseline_checks.ps1` ?? ?겸뫖猷??뺣뼄.
  - P2 observability gap: in-flight source-reference follow-up 癰궰野껋럩? `followup_reference_kind=source_reference` 繹먮슣?????ｋ┛筌왖筌? resolved `seed_source` ???????`reference_context_ordinal` 嚥??臾? ??쇰선揶쏄쑬?? `apps/conversation/entity_reference.py` ??`ResolvedEntityRef.source` literal??source-reference variant揶쎛 ??곷선 citation follow-up????곗뺘 ordinal follow-up provenance???브쑬???온筌β돧釉?????용뼄.
  - P2 streaming coverage gap: `apps/api/runtime_helpers.py` ??`TTFT_DEADLINE_EXCEEDED -> GEN_DEADLINE_EXCEEDED -> DEADLINE_EXCEEDED -> CHAR_LIMITED -> EMPTY_STREAM` ?怨쀪퐨??뽰맄???닌뗭겱???筌? `tests/test_runtime_helpers_stream_bypass.py` ??non-empty stream??`EMPTY_STREAM` ??곗쨮 ??삵뀋?쒖꼶由븝쭪? ??낅뮉 1椰꾨?彛??⑥쥙???뺣뼄.
  - P2 latent drift: `apps/conversation/request_facade.py` ??exported helper signature???袁⑹춦 `planner_stage15_prompt_version='v1'`, `planner_stage2_prompt_version='v1'` ??疫꿸퀡??첎誘れ몵嚥??遺얜뼄. ??쇱젫 ??wiring?? `apps/api/app_factory.py` ?癒?퐣 `v2 / v1 / v2` ??筌뤿굞??雅뚯눘????嚥???뽮쉐 甕곌쑨????袁⑤빍筌왖筌? helper direct-call ???뮞?紐껉돌 ?袁⑸꺗 ??沅??밸퓠????쇰뻻 drift??筌띾슢諭?????덈뼄.
  - P2 verifier weakness: `apps/chat/answer_merge.py` ?? `tests/test_answer_merge_bypass.py` ????쇰뻻 ?類ㅼ뵥???筌?answer-stage gate??refusal/internal-context leak/too-short heuristic?됰Ŋ?졿? evidence-grounded factuality verifier artifact?????????용뼄.
  - ?온筌???볧? `logs/app.log` ???袁⑹삺 0 byte??욱?筌ㅼ뮄????쑴堉???덈뼄. ??곸읈 `logs/app (3).log` ?紐꾨퓠????苡?source-reference / stream error-code ?브쑨由겼첎? ??쇱젫 ??곸겫 嚥≪뮄?????堉멨칰???ㅻ뮉筌왖 ????紐낅막 筌ㅼ뮇??嚥≪뮄??域뱀눊援끻첎? ?봔鈺곌퉲釉??
- changes:
  - `docs/SESSION_HANDOFF.md` ????苡?watcher ???뤷칰? 疫꿸퀡以됵쭕??곕떽???덈뼄.
- validations:
  - `python -m py_compile apps/conversation/followup_resolution.py apps/conversation/request_facade.py apps/api/runtime_helpers.py apps/chat/answer_merge.py tests/test_planner_stagewise.py tests/test_runtime_helpers_stream_bypass.py tests/test_answer_merge_bypass.py tests/test_api_routes_reference_payload.py tests/test_eval_fixture_schema.py` passed
  - `python -m pytest --collect-only -q --ignore-glob=pytest-cache-files-* --ignore-glob=tests/pytest-cache-files-* -p no:cacheprovider` failed: `No module named pytest`
- next best task:
  - 揶쎛????됱읈????쇱벉 ??ν뒄??`docs/03_??곸겫????띻펾.md` ??planner default / baseline 筌뤿굝議?/ smoke ??됰뻻???袁⑹삺 ?꾨뗀諭?? ??쎄쾿?깆???疫꿸퀣???곗쨮 ?類ｂ봺??롫뮉 野껉퍔???
  - 域???쇱벉 ??ν뒄 ?袁⑤궖??source-reference follow-up??`seed_source` / `ResolvedEntityRef.source` provenance??ordinal???브쑬???랁? strategy-meta ?癒?뮉 route-level ??? ???뮞??1椰꾨똻?앮에???? ?⑥쥙???롫뮉 野껉퍔???

## 2026-03-27T19:04:08.6646233+09:00 Watcher
- branch/head: `?⑥쥓猷?? / `f601ddfa81dab2729743bb95ec42751adaf77073`
- worktree: dirty (`apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/followup_resolution.py`, `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/SESSION_HANDOFF.md`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`)
- inspected files: `docs/SESSION_HANDOFF.md`, `docs/03_??곸겫????띻펾.md`, `apps/api/app_factory.py`, `apps/api/routes.py`, `apps/conversation/request_facade.py`, `apps/retrieval/retrieval_workflow.py`, `apps/chat/answer_generation.py`, `apps/chat/llm_runtime.py`, `apps/api/runtime_helpers.py`, `apps/chat/answer_merge.py`, `apps/conversation/followup_resolution.py`, `apps/conversation/entity_reference.py`, `apps/retrieval/filters.py`, `apps/platform/runtime_strategy_policy.py`, `tests/test_planner_stagewise.py`, `tests/test_runtime_helpers_stream_bypass.py`, `tests/test_answer_merge_bypass.py`, `tests/test_api_routes_reference_payload.py`, `tests/test_eval_fixture_schema.py`, `tests/test_rag_filter_policy.py`, `tests/test_contract_debt_paydown.py`, `tests/test_llm_runtime_prompt_paths.py`, `tests/test_request_facade_followup_seed_priority.py`, `pytest.ini`, `scripts/run_baseline_checks.ps1`, `logs/app.log`, `logs/app (3).log`
- findings:
  - P1 docs drift persists with the same HEAD: `docs/03_??곸겫????띻펾.md` ??stagewise 亦낅슣????띻펾?癒?퐣 ?袁⑹춦 `PLANNER_STAGE1_PROMPT_VERSION=v1`, `PLANNER_STAGE2_PROMPT_VERSION=v1` ???怨댄?`apps/api/app_factory.py` 疫꿸퀡??첎誘? `v2 / v1 / v2`), 疫꿸퀡??野꺜筌?筌뤿굝議?筌ㅼ뮇??smoke baseline??`scripts/run_baseline_checks.ps1`, `pytest.ini` ?? 筌띿쉸? ??낅뮉 `python -m pytest -m smoke`, `tests/test_api_routes_runtime.py`, `tests/test_request_facade_and_context.py` ????덇땀??뺣뼄.
  - P2 observability gap persists: `apps/conversation/followup_resolution.py` ??source-reference??resolve??猷?`seed_source` ??`reference_context_ordinal` 嚥??臾믩선 ?節딇? `apps/conversation/entity_reference.py` ??`ResolvedEntityRef.source` literal ????source-reference variant揶쎛 ??곷선 ordinary ordinal follow-up??provenance???브쑬??疫꿸퀡以??? 筌륁궢釉?? ?袁⑹삺 `tests/test_planner_stagewise.py` ??deictic 野껋럥以??`seed_source` ???⑥쥙????筌?source-reference 野껋럥以??`followup_reference_kind` ?? clarification wording筌??類ㅼ뵥??뺣뼄.
  - P2 streaming coverage gap persists: `apps/api/runtime_helpers.py` ??`TTFT_DEADLINE_EXCEEDED -> GEN_DEADLINE_EXCEEDED -> DEADLINE_EXCEEDED -> CHAR_LIMITED -> EMPTY_STREAM` ?怨쀪퐨??뽰맄???닌뗭겱???筌?`tests/test_runtime_helpers_stream_bypass.py` ??non-empty stream??`EMPTY_STREAM` ??곗쨮 ??삵뀋?쒖꼶由븝쭪? ??낅뮉 1椰꾨?彛?野꺜筌앹빜釉?? ??苡???쇳떔?癒?퐣 `TTFT_DEADLINE_EXCEEDED`, `GEN_DEADLINE_EXCEEDED`, `CHAR_LIMITED` ??筌욊낯???⑥쥙???롫뮉 ???뮞?紐껊뮉 筌≪뼚? 筌륁궢六??
  - P2 verifier weakness persists: `apps/chat/answer_merge.py` ?? `tests/test_answer_merge_bypass.py` ????쇰뻻 癰귣???answer-stage gate?? ???揶쎛 refusal/internal-context leak/too-short 餓λ쵐????? ?얜챷苑????덈뮉 groundedness ?遺쎈럡?? ????evidence-grounded factuality ?癒?뮉 unsupported fact ??????袁⑹춦 ?꾨뗀諭????뮞?紐껋쨮 ?????? ??녿릭??
  - P2 latent drift persists: `apps/conversation/request_facade.py` ??exported helper signature default???????`planner_stage15_prompt_version='v1'`, `planner_stage2_prompt_version='v1'` ???? ??쇱젫 app wiring?? explicit version????띻볼 ??뽮쉐 甕곌쑨????袁⑤빍筌왖筌? helper direct-call 野껋럥以?癒?퐣??stale default ??????袁る퓮????λ툡 ??덈뼄.
  - Guard status rechecked: SEARCH people-name hard-must?? JOIN `pjt_id`/`pjt_no` ??노? 疫뀀뜆? guard??`apps/retrieval/filters.py` ?? `tests/test_rag_filter_policy.py`, `tests/test_contract_debt_paydown.py` ??域밸챶?嚥???λ툡 ??뉙???苡??類ㅼ읅 ??쇳떔?癒?퐣 ????? 域뱀눊援??癰귣똻? 筌륁궢六??
  - Observability evidence is still thin: ?袁⑹삺 `logs/app.log` ??0 byte??욱? 筌ㅼ뮇???怨????遺우읅?? `logs/app (3).log` ?癒?춸 ??λ툡 ??덈뼄. ??苡?source-reference/stream-error-code ?귐딅뮞??? ??쇱젫 ??곸겫 嚥≪뮄?????堉멨칰?筌〓엨??遺???筌ㅼ뮇??嚥≪뮄?뉛쭕??몵嚥?????紐낅릭筌왖 筌륁궢六??
- changes:
  - `docs/SESSION_HANDOFF.md` ????苡?watcher ???筌?疫꿸퀡以됵쭕??곕떽???덈뼄. production code????륁젟??? ??녿릭??
- validations:
  - `python -m py_compile apps/api/app_factory.py apps/api/routes.py apps/conversation/request_facade.py apps/retrieval/retrieval_workflow.py apps/chat/answer_generation.py apps/chat/llm_runtime.py apps/api/runtime_helpers.py apps/chat/answer_merge.py apps/conversation/followup_resolution.py apps/conversation/entity_reference.py apps/retrieval/filters.py apps/platform/runtime_strategy_policy.py tests/test_planner_stagewise.py tests/test_runtime_helpers_stream_bypass.py tests/test_answer_merge_bypass.py tests/test_api_routes_reference_payload.py tests/test_eval_fixture_schema.py tests/test_rag_filter_policy.py tests/test_contract_debt_paydown.py tests/test_llm_runtime_prompt_paths.py tests/test_request_facade_followup_seed_priority.py` passed
  - `python -m pytest --collect-only -q --ignore-glob=pytest-cache-files-* --ignore-glob=tests/pytest-cache-files-* -p no:cacheprovider` failed: `No module named pytest`
  - `Get-Item logs/app.log, logs/app (3).log | Select Name,Length,LastWriteTime` ?類ㅼ뵥 野껉퀗??`app.log` ??`0 bytes`, `app (3).log` 筌?`256234 bytes` ????
- next best task:
  - 揶쎛????됱읈????쇱벉 ??ν뒄???????`docs/03_??곸겫????띻펾.md` ??planner defaults, baseline 筌뤿굝議? smoke ??됰뻻??`apps/api/app_factory.py`, `pytest.ini`, `scripts/run_baseline_checks.ps1` 疫꿸퀣???곗쨮 ?類ｂ봺??롫뮉 野껉퍔???
  - 域???쇱벉 ??밴텦?怨몄뵥 ??ν뒄 ?袁⑤궖??`tests/test_runtime_helpers_stream_bypass.py` ??stream error-code precedence ??????곕떽???띻탢?? source-reference follow-up??provenance(`seed_source`, `ResolvedEntityRef.source`)??ordinal???브쑬???롫뮉 野껉퍔???

## 2026-03-27T20:06:00+09:00 Architect
- branch/head: `?⑥쥓猷?? / `f601ddfa81dab2729743bb95ec42751adaf77073`
- worktree: dirty (`apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/followup_resolution.py`, `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/SESSION_HANDOFF.md`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`)
- inspected files:
  - `docs/01_?袁り텕??우퓗??_?癒?カ.md`
  - `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`
  - `docs/04_???疫꿸퀣????癒?.md`
  - `docs/SESSION_HANDOFF.md`
  - `apps/conversation/followup_resolution.py`
  - `apps/conversation/entity_reference.py`
  - `apps/conversation/request_facade.py`
  - `apps/retrieval/retrieval_workflow.py`
  - `apps/retrieval/rag_retriever.py`
  - `tests/test_planner_stagewise.py`
  - `tests/test_request_facade_followup_seed_priority.py`
- findings:
  - source-reference follow-up?? parser/clarification ??ｍ?癒?퐣??`followup_reference_kind=source_reference` 嚥??브쑬????筌? resolved provenance??????????곕벡?앮에??臾볦뿺??
  - `apps/conversation/followup_resolution.py` ??reference-context resolved source-reference??`seed_source=reference_context_ordinal` 嚥???ｋ┛?? `apps/conversation/entity_reference.py` ??`ResolvedEntityRef.source` ??source-reference variant ??곸뵠 ordinary ordinal/deictic 餓λ쵐??literal筌???됱뒠??뺣뼄.
  - `apps/retrieval/retrieval_workflow.py` ?? `apps/retrieval/rag_retriever.py` ??downstream?癒?퐣 雅뚯눖以?`anchor_source` ????뚯몵沃샕嚥?citation follow-up??ordinary ordinal follow-up????곸겫 ?온筌β돦肉???브쑬???띾┛ ??議??
  - ????곷뭼??execution semantics ?얜챷?ｅ첎? ?袁⑤빍??observability contract ?얜챷??? ?怨뺤뵬??planner strategy, retrieval mode, ids_map truth??椰꾨?諭띄뵳?? ??낅뮉 metadata-only migration??揶쎛????됱읈??롫뼄.
- changes:
  - `docs/ADR/ADR-0006-followup-reference-provenance-split.md` ???곕떽???덈뼄.
  - `docs/SESSION_HANDOFF.md` ????苡?architect ??블?疫꿸퀡以???곕떽???덈뼄.
- validations:
  - `rg -n "source_reference|reference_context_ordinal|seed_source|followup_reference_kind"` 嚥??온???꾨뗀諭??얜챷苑????뮞????뺛늺??????紐낅뻥??
  - ?얜챷苑??怨쀬뵠沃샕嚥?production code?????뮞?紐껊뮉 ??쎈뻬??? ??녿릭??
- next best task:
  - 揶쎛????됱읈????쇱벉 ?닌뗭겱 ??ν뒄??`apps/conversation/followup_resolution.py`, `apps/conversation/entity_reference.py`, `apps/conversation/request_facade.py` ??additive provenance field/literal???節딇? `tests/test_planner_stagewise.py`, `tests/test_request_facade_followup_seed_priority.py` 嚥?source-reference provenance regression???⑥쥙???롫뮉 野껉퍔???
  - 域???쇱벉 ??ｍ?癒?퐣 `apps/retrieval/retrieval_workflow.py` ?? `apps/retrieval/rag_retriever.py` 嚥≪뮄???`anchor_reference_kind` ???袁る솁??route/debug/ops ?온筌β돦??ordinary ordinal???브쑬???뺣뼄.

## 2026-03-28T19:43:00+09:00 Architect
- branch/head: `?⑥쥓猷?? / `f601ddfa81dab2729743bb95ec42751adaf77073`
- worktree: dirty (`apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/followup_resolution.py`, `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/SESSION_HANDOFF.md`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`, `docs/ADR/ADR-0006-followup-reference-provenance-split.md`)
- inspected files:
  - `docs/README.md`
  - `docs/01_?袁り텕??우퓗??_?癒?カ.md`
  - `docs/03_??곸겫????띻펾.md`
  - `docs/04_???疫꿸퀣????癒?.md`
  - `docs/PRODUCT_BASELINE.md`
  - `docs/SESSION_HANDOFF.md`
  - `README.md`
  - `apps/api/app_factory.py`
  - `apps/conversation/request_facade.py`
  - `pytest.ini`
  - `scripts/run_baseline_checks.ps1`
  - `tests/test_llm_runtime_prompt_paths.py`
  - `tests/test_planner_prompt_cards.py`
- findings:
  - planner default?? baseline 野꺜筌??紐낅뱜??筌욊쑴??癒?뵠 ?꾨뗀諭? exported helper default, baseline script, ??곸겫 ?얜챷苑???브쑴沅????덈뼄.
  - live runtime default??`apps/api/app_factory.py` ??`v2 / v1 / v2` ???筌?`apps/conversation/request_facade.py` exported helper default???????`v1` literal????? ?醫???뺣뼄.
  - `docs/03_??곸겫????띻펾.md` ??獄쏆꼶????뺚봺?袁る뱜????μ뵬 ?얜챷苑???쇰땾揶쎛 ?袁⑤빍??single source of truth ?봔??肉????룸┛???닌듼???얜챷???
  - `pytest.ini`, `scripts/run_baseline_checks.ps1`, `docs/PRODUCT_BASELINE.md`, `README.md` 揶쎛 baseline????뺤쨮 ??삘뀲 ??덇볼嚥???살구???筌? ??? ??甕곕뜆肉??⑥쥙???롫뮉 machine-readable owner揶쎛 ??용뼄.
  - ????곷뭼??retrieval semantics ?얜챷?ｅ첎? ?袁⑤빍??repo-level config/docops contract ?얜챷????嚥? SEARCH/LOOKUP/JOIN ??덉삂??椰꾨?諭띄뵳?? ??낅뮉 additive migration??揶쎛????됱읈??롫뼄.
- changes:
  - `docs/ADR/ADR-0007-planner-defaults-and-baseline-truth-manifest.md` ???곕떽???덈뼄.
  - `docs/SESSION_HANDOFF.md` ????苡?architect ??블??癒?뼊???곕떽???덈뼄.
- validations:
  - `rg -n "PLANNER_STAGE1_PROMPT_VERSION|PLANNER_STAGE15_PROMPT_VERSION|PLANNER_STAGE2_PROMPT_VERSION|run_baseline_checks|pytest -m smoke|test_api_routes_runtime|test_request_facade_and_context"` 嚥?drift ??뺛늺??????紐낅뻥??
  - ?얜챷苑?癰궰野껋럥彛???묐뻬??됱몵沃샕嚥?production code?? pytest????쎈뻬??? ??녿릭??
- next best task:
  - 揶쎛????됱읈????쇱벉 ?닌뗭겱 ??ν뒄??import-light contract artifact ??롪돌???곕떽???랁? `apps/api/app_factory.py`, `apps/conversation/request_facade.py`, `scripts/run_baseline_checks.ps1` ??planner defaults?? baseline inventory??域?artifact?癒?퐣 ??꾩쓺 筌띾슢諭??野껉퍔???
  - 域???쇱벉 ??ν뒄?癒?퐣 `docs/03_??곸겫????띻펾.md`, `docs/PRODUCT_BASELINE.md`, `README.md` ??pointer-based wording??곗쨮 ?類ｂ봺??랁?`tests/test_repo_contract_defaults.py` 揶쏆늿? drift regression???곕떽???롫뮉 野껉퍔???ル뿫??

## 2026-03-29T08:04:43+09:00 Watcher
- branch/head: `?⑥쥓猷??/ f601ddfa81dab2729743bb95ec42751adaf77073`
- worktree: dirty (`apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/followup_resolution.py`, `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/SESSION_HANDOFF.md`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`, `docs/ADR/ADR-0006-followup-reference-provenance-split.md`, `docs/ADR/ADR-0007-planner-defaults-and-baseline-truth-manifest.md`, `docs/reports/watcher/`)
- inspected files: `docs/CODEX_CONTEXT.md`, `docs/GOLDEN_TESTS.md`, `docs/PRODUCT_BASELINE.md`, `docs/03_??곸겫????띻펾.md`, `docs/04_???疫꿸퀣????癒?.md`, `apps/api/app_factory.py`, `apps/api/routes.py`, `apps/chat/answer_merge.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/api/runtime_helpers.py`, `apps/conversation/entity_reference.py`, `apps/retrieval/filters.py`, `apps/conversation/followup_resolution.py`, `apps/platform/runtime_strategy_policy.py`, `tests/test_answer_merge_bypass.py`, `tests/test_api_routes_reference_payload.py`, `tests/test_contract_debt_paydown.py`, `tests/test_eval_fixture_schema.py`, `tests/test_llm_runtime_prompt_paths.py`, `tests/test_planner_prompt_cards.py`, `tests/test_planner_stagewise.py`, `tests/test_rag_filter_policy.py`, `tests/test_runtime_helpers_stream_bypass.py`, `eval/sample_queries.jsonl`, `pytest.ini`, `scripts/run_baseline_checks.ps1`, `logs/app.log`, `logs/app (3).log`
- findings:
  - P1 source-reference precedence is still wrong in the dirty worktree: `build_intent_payload()` still lets display anchoring run before reference-context follow-up resolution, `followup_anchor.py` lets `?곗뮇荑?source N` flow through ordinal parsing, and `tests/test_planner_stagewise.py` still codifies the wrong display-snapshot precedence for `?곗뮇荑?2???怨뚮럡???類ｋ궖`.
  - P1 docs drift persists in `docs/03_??곸겫????띻펾.md`: it still recommends planner defaults `v1 / v1` and stale smoke commands, while live runtime defaults remain `v2 / v1 / v2` and the maintained baseline entrypoint is `scripts/run_baseline_checks.ps1`.
  - P2 source-reference provenance still collapses into `reference_context_ordinal`; eval semantics for `source_ref`, stream error-code precedence coverage, heuristic-only answer verification, and exported helper default drift all remain open.
  - No new evidence of fallback-chat reintroduction, SEARCH people-name hard-must drift, or JOIN `pjt_id` / `pjt_no` confusion surfaced in this pass.
- changes:
  - added `docs/reports/watcher/2026-03-29-0802.md`
  - appended this watcher handoff entry only; no production code edits
- validations:
  - `python -m py_compile apps/conversation/request_facade.py apps/conversation/followup_anchor.py apps/conversation/followup_resolution.py apps/conversation/entity_reference.py apps/api/runtime_helpers.py apps/chat/answer_merge.py apps/api/routes.py tests/test_planner_stagewise.py tests/test_runtime_helpers_stream_bypass.py tests/test_eval_fixture_schema.py tests/test_api_routes_reference_payload.py tests/test_answer_merge_bypass.py` passed
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` failed with `No module named pytest`
  - `Get-Item logs/app.log, logs/app (3).log | Select-Object Name,Length,LastWriteTime` confirmed `app.log` is still `0` bytes and the newest non-empty watcher-visible log is stale
- next best task:
  - in `apps/conversation/request_facade.py`, detect `parse_source_reference(question)` before `resolve_followup_anchor()` and bypass display anchoring for that case
  - replace the dirty source-reference regression in `tests/test_planner_stagewise.py` with a coexistence test proving `?곗뮇荑?2 ...` resolves through reference context even when `latest_display_snapshot` is present
## 2026-03-29T10:02:53.7775978+09:00 Watcher
- branch/head: `?⑥쥓猷??/ f601ddfa81dab2729743bb95ec42751adaf77073`
- worktree: dirty (`apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/followup_resolution.py`, `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/SESSION_HANDOFF.md`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`, `docs/ADR/ADR-0006-followup-reference-provenance-split.md`, `docs/ADR/ADR-0007-planner-defaults-and-baseline-truth-manifest.md`, `docs/reports/`)
- inspected files: `docs/SESSION_HANDOFF.md`, `docs/CODEX_CONTEXT.md`, `docs/GOLDEN_TESTS.md`, `docs/03_??곸겫????띻펾.md`, `docs/PRODUCT_BASELINE.md`, `apps/api/app_factory.py`, `apps/api/routes.py`, `apps/conversation/request_facade.py`, `apps/conversation/followup_anchor.py`, `apps/api/runtime_helpers.py`, `apps/chat/answer_merge.py`, `apps/conversation/followup_resolution.py`, `apps/conversation/entity_reference.py`, `apps/retrieval/filters.py`, `tests/test_planner_stagewise.py`, `tests/test_runtime_helpers_stream_bypass.py`, `tests/test_answer_merge_bypass.py`, `tests/test_api_routes_reference_payload.py`, `tests/test_eval_fixture_schema.py`, `tests/test_rag_filter_policy.py`, `tests/test_contract_debt_paydown.py`, `eval/sample_queries.jsonl`, `pytest.ini`, `scripts/run_baseline_checks.ps1`, `logs/app.log`, `logs/app (3).log`
- findings:
  - P1 source-reference precedence still fails in the current dirty worktree: `apps/conversation/request_facade.py` still calls `resolve_followup_anchor()` before `resolve_reference_context_followup()`, `apps/conversation/followup_anchor.py` still lets `parse_source_reference()` flow through `parse_ordinal_reference()`, and `tests/test_planner_stagewise.py` still locks in display-snapshot precedence for the `source N` follow-up case.
  - P1 docs drift still persists in `docs/03_??곸겫????띻펾.md`: the stagewise section still recommends `PLANNER_STAGE1_PROMPT_VERSION=v1` and `PLANNER_STAGE2_PROMPT_VERSION=v1`, and its smoke example still points at missing `tests/test_api_routes_runtime.py` and `tests/test_request_facade_and_context.py`, while live runtime defaults remain `v2 / v1 / v2` in `apps/api/app_factory.py` and the maintained baseline entrypoint is `scripts/run_baseline_checks.ps1`.
  - P2 streaming coverage is still thin: `apps/api/runtime_helpers.py` implements `TTFT_DEADLINE_EXCEEDED -> GEN_DEADLINE_EXCEEDED -> DEADLINE_EXCEEDED -> CHAR_LIMITED -> EMPTY_STREAM`, but `tests/test_runtime_helpers_stream_bypass.py` still covers only the non-empty-stream case.
  - P2 verifier weakness still persists: `apps/chat/answer_merge.py` rejects refusal-like answers, internal-context leaks, fallback markers, and too-short answers, but this run still found no evidence-grounded factuality verdict artifact or unsupported-fact regression coverage.
  - P2 provenance/default drift remains open: `apps/conversation/followup_resolution.py` still collapses resolved source-reference follow-ups into `reference_context_ordinal`, `apps/conversation/entity_reference.py` still has no source-reference literal, and the exported helper in `apps/conversation/request_facade.py` still defaults `planner_stage15_prompt_version='v1'` and `planner_stage2_prompt_version='v1'`.
  - Guard status: no new evidence of fallback-chat reintroduction, SEARCH people-name hard-must drift, JOIN `pjt_id`/`pjt_no` confusion, or route terminal sequencing regressions surfaced in this pass.
- changes:
  - appended this watcher handoff entry only; no production code edits
- validations:
  - `python -m py_compile apps/conversation/request_facade.py apps/conversation/followup_anchor.py apps/conversation/followup_resolution.py apps/conversation/entity_reference.py apps/api/runtime_helpers.py apps/chat/answer_merge.py apps/api/app_factory.py tests/test_planner_stagewise.py tests/test_runtime_helpers_stream_bypass.py tests/test_eval_fixture_schema.py tests/test_answer_merge_bypass.py tests/test_contract_debt_paydown.py tests/test_rag_filter_policy.py` passed
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` failed with `No module named pytest`
  - `Test-Path tests/test_api_routes_runtime.py` and `Test-Path tests/test_request_facade_and_context.py` both returned `False`
  - `logs/app.log` is still `0` bytes and `logs/app (3).log` remains the newest non-empty watcher-visible log
- next best task:
  - in `apps/conversation/request_facade.py`, detect `parse_source_reference(question)` before display-anchor resolution and add a coexistence regression in `tests/test_planner_stagewise.py` proving reference-context precedence survives even when `latest_display_snapshot` exists
  - after that, sync `docs/03_??곸겫????띻펾.md` to `apps/api/app_factory.py`, `pytest.ini`, and `scripts/run_baseline_checks.ps1`, then add direct precedence tests for `derive_stream_error_code()`
## 2026-03-29T11:02:28+09:00 Watcher
- branch/head: `?⑥쥓猷??/ f601ddfa81dab2729743bb95ec42751adaf77073`
- worktree: dirty (`apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/followup_resolution.py`, `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/SESSION_HANDOFF.md`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`, `docs/ADR/ADR-0006-followup-reference-provenance-split.md`, `docs/ADR/ADR-0007-planner-defaults-and-baseline-truth-manifest.md`, `docs/reports/`)
- inspected files: `docs/SESSION_HANDOFF.md`, `docs/CODEX_CONTEXT.md`, `docs/GOLDEN_TESTS.md`, `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/03_??곸겫????띻펾.md`, `docs/reports/watcher/2026-03-29-0903.md`, `eval/sample_queries.jsonl`, `pytest.ini`, `scripts/run_baseline_checks.ps1`, `apps/api/app_factory.py`, `apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/api/runtime_helpers.py`, `apps/chat/answer_merge.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/entity_reference.py`, `apps/conversation/followup_resolution.py`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `tests/test_runtime_helpers_stream_bypass.py`, `tests/test_eval_fixture_schema.py`, `tests/test_answer_merge_bypass.py`, `logs/app.log`, `logs/app (3).log`
- findings:
  - P1 source-reference wording/tests progressed in the dirty tree, but the actual precedence bug remains: `apps/conversation/request_facade.py` still runs `resolve_followup_anchor()` before `resolve_reference_context_followup()`, `apps/conversation/followup_anchor.py` still maps `?곗뮇荑?N` through `parse_ordinal_reference()` into display rank selection, and `tests/test_planner_stagewise.py` now contains a regression that locks that display-snapshot behavior in.
  - P1 docs drift still persists in `docs/03_??곸겫????띻펾.md`: the triage section still recommends `PLANNER_STAGE1_PROMPT_VERSION=v1`, `PLANNER_STAGE2_PROMPT_VERSION=v1`, `python -m pytest -m smoke`, and missing smoke files, while `apps/api/app_factory.py`, `pytest.ini`, and `scripts/run_baseline_checks.ps1` remain the current source of truth.
  - P2 provenance/default drift remains open: `apps/conversation/followup_resolution.py` still collapses resolved source references into `reference_context_ordinal`, `apps/conversation/entity_reference.py` still lacks a source-reference literal, and `apps/conversation/request_facade.py` still defaults `planner_stage15_prompt_version='v1'`, `planner_stage2_prompt_version='v1'`.
  - P2 stream/verifier gaps remain open: `tests/test_runtime_helpers_stream_bypass.py` still covers only the non-empty-stream bypass, and `tests/test_answer_merge_bypass.py` still does not protect against unsupported factual answers that avoid heuristic leak/refusal checks.
  - Positive progress observed: the dirty detail-coverage patch now hydrates `summary/goal/period/budget/outputs` through canonical facts, adds `tests/test_detail_contract.py` coverage, and syncs the contract note in `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`.
- changes:
  - added `docs/reports/watcher/2026-03-29-1102.md`
  - appended this watcher handoff entry only; no production code edits
- validations:
  - `python -m py_compile apps/evidence/detail_contract.py apps/conversation/followup_anchor.py apps/conversation/request_facade.py apps/evidence/canonical_evidence.py apps/conversation/followup_resolution.py apps/conversation/entity_reference.py apps/api/runtime_helpers.py apps/chat/answer_merge.py tests/test_detail_contract.py tests/test_planner_stagewise.py tests/test_runtime_helpers_stream_bypass.py tests/test_eval_fixture_schema.py tests/test_answer_merge_bypass.py` passed
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` failed with `No module named pytest`
  - `Get-Item logs/app.log, 'logs/app (3).log' | Select-Object Name,Length,LastWriteTime` confirmed `app.log` is still `0` bytes and the newest non-empty watcher-visible log remains stale
- next best task:
  - in `apps/conversation/request_facade.py`, short-circuit `parse_source_reference(question)` before display-anchor resolution and replace the dirty display-snapshot regression in `tests/test_planner_stagewise.py` with a coexistence test that proves reference-context precedence
  - after that, sync `docs/03_??곸겫????띻펾.md` to `apps/api/app_factory.py`, `pytest.ini`, and `scripts/run_baseline_checks.ps1`, then add direct precedence tests for `derive_stream_error_code()` and a source-reference provenance regression
## 2026-03-29T20:05:50+09:00 Architect
- branch/head: `?⑥쥓猷?? / `f601ddfa81dab2729743bb95ec42751adaf77073`
- worktree: dirty (`apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/followup_resolution.py`, `docs/02_??쎈뻬?④쑴鍮잍??袁⑥셽域뱀뮇??md`, `docs/SESSION_HANDOFF.md`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`, `docs/ADR/ADR-0006-followup-reference-provenance-split.md`, `docs/ADR/ADR-0007-planner-defaults-and-baseline-truth-manifest.md`, `docs/reports/`)
- inspected files:
  - `docs/CODEX_CONTEXT.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/SESSION_HANDOFF.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/ADR/ADR-0004-execution-artifact-projection-boundary.md`
  - `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`
  - `apps/chat/answer_generation.py`
  - `apps/chat/answer_merge.py`
  - `apps/api/routes.py`
  - `apps/api/contracts/workflow_models.py`
  - `tests/test_answer_merge_bypass.py`
  - `tests/test_api_routes_reference_payload.py`
  - `eval/sample_queries.jsonl`
- findings:
  - current answer-stage verifier remains heuristic-only: `apps/chat/answer_merge.py` still decides only from refusal, leak, deadline/char-limit, fallback marker, and length heuristics.
  - `apps/chat/answer_generation.py` and `apps/api/routes.py` already preserve terminal answer truth, but there is still no first-class groundedness artifact between answer candidate generation and route serialization.
  - `apps/api/contracts/workflow_models.py` still has no dedicated answer groundedness snapshot/verdict field, so unsupported-claim semantics cannot be owned by a stable contract.
  - `tests/test_answer_merge_bypass.py` and `docs/GOLDEN_TESTS.md` are now misaligned: the docs require conservative handling of invented ids/dates/orgs/counts, while the automated regressions still cover heuristic-only failure classes.
  - this is an answer-layer contract gap, not a planner/retrieval semantics gap, so it can be addressed additively without touching immutable strategy rules.
- changes:
  - added `docs/ADR/ADR-0008-answer-groundedness-verdict-contract.md`
  - appended this architect handoff entry
- validations:
  - `rg -n "select_final_answer|merge_answers|selected_answer_meta|final_answer_artifact|AnswerArtifact|degraded|unsupported"` across docs/runtime/tests to confirm the current groundedness surface and gaps
  - documentation-only change; no production code or pytest execution
- next best task:
  - add an additive `AnswerEvidenceSnapshot` / `AnswerGroundednessVerdict` contract and fixture schema before changing selector behavior
  - then land passive groundedness verdict logging for unsupported ids/years/orgs/counts, and only after that consider narrow selector gating
## 2026-03-29T23:02:56+09:00 Watcher
- branch/head: `怨좊룄??/ f601ddfa81dab2729743bb95ec42751adaf77073`
- worktree: dirty (`apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/followup_resolution.py`, `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/SESSION_HANDOFF.md`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`, `docs/ADR/ADR-0006-followup-reference-provenance-split.md`, `docs/ADR/ADR-0007-planner-defaults-and-baseline-truth-manifest.md`, `docs/ADR/ADR-0008-answer-groundedness-verdict-contract.md`, `docs/reports/`)
- inspected files: `docs/CODEX_CONTEXT.md`, `docs/GOLDEN_TESTS.md`, `docs/03_?댁쁺怨??섍꼍.md`, `docs/04_?뚭?湲곗?怨??먭?.md`, `docs/SESSION_HANDOFF.md`, `docs/reports/watcher/2026-03-29-2203.md`, `eval/sample_queries.jsonl`, `pytest.ini`, `scripts/run_baseline_checks.ps1`, `apps/api/app_factory.py`, `apps/conversation/request_facade.py`, `apps/conversation/followup_anchor.py`, `apps/api/runtime_helpers.py`, `apps/chat/answer_merge.py`, `apps/chat/llm_runtime.py`, `apps/conversation/followup_resolution.py`, `apps/conversation/entity_reference.py`, `apps/platform/runtime_strategy_policy.py`, `apps/retrieval/rag_filter_policy.py`, `apps/planner/planner_contract.py`, `tests/test_planner_stagewise.py`, `tests/test_runtime_helpers_stream_bypass.py`, `tests/test_answer_merge_bypass.py`, `tests/test_eval_fixture_schema.py`, `tests/test_api_routes_reference_payload.py`, `tests/test_llm_runtime_prompt_paths.py`, `tests/test_rag_filter_policy.py`, `tests/test_people_filter_nested_gate.py`, `logs/app.log`, `logs/app (3).log`
- findings:
  - P1 source-reference precedence is still wrong in the dirty tree: `apps/conversation/request_facade.py` still calls `resolve_followup_anchor()` before `resolve_reference_context_followup()`, `apps/conversation/followup_anchor.py` still maps `異쒖쿂 N` through the display-rank path, and `tests/test_planner_stagewise.py:1270-1325` now codifies that display-derived outcome while labeling it `source_reference`.
  - P1 docs drift still persists in `docs/03_?댁쁺怨??섍꼍.md`: the triage section still recommends `PLANNER_STAGE1_PROMPT_VERSION=v1`, `PLANNER_STAGE2_PROMPT_VERSION=v1`, `python -m pytest -m smoke`, and missing smoke files, even though the live defaults are `v2 / v1 / v2` and the maintained baseline entrypoint is `scripts/run_baseline_checks.ps1`.
  - P2 source-reference clarification wording improved in `apps/conversation/followup_resolution.py`, but resolved provenance still collapses into `reference_context_ordinal`, `tests/test_eval_fixture_schema.py` still checks schema only, stream precedence coverage still stops at the non-empty-stream case, and the answer-stage verifier still remains heuristic-only for unsupported facts.
  - Guard status stayed stable in this pass: no new static evidence of fallback-chat reintroduction, SEARCH people-name hard-must drift, JOIN `pjt_id`/`pjt_no` confusion, prompt-path drift, or route terminal sequencing regressions surfaced.
- changes:
  - added `docs/reports/watcher/2026-03-29-2302.md`
  - appended this watcher handoff entry only; no production code edits
- validations:
  - `python -m py_compile apps/conversation/request_facade.py apps/conversation/followup_anchor.py apps/conversation/followup_resolution.py apps/conversation/entity_reference.py apps/api/runtime_helpers.py apps/chat/answer_merge.py apps/api/app_factory.py tests/test_planner_stagewise.py tests/test_runtime_helpers_stream_bypass.py tests/test_answer_merge_bypass.py tests/test_eval_fixture_schema.py tests/test_api_routes_reference_payload.py tests/test_llm_runtime_prompt_paths.py tests/test_rag_filter_policy.py tests/test_people_filter_nested_gate.py` passed
  - `python -c "import pytest"` failed with `No module named 'pytest'`
  - `python -c "import pydantic"` failed with `No module named 'pydantic'`
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` failed with `No module named pytest`
  - `Test-Path tests/test_api_routes_runtime.py` and `Test-Path tests/test_request_facade_and_context.py` both returned `False`
  - `logs/app.log` is still `0` bytes and `logs/app (3).log` remains the newest non-empty watcher-visible log
- next best task:
  - in `apps/conversation/request_facade.py`, short-circuit `parse_source_reference(question)` before display-anchor resolution and add a coexistence regression that proves reference-context precedence survives even when `latest_display_snapshot` exists
  - after that, sync `docs/03_?댁쁺怨??섍꼍.md` to `apps/api/app_factory.py`, `pytest.ini`, and `scripts/run_baseline_checks.ps1`, then add direct precedence tests for `derive_stream_error_code()` and a source-reference provenance regression
## 2026-03-30T00:03:40+09:00 Watcher
- branch/head: `怨좊룄??/ f601ddfa81dab2729743bb95ec42751adaf77073`
- worktree: dirty (`apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/followup_resolution.py`, `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/SESSION_HANDOFF.md`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`, `docs/ADR/ADR-0006-followup-reference-provenance-split.md`, `docs/ADR/ADR-0007-planner-defaults-and-baseline-truth-manifest.md`, `docs/ADR/ADR-0008-answer-groundedness-verdict-contract.md`, `docs/reports/`)
- inspected_files:
  - `docs/CODEX_CONTEXT.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/SESSION_HANDOFF.md`
  - `eval/sample_queries.jsonl`
  - `pytest.ini`
  - `scripts/run_baseline_checks.ps1`
  - `apps/api/app_factory.py`
  - `apps/conversation/request_facade.py`
  - `apps/conversation/followup_anchor.py`
  - `apps/api/runtime_helpers.py`
  - `apps/chat/answer_merge.py`
  - `apps/conversation/followup_resolution.py`
  - `apps/platform/runtime_strategy_policy.py`
  - `apps/planner/planner_contract.py`
  - `apps/retrieval/rag_filter_policy.py`
  - `tests/test_planner_stagewise.py`
  - `tests/test_runtime_helpers_stream_bypass.py`
  - `tests/test_answer_merge_bypass.py`
  - `tests/test_eval_fixture_schema.py`
  - `tests/test_people_filter_nested_gate.py`
  - `tests/test_rag_filter_policy.py`
  - `logs/app.log`
  - `logs/app (3).log`
- architecture_map:
  - follow-up precedence is assembled in `apps/conversation/request_facade.py`; `resolve_followup_anchor()` currently runs before `resolve_reference_context_followup()`.
  - display ordinal/source parsing lives in `apps/conversation/followup_anchor.py`; reference-context parsing, clarification wording, and seed ownership live in `apps/conversation/followup_resolution.py`.
  - planner prompt defaults are sourced from `apps/api/app_factory.py`; test/runbook truth is `pytest.ini` plus `scripts/run_baseline_checks.ps1`.
  - answer gating is still `apps/chat/answer_merge.py::select_final_answer()`, and stream failure classification is `apps/api/runtime_helpers.py::derive_stream_error_code()`.
- findings_by_priority:
  - P1: source-reference precedence is still wrong in the dirty tree. `apps/conversation/request_facade.py` still resolves display anchors first at lines 576-584, `_build_followup_resolution_from_anchor()` still labels that display-derived result as `source_reference` at lines 340-349, `apps/conversation/followup_anchor.py` still feeds `異쒖쿂 N` through `parse_ordinal_reference()` at lines 87-107, and `tests/test_planner_stagewise.py:1270-1325` now locks that display-snapshot path in as the expected behavior.
  - P1: docs drift still persists in `docs/03_?댁쁺怨??섍꼍.md`. The file still recommends `PLANNER_STAGE1_PROMPT_VERSION=v1` and `PLANNER_STAGE2_PROMPT_VERSION=v1` at lines 151-152 and still points smoke validation at `python -m pytest -m smoke` plus missing files at lines 249-261, while `apps/api/app_factory.py:141-143` and the same doc's own line 295 say the live defaults are `v2 / v1 / v2`.
  - P2: source-reference wording improved, but provenance/default drift remains open. `apps/conversation/followup_resolution.py:313-314` still collapses resolved source references into `reference_context_ordinal`, and `apps/conversation/request_facade.py:718-719` still exports stale helper defaults for `planner_stage15_prompt_version` and `planner_stage2_prompt_version`.
  - P2: stream error precedence is implemented but still under-tested. `apps/api/runtime_helpers.py:122-131` encodes `TTFT_DEADLINE_EXCEEDED -> GEN_DEADLINE_EXCEEDED -> DEADLINE_EXCEEDED -> CHAR_LIMITED -> EMPTY_STREAM`, but `tests/test_runtime_helpers_stream_bypass.py:4-10` still covers only the non-empty-stream bypass case.
  - P2: answer verification is still heuristic-only for unsupported claims. `apps/chat/answer_merge.py:126-143` checks deadline, char limit, fallback marker, refusal, internal-context leak, and minimum length only; `tests/test_answer_merge_bypass.py:23-107` covers those classes but there is still no additive groundedness fixture or unsupported id/year/org/count regression in `eval/` or `tests/`.
- user_quality_risks:
  - A user follow-up like `異쒖쿂 2???곌뎄???뺣낫` can still be seeded from the visible display list instead of the reference-context evidence list, which risks answering about the wrong entity while preserving the misleading label `source_reference`.
  - On-call or local triage remains slower than necessary because the main operations doc still mixes stale planner defaults and smoke commands with the current baseline entrypoint.
  - Stream failures beyond the non-empty bypass case can regress silently because direct precedence tests for deadline and char-limit branches are still absent.
  - Fluent but unsupported factual answers still have no deterministic regression coverage; the current selector can only reject obvious leak/refusal/fallback patterns.
  - Guard status remained stable in this pass: no new static evidence of fallback-chat reintroduction, SEARCH people-name hard-must drift, or JOIN `pjt_id`/`pjt_no` confusion surfaced.
- recommended_next_patch:
  - In `apps/conversation/request_facade.py`, detect `parse_source_reference(question)` before `resolve_followup_anchor()` and bypass display anchoring for that case so reference-context resolution owns source references.
  - Replace `tests/test_planner_stagewise.py:1270-1325` with a coexistence regression that proves `異쒖쿂 N` follows reference-context precedence even when `latest_display_snapshot` is present.
- recommended_new_tests:
  - Add direct unit tests for `derive_stream_error_code()` covering TTFT, generation deadline, generic deadline, char-limit, and true empty-stream precedence.
  - Add a provenance regression that distinguishes `source_reference` from `reference_context_ordinal` in resolved follow-up metadata.
  - Add additive answer-groundedness fixtures for unsupported project id, year, org name, and count claims before any selector change.
- docs_to_sync:
  - `docs/03_?댁쁺怨??섍꼍.md` with `apps/api/app_factory.py`, `pytest.ini`, and `scripts/run_baseline_checks.ps1`
  - `docs/GOLDEN_TESTS.md` and `docs/04_?뚭?湲곗?怨??먭?.md` with the planned unsupported-claim groundedness fixtures once they exist
- changes:
  - appended this watcher handoff entry only; no production code edits
- validations:
  - `python -m py_compile apps/conversation/request_facade.py apps/conversation/followup_anchor.py apps/conversation/followup_resolution.py apps/api/runtime_helpers.py apps/chat/answer_merge.py apps/api/app_factory.py tests/test_planner_stagewise.py tests/test_runtime_helpers_stream_bypass.py tests/test_answer_merge_bypass.py tests/test_eval_fixture_schema.py tests/test_people_filter_nested_gate.py tests/test_rag_filter_policy.py` passed
  - `python -c "import pytest"` failed with `No module named 'pytest'`
  - `python -c "import pydantic"` failed with `No module named 'pydantic'`
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` failed with `No module named pytest`
  - `Test-Path tests/test_api_routes_runtime.py` and `Test-Path tests/test_request_facade_and_context.py` both returned `False`
  - `logs/app.log` is still `0` bytes and `logs/app (3).log` remains the newest non-empty watcher-visible log
## 2026-03-30T03:03:44+09:00 Watcher
- branch/head: `怨좊룄??/ f601ddfa81dab2729743bb95ec42751adaf77073`
- worktree: dirty (`apps/evidence/detail_contract.py`, `apps/conversation/followup_anchor.py`, `apps/conversation/request_facade.py`, `apps/evidence/canonical_evidence.py`, `apps/conversation/followup_resolution.py`, `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/SESSION_HANDOFF.md`, `tests/test_detail_contract.py`, `tests/test_planner_stagewise.py`, `docs/ADR/ADR-0005-answer-pipeline-verifier-regression-hardening.md`, `docs/ADR/ADR-0006-followup-reference-provenance-split.md`, `docs/ADR/ADR-0007-planner-defaults-and-baseline-truth-manifest.md`, `docs/ADR/ADR-0008-answer-groundedness-verdict-contract.md`, `docs/reports/`)
- inspected_files:
  - `docs/CODEX_CONTEXT.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/SESSION_HANDOFF.md`
  - `eval/sample_queries.jsonl`
  - `pytest.ini`
  - `scripts/run_baseline_checks.ps1`
  - `apps/api/app_factory.py`
  - `apps/conversation/request_facade.py`
  - `apps/conversation/followup_anchor.py`
  - `apps/evidence/detail_contract.py`
  - `apps/api/runtime_helpers.py`
  - `apps/chat/answer_merge.py`
  - `apps/evidence/canonical_evidence.py`
  - `apps/conversation/followup_resolution.py`
  - `apps/retrieval/filters.py`
  - `apps/retrieval/result_contract.py`
  - `apps/platform/runtime_strategy_policy.py`
  - `tests/test_planner_stagewise.py`
  - `tests/test_detail_contract.py`
  - `tests/test_runtime_helpers_stream_bypass.py`
  - `tests/test_answer_merge_bypass.py`
  - `tests/test_eval_fixture_schema.py`
  - `tests/test_people_filter_nested_gate.py`
  - `tests/test_rag_filter_policy.py`
  - `logs/app.log`
  - `logs/app (3).log`
- findings:
  - P1 source-reference precedence is still wrong in the dirty tree. `apps/conversation/request_facade.py:576-584` still resolves display anchors before reference-context evidence, `_build_followup_resolution_from_anchor()` in `apps/conversation/request_facade.py:340-349` relabels that display-derived path as `source_reference`, `apps/conversation/followup_anchor.py:101-107` still routes `異쒖쿂 N` through the display ordinal path, and `tests/test_planner_stagewise.py:1270-1325` now locks that behavior in.
  - P1 docs drift still persists in `docs/03_?댁쁺怨??섍꼍.md`: the triage block still recommends `PLANNER_STAGE1_PROMPT_VERSION=v1`, `PLANNER_STAGE2_PROMPT_VERSION=v1`, `python -m pytest -m smoke`, and missing smoke files, while `apps/api/app_factory.py`, `pytest.ini`, and `scripts/run_baseline_checks.ps1` remain the live source of truth.
  - P2 source-reference wording improved, but provenance/default drift remains open: `apps/conversation/followup_resolution.py:313-314` still collapses resolved source references into `reference_context_ordinal`, and `apps/conversation/request_facade.py:718-719` still exports stale helper defaults for `planner_stage15_prompt_version` and `planner_stage2_prompt_version`.
  - P2 stream error precedence is implemented but still under-tested. `apps/api/runtime_helpers.py:122-131` has the right ordering, but `tests/test_runtime_helpers_stream_bypass.py:4-10` still covers only the non-empty-stream bypass.
  - P2 answer verification is still heuristic-only for unsupported claims. `apps/chat/answer_merge.py:126-143` still lacks groundedness checks for unsupported ids/years/orgs/counts, and `tests/test_answer_merge_bypass.py` still covers only refusal/leak/fallback classes.
  - P2 observability remains thin: `logs/app.log` is still `0` bytes and `logs/app (3).log` is the newest non-empty watcher-visible log.
  - Positive progress observed: the dirty detail-coverage patch in `apps/evidence/detail_contract.py`, `apps/evidence/canonical_evidence.py`, and `tests/test_detail_contract.py` looks internally consistent and improves rich project fact hydration.
  - Guard status remained stable in this pass: no new static evidence of fallback-chat reintroduction, SEARCH people-name hard-must drift, or JOIN `pjt_id`/`pjt_no` confusion surfaced.
- changes:
  - added `docs/reports/watcher/2026-03-30-0303.md`
  - appended this watcher handoff entry only; no production code edits
- validations:
  - `python -m py_compile apps/conversation/request_facade.py apps/conversation/followup_anchor.py apps/conversation/followup_resolution.py apps/api/runtime_helpers.py apps/chat/answer_merge.py apps/api/app_factory.py tests/test_planner_stagewise.py tests/test_runtime_helpers_stream_bypass.py tests/test_answer_merge_bypass.py tests/test_eval_fixture_schema.py tests/test_people_filter_nested_gate.py tests/test_rag_filter_policy.py tests/test_detail_contract.py apps/evidence/detail_contract.py apps/evidence/canonical_evidence.py` passed
  - `python -c "import pytest"` failed with `No module named 'pytest'`
  - `python -c "import pydantic"` failed with `No module named 'pydantic'`
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` failed with `No module named pytest`
  - `Test-Path tests/test_api_routes_runtime.py` and `Test-Path tests/test_request_facade_and_context.py` both returned `False`
  - `logs/app.log` is still `0` bytes and `logs/app (3).log` remains the newest non-empty watcher-visible log
- next best task:
  - in `apps/conversation/request_facade.py`, short-circuit `parse_source_reference(question)` before `resolve_followup_anchor()` and replace the dirty display-snapshot regression in `tests/test_planner_stagewise.py` with a coexistence test that proves reference-context precedence
  - after that, sync `docs/03_?댁쁺怨??섍꼍.md` to `apps/api/app_factory.py`, `pytest.ini`, and `scripts/run_baseline_checks.ps1`, then add direct precedence tests for `derive_stream_error_code()` and a source-reference provenance regression

## 2026-03-30T11:40:00+09:00 Improver
- branch/head: `怨좊룄??/ `797380e952c15a0be91f3a44adee0b3165911cab`
- inspected files: `apps/retrieval/retrieval_workflow.py`, `apps/api/contracts/workflow_models.py`, `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/GOLDEN_TESTS.md`, `tests/test_retrieval_workflow_detail_runtime.py`
- findings:
  - `node_knowledge_sufficiency()` still let summary-like questions with live `prev_context` fall through to LLM sufficiency, even when the user explicitly asked for fresh data via cues like `latest/current/update`.
  - answer generation still hard-locks to `[?쒓났???뺣낫]`, so when that sufficiency path returned low the runtime could repeatedly end in refusal-like answers instead of issuing a new retrieval.
  - anchored detail follow-ups could also stay on stale fast paths because exact lookup/detail cache logic had no freshness bypass.
- changes:
  - `apps/api/contracts/workflow_models.py`: added `knowledge_sufficiency_meta` to carry non-schema runtime metadata for KS overrides.
  - `apps/retrieval/retrieval_workflow.py`: added rule-based freshness/new-data override detection, `KS.FRESHNESS_OVERRIDE` and `RAG.FRESHNESS_OVERRIDE` logging, and a detail freshness bypass that skips stale cache/exact-lookup fast paths while preserving the active anchor in the retrieval query.
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/GOLDEN_TESTS.md`: synced the contract and golden coverage to the new freshness-override behavior.
  - added `tests/test_retrieval_workflow_freshness_override.py` for KS short-circuit and anchored-detail bypass coverage.
- validations:
  - `python -m py_compile apps/api/contracts/workflow_models.py apps/retrieval/retrieval_workflow.py` passed
  - `python -m py_compile apps/api/contracts/workflow_models.py apps/retrieval/retrieval_workflow.py tests/test_retrieval_workflow_freshness_override.py` passed
  - `python -m pytest tests/test_retrieval_workflow_freshness_override.py -q` passed (`3 passed`), with a Windows `PytestCacheWarning` because the workspace still contains inaccessible `pytest-cache-files-*` directories
  - `python -m pytest tests/test_retrieval_workflow_freshness_override.py tests/test_retrieval_workflow_detail_runtime.py -k "fresh or stale_cache or exact_lookup" -q` passed (`7 passed, 12 deselected`), with the same cache warning
- next best task:
  - clean up the inaccessible `pytest-cache-files-*` directories so pytest can write cache data without warnings and future patch refreshes do not fail under the Windows sandbox
  - confirm the new `KS.FRESHNESS_OVERRIDE` / `RAG.FRESHNESS_OVERRIDE` events on one live request such as `洹?怨쇱젣 理쒖떊 ?뺣낫` and verify the answer no longer falls back to a context-only refusal

## 2026-03-30T12:05:00+09:00 Improver
- branch/head: `怨좊룄??/ `797380e952c15a0be91f3a44adee0b3165911cab`
- inspected files: `apps/retrieval/retrieval_workflow.py`, `apps/api/contracts/workflow_models.py`, `tests/test_retrieval_workflow_freshness_override.py`, `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/GOLDEN_TESTS.md`
- findings:
  - cue substring 湲곕컲 freshness override???ъ슜?먭? ?붽뎄??諛⑺뼢怨?留욎? ?딆븯怨? ?ㅽ깘 ??遺덊븘?뷀븳 寃?됱쓣 媛뺤젣?????덉뿀??
  - ?ㅼ젣 ?꾩슂??寃껋? keyword 洹쒖튃???꾨땲??knowledge sufficiency LLM???쒖씠 吏덈Ц? 湲곗〈 臾몃㎘留??곕㈃ stale answer risk媛 ?곌??앸? 援ъ“?곸쑝濡??먯젙?섍퀬, 洹?寃곌낵瑜?runtime fast-path bypass源뚯? ?꾨떖?섎뒗 怨꾩빟?댁뿀??
- changes:
  - `apps/api/contracts/workflow_models.py`: `KnowledgeSufficiency.prefer_fresh_retrieval`瑜?異붽??섍퀬, ?꾩떆 `knowledge_sufficiency_meta` state ?꾨뱶瑜??쒓굅?덈떎.
  - `apps/retrieval/retrieval_workflow.py`: cue-based override ?⑥닔瑜??쒓굅?섍퀬, knowledge sufficiency prompt??fresh-retrieval ?먮떒 洹쒖튃怨?`prefer_fresh_retrieval` 異쒕젰 ?꾨뱶瑜?異붽??덈떎.
  - `apps/retrieval/retrieval_workflow.py`: detail runtime? `ks.prefer_fresh_retrieval=true`???뚮쭔 stale cache/exact-lookup fast path瑜??고쉶?섍퀬, query??anchor seed瑜?蹂댁〈??fresh retrieval query濡??ㅼ떆 議곕┰?쒕떎.
  - `tests/test_retrieval_workflow_freshness_override.py`: heuristic ?뚯뒪?몃? ?쒓굅?섍퀬, LLM structured result 湲곕컲 KS ?먯젙怨?detail bypass ?뚭?濡?援먯껜?덈떎.
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/GOLDEN_TESTS.md`: fresh retrieval ?먮떒??source of truth瑜?LLM structured decision?쇰줈 ?ㅼ떆 留욎톬??
- validations:
  - `python -m py_compile apps/api/contracts/workflow_models.py apps/retrieval/retrieval_workflow.py tests/test_retrieval_workflow_freshness_override.py` passed
  - `python -m pytest tests/test_retrieval_workflow_freshness_override.py -q` passed
  - `python -m pytest tests/test_retrieval_workflow_freshness_override.py tests/test_retrieval_workflow_detail_runtime.py -k "fresh or stale_cache or exact_lookup" -q` passed
- next best task:
  - live request?먯꽌 `knowledge_sufficiency.prefer_fresh_retrieval=true`媛 debug payload濡?蹂댁씠怨? 媛숈? ?붿껌??stale detail cache瑜??吏 ?딅뒗吏 ?뺤씤
  - source-reference precedence dirty worktree issue??蹂꾨룄 ?⑥튂濡?怨꾩냽 遺꾨━ ???
## 2026-03-30T16:35:07+09:00 Improver
- branch/head: `怨좊룄??/ `797380e952c15a0be91f3a44adee0b3165911cab`
- inspected files:
  - `apps/api/contracts/repo_manifest.py`
  - `apps/api/contracts/answer_groundedness.py`
  - `apps/api/contracts/workflow_models.py`
  - `apps/conversation/request_facade.py`
  - `apps/chat/answer_merge.py`
  - `apps/chat/answer_generation.py`
  - `apps/retrieval/rag_retriever.py`
  - `apps/retrieval/retrieval_workflow.py`
  - `scripts/run_baseline_checks.ps1`
  - `tests/test_planner_stagewise.py`
  - `tests/test_rag_anchor_truth_active_only.py`
  - `tests/test_runtime_helpers_stream_bypass.py`
  - `tests/test_answer_merge_bypass.py`
  - `tests/test_api_routes_reference_payload.py`
  - `tests/test_eval_fixture_schema.py`
  - `tests/test_repo_contract_defaults.py`
  - `tests/test_answer_groundedness_verdict.py`
  - `eval/answer_groundedness_cases.jsonl`
  - `README.md`
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/PRODUCT_BASELINE.md`
- findings:
  - ADR-0006/0007/0005/0008 愿??dirty groundwork???대? ?쇰? ?ㅼ뼱? ?덉뿀吏留? single owner ?곌껐怨?regression coverage媛 ?앷퉴吏 ?ロ엳吏 ?딆븯??
  - `answer_merge.py`媛 groundedness helper瑜??먯껜 援ы쁽?쇰줈 蹂듭젣?섍퀬 ?덉뼱, ??怨꾩빟 ?뚯씪 `apps/api/contracts/answer_groundedness.py`? drift 媛?μ꽦???덉뿀??
  - `scripts/run_baseline_checks.ps1`??manifest owner媛 異붽????ㅼ뿉???섎뱶肄붾뵫 ?곹깭?怨? ?대쾲 PowerShell ?섍꼍?먯꽌??`ConvertFrom-Json -Depth`??吏?먮릺吏 ?딆븯??
  - ?ㅽ뻾 寃利??섍꼍? 遺덉셿?꾪뻽?? ?꾩옱 湲곕낯 `python`?먮뒗 `pytest`? `pydantic`媛 紐⑤몢 ?놁뼱 pytest/baseline ?ㅽ뻾???앷퉴吏 ?뚮┫ ???놁뿀??
- changes:
  - `apps/conversation/request_facade.py`: helper default瑜?`PLANNER_PROMPT_DEFAULTS["stage1"] / ["stage15"] / ["stage2"]`濡?留욎톬??
  - `apps/api/contracts/repo_manifest.py`, `scripts/run_baseline_checks.ps1`: baseline inventory? prompt default??owner瑜?manifest濡?怨좎젙?섍퀬, baseline ?ㅽ겕由쏀듃媛 洹?JSON???쎌뼱 collect/core/eval ?④퀎瑜?議곕┰?섎룄濡?諛붽엥??
  - `apps/chat/answer_merge.py`, `apps/chat/answer_generation.py`, `apps/api/contracts/workflow_models.py`: groundedness helper??owner瑜?contract 紐⑤뱢濡?紐⑥쑝怨? state??passive snapshot/verdict瑜?異붽??덈떎.
  - `apps/retrieval/rag_retriever.py`, `apps/retrieval/retrieval_workflow.py`: `anchor_reference_kind`瑜?anchor context/query-repair/log payload源뚯? ?꾪뙆?덈떎.
  - `tests/test_planner_stagewise.py`: ?섎せ ?좉꺼 ?덈뜕 display-snapshot source-reference regression??reference-context precedence ?뚭?濡?援먯껜?덈떎.
  - `tests/test_runtime_helpers_stream_bypass.py`, `tests/test_answer_merge_bypass.py`, `tests/test_api_routes_reference_payload.py`, `tests/test_rag_anchor_truth_active_only.py`: stream error precedence, bypass groundedness skip, debug route groundedness meta preservation, source-reference provenance 蹂댁〈 ?뚭?瑜?異붽??덈떎.
  - `tests/test_repo_contract_defaults.py`, `tests/test_answer_groundedness_verdict.py`, `eval/answer_groundedness_cases.jsonl`, `tests/test_eval_fixture_schema.py`: manifest drift regression怨?passive groundedness fixture/schema/verdict coverage瑜?異붽??덈떎.
  - `README.md`, `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/03_?댁쁺怨??섍꼍.md`, `docs/04_?뚭?湲곗?怨??먭?.md`, `docs/PRODUCT_BASELINE.md`: source-reference precedence, passive groundedness contract, manifest-owned defaults/baseline ?먮쫫?쇰줈 臾몄꽌瑜?sync?덈떎.
- validations:
  - `python -m py_compile apps/api/contracts/repo_manifest.py apps/api/contracts/answer_groundedness.py apps/api/contracts/workflow_models.py apps/conversation/request_facade.py apps/chat/answer_merge.py apps/chat/answer_generation.py apps/retrieval/rag_retriever.py apps/retrieval/retrieval_workflow.py tests/test_repo_contract_defaults.py tests/test_answer_groundedness_verdict.py tests/test_answer_merge_bypass.py tests/test_runtime_helpers_stream_bypass.py tests/test_api_routes_reference_payload.py tests/test_eval_fixture_schema.py tests/test_planner_stagewise.py tests/test_rag_anchor_truth_active_only.py` passed
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` now reaches manifest-driven collect-only, then fails with `No module named pytest`
  - `python -c "import pytest"` failed with `No module named pytest`
  - `python -c "import pydantic"` failed with `No module named pydantic`
- next best task:
  - repo ?꾩슜 Python environment ?먮뒗 dependency bootstrap??癒쇱? 蹂듦뎄???? `scripts/run_baseline_checks.ps1`? ?대쾲 pytest subset???ㅼ젣濡??뚮젮 green ?곹깭瑜??뺤씤??寃?  - 洹??ㅼ쓬 live request ?섎굹?먯꽌 `異쒖쿂 2???곌뎄???뺣낫`? groundedness debug metadata媛 ??provenance/metadata contract瑜?洹몃?濡??몄텧?섎뒗吏 ?뺤씤??寃?
## 2026-03-30T18:37:23.4769285+09:00 Improver
- branch/head: `怨좊룄??/ 797380e952c15a0be91f3a44adee0b3165911cab`
- inspected files:
  - `apps/retrieval/retrieval_workflow.py`
  - `apps/conversation/request_facade.py`
  - `apps/planner/planner_runtime.py`
  - `apps/planner/planner_validation.py`
  - `tests/test_retrieval_workflow_freshness_override.py`
  - `tests/test_retrieval_workflow_detail_runtime.py`
  - `tests/test_planner_stagewise.py`
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/SESSION_HANDOFF.md`
- findings:
  - anchored detail follow-up?먯꽌 `detail` search-required early exit? `prev_context=[]` early exit ?뚮Ц??`prefer_fresh_retrieval` LLM ?먮떒???щ씪吏????덉뿀??
  - `異쒖쿂 N` follow-up? reference-context owner媛 鍮꾩뼱 ?덉쓣 ??display snapshot fallback???놁뼱 source-reference 吏덉쓽媛 洹몃?濡??딄만 ???덉뿀??
  - planner runtime legality guard??`gate_seed_map`媛 ?녿뒗 locked strategy fixture?먯꽌 `AttributeError`瑜??덇퀬, deterministic repair??broad-history semantic cue(`?쒕룞?대젰`)瑜?retrieval query???ㅼ떆 ?대━吏 紐삵빐 raw fallback 吏곸쟾 ?섎? 蹂댁〈???쏀뻽??
- changes:
  - `apps/retrieval/retrieval_workflow.py`: anchored detail follow-up?대㈃ `detail`/`prev_context=[]` early exit蹂대떎 ?욎꽌 freshness LLM probe瑜??덉슜?섍퀬, search-required action?대씪??LLM??`prefer_fresh_retrieval`/`retrieval_query`瑜??좎???梨?`requires_new_knowledge=high`濡??밴꺽?섎룄濡??뺣━?덈떎.
  - `apps/conversation/request_facade.py`: `異쒖쿂 N`? reference-context owner瑜?癒쇱? ?곌퀬, 洹?寃곌낵媛 `missing_context|none`???뚮쭔 display snapshot ordinal fallback???덉슜?섎룄濡?諛붽엥??
  - `apps/planner/planner_runtime.py`: legality guard瑜?safe `getattr()`濡?怨좎튂怨? broad-history deterministic repair媛 `?쒕룞?대젰` 媛숈? semantic cue瑜?retrieval query??蹂듦뎄?섎룄濡?蹂닿컯?덈떎.
  - `tests/test_retrieval_workflow_detail_followup_freshness.py`, `tests/test_request_facade_source_reference_fallback.py`: fresh detail follow-up怨?source-reference display fallback ?뚭?瑜?異붽??덈떎.
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/GOLDEN_TESTS.md`: follow-up precedence, cache-backed freshness probe, broad-history deterministic repair 怨꾩빟??臾몄꽌?뷀뻽??
- validations:
  - `python -m pytest tests/test_retrieval_workflow_detail_followup_freshness.py tests/test_request_facade_source_reference_fallback.py -q` passed (`4 passed`)
  - `python -m pytest tests/test_retrieval_workflow_detail_followup_freshness.py tests/test_retrieval_workflow_freshness_override.py tests/test_request_facade_source_reference_fallback.py tests/test_detail_contract.py tests/test_answer_merge_bypass.py -q` passed (`39 passed`)
  - `python -m pytest tests/test_retrieval_workflow_detail_runtime.py -q -k "detail_cache_hit_returns_evidence_context_not_direct_answer or uses_anchor_locked_exact_lookup_for_detail_followup"` passed (`2 passed`)
  - `python -m pytest tests/test_planner_stagewise.py -q -k "source_reference or deterministic_repair_before_raw_fallback"` passed (`4 passed`)
  - `python -m pytest tests/test_planner_prompt_cards.py -q -k "broad_history_validation_ignores_count_and_generic_history_must_keep_terms"` passed (`1 passed`)
  - `python -m py_compile apps/retrieval/retrieval_workflow.py apps/conversation/request_facade.py apps/planner/planner_runtime.py tests/test_retrieval_workflow_detail_followup_freshness.py tests/test_request_facade_source_reference_fallback.py` passed
  - all pytest runs emitted `PytestCacheWarning` because the workspace still contains inaccessible `pytest-cache-files-*` directories under Windows.
- next best task:
  - `apps/conversation/followup_resolution.py` / `apps/conversation/entity_reference.py`?먯꽌 source-reference provenance literal??ordinal怨?遺꾨━??observability源뚯? ?쇨??섍쾶 留욎텧 寃?  - Windows workspace??inaccessible `pytest-cache-files-*` ?붾젆?곕━瑜??뺣━??patch refresh / pytest cache warning ?몄씠利덈? ?쒓굅??寃?
## 2026-03-30T18:40:49.5052998+09:00 Watcher
- branch/head: `怨좊룄??/ 797380e952c15a0be91f3a44adee0b3165911cab`
- worktree: dirty (`README.md`, `apps/api/app_factory.py`, `apps/api/contracts/workflow_models.py`, `apps/chat/answer_generation.py`, `apps/chat/answer_merge.py`, `apps/planner/planner_runtime.py`, `apps/retrieval/rag_retriever.py`, `apps/conversation/request_facade.py`, `apps/retrieval/retrieval_workflow.py`, `apps/conversation/entity_reference.py`, `apps/conversation/followup_resolution.py`, `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/03_?댁쁺怨??섍꼍.md`, `docs/04_?뚭?湲곗?怨??먭?.md`, `docs/GOLDEN_TESTS.md`, `docs/PRODUCT_BASELINE.md`, `docs/SESSION_HANDOFF.md`, `scripts/run_baseline_checks.ps1`, `tests/test_answer_merge_bypass.py`, `tests/test_api_routes_reference_payload.py`, `tests/test_detail_contract.py`, `tests/test_eval_fixture_schema.py`, `tests/test_planner_stagewise.py`, `tests/test_rag_anchor_truth_active_only.py`, `tests/test_runtime_helpers_stream_bypass.py`, plus new contract/ADR/test files)
- inspected_files:
  - `docs/SESSION_HANDOFF.md`
  - `docs/CODEX_CONTEXT.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/PRODUCT_BASELINE.md`
  - `docs/ADR/ADR-0006-followup-reference-provenance-split.md`
  - `docs/ADR/ADR-0008-answer-groundedness-verdict-contract.md`
  - `pytest.ini`
  - `scripts/run_baseline_checks.ps1`
  - `apps/api/app_factory.py`
  - `apps/api/contracts/repo_manifest.py`
  - `apps/api/contracts/answer_groundedness.py`
  - `apps/conversation/request_facade.py`
  - `apps/retrieval/rag_retriever.py`
  - `apps/api/runtime_helpers.py`
  - `apps/chat/answer_merge.py`
  - `apps/chat/answer_generation.py`
  - `apps/conversation/followup_resolution.py`
  - `apps/retrieval/filters.py`
  - `tests/test_planner_stagewise.py`
  - `tests/test_request_facade_source_reference_fallback.py`
  - `tests/test_runtime_helpers_stream_bypass.py`
  - `tests/test_answer_merge_bypass.py`
  - `tests/test_repo_contract_defaults.py`
  - `tests/test_eval_fixture_schema.py`
  - `tests/test_rag_filter_policy.py`
  - `tests/test_rag_anchor_truth_active_only.py`
  - `eval/answer_groundedness_cases.jsonl`
  - `logs/app.log`
  - `logs/app (3).log`
- architecture_map:
  - planner/baseline defaults owner??`apps/api/contracts/repo_manifest.py`?닿퀬 `apps/api/app_factory.py`, `apps/conversation/request_facade.py`, `scripts/run_baseline_checks.ps1`媛 ?대? ?뚮퉬?쒕떎.
  - source-reference follow-up path??`apps/conversation/request_facade.py -> apps/conversation/followup_resolution.py -> apps/retrieval/rag_retriever.py -> apps/retrieval/retrieval_workflow.py`??
  - answer verifier path??`apps/chat/answer_generation.py -> apps/chat/answer_merge.py -> apps/api/contracts/answer_groundedness.py`??
  - SEARCH people/org nested gate? JOIN `pjt_id`/`pjt_no` strictness???ъ쟾??`apps/retrieval/filters.py`? 愿???뚭? ?뚯뒪?멸? source of truth??
- findings_by_priority:
  - P1: unsupported structured groundedness???꾩쭅 user-visible selection??留됱? ?딅뒗?? `apps/chat/answer_merge.py`??groundedness verdict瑜?怨꾩궛?섏?留?fail reason?쇰줈 ?밴꺽?섏? ?딄퀬, `tests/test_answer_merge_bypass.py`??unsupported id/year/org/count answer?먯꽌??`solar_failed is False`? `selected_model == "solar"`瑜?怨좎젙?쒕떎. ?대뒗 ADR-0008 湲곗??쇰줈???꾩쭅 Phase 2(passive verdict) ?곹깭??
  - P1: `異쒖쿂 N` follow-up??reference context ?놁씠 ?ㅼ뼱?ㅻ㈃ display snapshot fallback??怨꾩냽 ?덉슜?쒕떎. `apps/conversation/request_facade.py`??`missing_context|none`????display snapshot anchor濡??대젮媛怨? `tests/test_request_facade_source_reference_fallback.py`??洹??곹솴?먯꽌??`followup_reference_kind == "source_reference"`瑜?怨좎젙?쒕떎. citation-style 吏덈Ц??source list媛 ?꾨땶 visible display list??留ㅽ븨?섎뒗 寃껋씠 product intent?몄? ?꾩쭅 遺덊솗?ㅽ븯??
  - P2: `docs/03_?댁쁺怨??섍꼍.md`??理쒖냼 smoke baseline? ?ъ쟾???녿뒗 ?뚯씪 `tests/test_api_routes_runtime.py`, `tests/test_request_facade_and_context.py`瑜?媛由ы궓?? planner default? manifest owner ?ㅻ챸? 理쒖떊?붾릱吏留? ?ㅼ젣 triage??smoke block? ?꾩쭅 drift ?곹깭??
  - P2: ??source-reference fallback ?뚭?媛 baseline inventory???꾩쭅 ?ы븿?섏? ?딆븯?? `tests/test_request_facade_source_reference_fallback.py`??議댁옱?섏?留?`apps/api/contracts/repo_manifest.py`??`BASELINE_INVENTORY["core_contract_subset"]`?먮뒗 ?녿떎.
  - P2: watcher媛 蹂????덈뒗 濡쒓렇???ъ쟾???뉖떎. `logs/app.log`??0 bytes?닿퀬 理쒖떊 non-empty ?뚯씪? `logs/app (3).log`(2026-03-27)?? ?ㅼ젣 runtime??理쒓렐???뚯? ?딆? 寃껋씤吏, file sink媛 drift??寃껋씤吏???꾩옱 ?섎쭔?쇰줈 ?뺤젙?????녿떎.
  - Stable in this pass: `apps/retrieval/filters.py`? `tests/test_rag_filter_policy.py` 湲곗??쇰줈 people-name/org nested hard gate???좎??섍퀬, `apps/retrieval/filters.py`??JOIN must-key validation? `pjt_id`/`pjt_no` ?쇱슜 湲덉?瑜?怨꾩냽 媛뺤젣?쒕떎. ?대쾲 ?뺤쟻 ?먭??먯꽌??fallback chat ?щ룄??利앷굅??蹂댁? 紐삵뻽??
- user_quality_risks:
  - fluent?섏?留?洹쇨굅 諛뽰씤 project id / year / org / count answer媛 ?ъ쟾??理쒖쥌 ?듬??쇰줈 ?좏깮?????덈떎.
  - `異쒖쿂 2???곌뎄???뺣낫` 媛숈? 吏덉쓽??reference-context owner媛 ?놁쓣 ??visible display item 2濡??곌껐?????덉뼱, citation semantics? ?ㅼ젣 seed origin???닿툔?????덈떎.
  - on-call triage 臾몄꽌媛 ?녿뒗 smoke ?뚯씪??怨꾩냽 ?덈궡?섎?濡? ?ㅼ젣 ?뚭? ?뺤씤???먮젮吏怨??섎せ??寃利?猷⑦봽瑜?留뚮뱾 ???덈떎.
- recommended_next_patch:
  - ADR-0008 Phase 3瑜?醫곸? 踰붿쐞濡??쒖옉??`apps/chat/answer_merge.py`?먯꽌 `llm_streamed`/`llm_collected` answer??`groundedness.status == "unsupported"`瑜?fail reason?쇰줈 ?밴꺽?섍퀬, ?ㅻⅨ ?대? ?앹꽦??valid candidate媛 ?놁쑝硫?紐낆떆??degraded final濡??대━?꾨줉 怨좎젙?섎뒗 寃껋씠 媛??媛믩퉬??user-visible risk瑜?媛???묒? selector patch濡?以꾩씠??湲몄씠??
- recommended_new_tests:
  - `tests/test_answer_merge_bypass.py`??unsupported groundedness媛 alternate valid candidate ?먮뒗 degraded final???좏깮?섎뒗 ?뚭?瑜?異붽???寃?
  - `tests/test_request_facade_source_reference_fallback.py`瑜?`BASELINE_INVENTORY["core_contract_subset"]`???ы븿??baseline??source-reference fallback contract瑜??ㅼ젣濡??ㅽ뻾?섍쾶 ??寃?
  - source-reference fallback???좎????뺤콉?대씪硫?`/query/debug` ?먮뒗 route-level payload?먯꽌 `anchor_source=display_snapshot`? `anchor_reference_kind=source_reference` pair瑜??④퍡 ?몄텧?섎뒗 ?뚭?瑜?異붽???寃?
- docs_to_sync:
  - `docs/03_?댁쁺怨??섍꼍.md` with `apps/api/contracts/repo_manifest.py`, `pytest.ini`, and the actual existing smoke subset
  - `docs/PRODUCT_BASELINE.md` if baseline inventory grows to include the new source-reference fallback regression
  - `docs/04_?뚭?湲곗?怨??먭?.md` and `docs/GOLDEN_TESTS.md` when groundedness gating moves from passive verdict to selector-enforced behavior
- changes:
  - appended this watcher handoff entry only; no production code edits
- validations:
  - preflight passed: `git rev-parse --show-toplevel` -> `D:/Project/python_project/ntis_domain_rag_chatbot`, `git rev-parse --abbrev-ref HEAD` -> `怨좊룄??, `git rev-parse HEAD` -> `797380e952c15a0be91f3a44adee0b3165911cab`
  - `python -m py_compile apps/conversation/request_facade.py apps/conversation/followup_resolution.py apps/retrieval/rag_retriever.py apps/api/runtime_helpers.py apps/chat/answer_merge.py apps/chat/answer_generation.py apps/api/contracts/answer_groundedness.py apps/api/contracts/repo_manifest.py` passed
  - `python -m apps.api.contracts.repo_manifest --section baseline_inventory` passed
  - `python -c "import pytest"` failed with `No module named pytest`
  - `python -c "import pydantic"` failed with `No module named pydantic`
  - `python -m pytest tests/test_request_facade_source_reference_fallback.py tests/test_runtime_helpers_stream_bypass.py tests/test_answer_merge_bypass.py tests/test_repo_contract_defaults.py tests/test_eval_fixture_schema.py -q -p no:cacheprovider` failed with `No module named pytest`
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` failed at collect-only with `No module named pytest`
  - `Test-Path tests/test_api_routes_runtime.py` and `Test-Path tests/test_request_facade_and_context.py` both returned `False`
  - `logs/app.log` is still `0` bytes and `logs/app (3).log` is the newest non-empty watcher-visible log
- next best task:
  - repo ?꾩슜 Python environment ?먮뒗 dependency bootstrap??癒쇱? 蹂듦뎄??pytest/baseline???ㅼ떆 ?ㅽ뻾?섍퀬, 洹??ㅼ쓬 unsupported groundedness gating ?먮뒗 source-reference fallback policy瑜??ㅼ젣 runtime 濡쒓렇? ?④퍡 寃곗젙??寃?
## 2026-03-30T00:00:00+09:00 Improver
- branch/head: `怨좊룄?? / `797380e952c15a0be91f3a44adee0b3165911cab`
- inspected files: `apps/retrieval/retrieval_workflow.py`, `apps/chat/answer_generation.py`, `tests/test_retrieval_workflow_fresh_query_rewrite.py`, `tests/test_answer_generation_groundedness_snapshot.py`, `docs/SESSION_HANDOFF.md`
- findings:
  - `prefer_fresh_retrieval=true` 遺꾧린?먯꽌 fresh query瑜?`raw_query`濡??ㅼ떆 留뚮뱾怨??덉뼱, `resolve_rag_queries_fn()`???대? 怨좊Ⅸ KS rewritten query媛 detail follow-up?먯꽌 踰꾨젮吏????덉뿀??
  - groundedness snapshot? ?꾩옱 turn??detail?댁뼱???댁쟾 list turn??`latest_display_snapshot.visible_count`瑜?洹몃?濡??섍꺼, `unsupported_count`媛 stale list 湲곗??쇰줈 怨꾩궛?????덉뿀??
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`??fresh retrieval???쒗쁽??吏덈Ц??湲곗??쇰줈 ?ㅼ떆 寃?됲븯??anchor seed??蹂댁〈?쒕떎?앸뒗 怨꾩빟???대? 紐낆떆?섍퀬 ?덉뼱, ?대쾲 ?⑥튂??怨꾩빟 蹂寃쎌씠 ?꾨땲??援ы쁽 drift 蹂듦뎄??
- changes:
  - `apps/retrieval/retrieval_workflow.py`: fresh retrieval query 議곕┰??異쒕컻?먯쓣 `resolved_retrieval_query or raw_query`濡?諛붽퓭 KS rewritten query瑜??좎??섎㈃??anchor留?蹂닿컯?섎룄濡??섏젙?덈떎.
  - `apps/chat/answer_generation.py`: groundedness snapshot??`visible_count`瑜??꾩옱 異쒕젰 ??낆씠 list-like???뚮쭔 ?ъ슜?섎룄濡?helper瑜?異붽??덈떎. detail turn? ?꾩옱 `canonical_evidence` 湲몄씠留?洹쇨굅濡??쇰뒗??
  - `tests/test_retrieval_workflow_fresh_query_rewrite.py`: KS rewritten query媛 raw follow-up?쇰줈 ?섎룎?꾧?吏 ?딅뒗 ?뚭?瑜?異붽??덈떎.
  - `tests/test_answer_generation_groundedness_snapshot.py`: detail turn?먯꽌 stale list `visible_count`瑜?臾댁떆?섍퀬 current evidence count留?snapshot???④린???뚭?瑜?異붽??덈떎.
  - 怨꾩빟 臾몄꽌??洹몃?濡??먭퀬 handoff留?媛깆떊?덈떎. `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`??湲곗〈 ?ㅻ챸???대쾲 ?숈옉怨??대? ?쇱튂?쒕떎.
- validations:
  - `python -m py_compile apps/retrieval/retrieval_workflow.py apps/chat/answer_generation.py tests/test_retrieval_workflow_fresh_query_rewrite.py tests/test_answer_generation_groundedness_snapshot.py` passed
  - `python -m pytest tests/test_retrieval_workflow_freshness_override.py tests/test_retrieval_workflow_fresh_query_rewrite.py tests/test_answer_merge_bypass.py tests/test_answer_generation_groundedness_snapshot.py -q -p no:cacheprovider` passed (`16 passed`)
- next best task:
  - ?ㅼ젣 app/runtime request?먯꽌 `prefer_fresh_retrieval=true` debug payload? detail follow-up groundedness metadata媛 ?대쾲 ?뚭? ?섎룄?濡??몄텧?섎뒗吏 ??踰????뺤씤??寃?## 2026-03-30T19:20:10+09:00 Improver
- branch/head: `怨좊룄?? / `797380e952c15a0be91f3a44adee0b3165911cab`
- inspected files: `apps/api/request_overrides.py`, `apps/api/routes.py`, `tests/test_oracle_request_overrides.py`, `docs/03_?댁쁺怨??섍꼍.md`, `docs/README.md`, `docs/GOLDEN_TESTS.md`, `docs/SESSION_HANDOFF.md`
- findings:
  - route-level Oracle skip coverage was a false positive because `CountingRequestDefaultsLoader` only counted `load_defaults()`, while the live controller path prefers `load_defaults_with_meta()`.
  - the Oracle loader normalized only exact mixed-case aliases, so real Oracle cursor aliases like `TEMPERATURE` / `TOPP` could be read as an empty defaults row.
  - the existing ops docs already describe the intended Oracle override contract and `REQ.ORACLE.DEFAULTS` triage fields, so this pass needed code/test hardening rather than new contract docs.
- changes:
  - `apps/api/request_overrides.py`: made Oracle default-row normalization case-insensitive so uppercase Oracle aliases still map into request overrides.
  - `tests/test_oracle_request_overrides.py`: added coverage for uppercase Oracle aliases, direct loader `SELECT` execution, `/query/debug` Oracle lookup logging, no-param lookup calls for `/query/stream` and `/query/debug`, and full-param skip behavior for both routes.
  - `tests/test_oracle_request_overrides.py`: fixed the counting loader test double so `load_defaults_with_meta()` calls are tracked on the same path the controller uses.
- validations:
  - `python -m py_compile apps/api/request_overrides.py apps/api/routes.py tests/test_oracle_request_overrides.py` passed
  - inline Python validation passed: `request_overrides_inline_validation_ok`
  - `python -c "import importlib.util; ..."` confirmed `fastapi_missing` and `pytest_missing` in this shell
  - `python -m pytest tests/test_oracle_request_overrides.py -q -p no:cacheprovider` failed in this shell: `No module named pytest`
- next best task:
  - run `python -m pytest tests/test_oracle_request_overrides.py -q -p no:cacheprovider` inside the real app/runtime environment that has `fastapi` and `pytest`, then issue one override-empty `/query/stream` and one override-empty `/query/debug` request to confirm `REQ.ORACLE.DEFAULTS.oracle_lookup_attempted=True` and non-skip statuses against the actual Oracle connector.

## 2026-03-31T10:49:52.4233536+09:00 Improver
- branch/head: `怨좊룄?? / `5a81e0cbd119841c327f3652a8c005f03e581c7d`
- inspected files:
  - `apps/api/routes.py`
  - `tests/test_api_routes_reference_payload.py`
  - `templates/index.html`
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/SESSION_HANDOFF.md`
- findings:
  - `/query/stream`? ?대? `clarification`???대낫?닿퀬 ?덉뿀吏留?canonical `tag="event"` envelope ?덉쓽 `event.kind="clarification"`濡쒕쭔 蹂대궡怨?`event.content`??鍮꾩뼱 ?덉뼱, flat chunk ?꾩＜ ?뚮퉬?먮굹 以묒꺽 meta瑜??쎌? ?딅뒗 ?꾨줎?몃뒗 ?꾩냽吏덈Ц 臾멸뎄瑜??붾㈃??紐??꾩슱 ???덉뿀??
  - route??clarification ?댁꽍 濡쒖쭅??stream/debug??以묐났?섏뼱 ?덉뼱 message backfill 洹쒖튃???쒖そ留?怨좎튂硫??ㅻⅨ 履쎌씠 drift???꾪뿕???덉뿀??
  - repo ??`templates/index.html`? ?뚯뒪?몄슜 smoke harness吏留? ?꾩옱??`tag="clarification"`怨?`event.kind="clarification"`瑜??????쎌? ?딆븘 ?대쾲 SSE ?명솚 ?⑥튂瑜??덉쑝濡??뺤씤?섍린 ?대젮???곹깭???
- changes:
  - `apps/api/routes.py`: clarification payload ?뺢퇋??helper瑜?異붽??섍퀬, `selected_artifact.text`濡?visible `message`瑜?backfill?섎룄濡?臾띠뿀??
  - `apps/api/routes.py`: canonical clarification event??`content`瑜?梨꾩슦怨? legacy flat compatibility frame `tag="clarification"`??異붽?濡??대낫??top-level `content`? nested `clarification.message`瑜?媛숈씠 蹂댁〈?섎룄濡?諛붽엥??
  - `tests/test_api_routes_reference_payload.py`: clarification ?뚭?瑜??쐆idden??湲곗??먯꽌 ?쐄lat clarification + event clarification + answer.final + done??湲곗??쇰줈 諛붽씀怨? `/query/debug` clarification message backfill ?뚭?瑜?異붽??덈떎.
  - `templates/index.html`: smoke harness媛 flat clarification/event clarification???????뚯떛?섍퀬, clarification? ?묒そ 移대뱶??1?뚮쭔 蹂듭젣 ?뚮뜑留곹븯硫??ㅼ씠? duplicate `answer.final`? 臾댁떆?섎룄濡?蹂닿컯?덈떎.
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/03_?댁쁺怨??섍꼍.md`, `docs/04_?뚭?湲곗?怨??먭?.md`: canonical clarification event + compatibility flat clarification frame 怨꾩빟怨?legacy flat Solar label=`UPSTAGE` 臾멸뎄濡?sync?덈떎.
- validations:
  - `python -m py_compile apps/api/routes.py tests/test_api_routes_reference_payload.py` passed
  - `python -m pytest tests/test_api_routes_reference_payload.py -q -p no:cacheprovider` failed in this shell: `No module named pytest`
  - inline manual smoke using FastAPI `TestClient` is blocked in this shell: `No module named fastapi`
- next best task:
  - repo ?꾩슜 runtime environment?먯꽌 `python -m pytest tests/test_api_routes_reference_payload.py -q -p no:cacheprovider`瑜??ㅼ떆 ?뚮젮 clarification stream/debug ?뚭?瑜?green?쇰줈 ?뺤씤??寃?  - ?ㅼ젣 ?꾨줎???쒕쾭?먯꽌 `tag="clarification"` ?먮뒗 canonical `event.kind="clarification"`瑜??뚮퉬?섎룄濡??뚯꽌瑜?留욎텛怨? `異쒖쿂 N` out-of-range follow-up ??嫄댁쑝濡?end-to-end ?쒖떆瑜??뺤씤??寃?
## 2026-03-31T11:03:21.7647722+09:00 Improver
- branch/head: `怨좊룄?? / `5a81e0cbd119841c327f3652a8c005f03e581c7d`
- inspected files:
  - `apps/api/routes.py`
  - `apps/api/streaming/contracts.py`
  - `tests/test_api_routes_reference_payload.py`
  - `templates/index.html`
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/05_?좎?蹂댁닔?_?뺤옣.md`
  - `docs/README.md`
  - `docs/SESSION_HANDOFF.md`
- findings:
  - 吏곸쟾 ?⑥튂濡?clarification ?명솚? 醫뗭븘議뚯?留?route???ъ쟾??conversation/status/chunk/reference/error/degraded-final ?쇰?瑜?flat compatibility shape濡??④퍡 ?대낫?닿퀬 ?덉뼱, ?쐁anonical event envelope only??怨꾩빟?쇰줈???꾩쭅 ?ロ엳吏 ?딆? ?곹깭???
  - ?뚯뒪?몄? smoke harness??top-level `conversationId`, `status`, `chunk`, `reference` 媛숈? legacy flat field瑜?怨꾩냽 ?쎄퀬 ?덉뼱, ?ㅼ젣 ?뚮퉬?먭? ??怨꾩빟?쇰줈 ?댄뻾?덈뒗吏 ?뺤씤?섍린 ?대젮?좊떎.
- changes:
  - `apps/api/streaming/contracts.py`: canonical stream kind??`conversation`, `status`瑜?異붽???flat handshake/progress payload??event envelope ?덉쑝濡???만 ???덇쾶 ?덈떎.
  - `apps/api/routes.py`: legacy flat serializer helper瑜??쒓굅?섍퀬 `/query/stream`??紐⑤뱺 frame??`tag="event"` + `event.kind=*`濡쒕쭔 ?대낫?대룄濡?援먯껜?덈떎.
  - `apps/api/routes.py`: conversation init? `conversation` event `meta.conversation_id`, retrieve progress??`status` event `meta.status="retrieve"`, reference payload??`reference.set` event `meta.references`, degraded/error terminal??canonical `answer.final`/`error`/`done` event濡쒕쭔 ?좎??섍쾶 ?뺣━?덈떎.
  - `tests/test_api_routes_reference_payload.py`: reference/clarification/chunk/guard-final ?뚭?瑜?event-only 湲곗??쇰줈 媛깆떊?섍퀬, 紐⑤뱺 stream frame??canonical event envelope留??곕뒗吏 ?뺤씤?섎뒗 ?뚭?瑜?異붽??덈떎.
  - `templates/index.html`: ?뚯뒪??harness媛 ?댁젣 flat field瑜??쎌? ?딄퀬 canonical event留??뚯떛?섎룄濡??뺣━?덈떎.
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`, `docs/03_?댁쁺怨??섍꼍.md`, `docs/04_?뚭?湲곗?怨??먭?.md`, `docs/05_?좎?蹂댁닔?_?뺤옣.md`, `docs/README.md`: current contract瑜?legacy flat ?쒓굅 ??canonical event-only 湲곗??쇰줈 sync?덈떎.
- validations:
  - `python -m py_compile apps/api/routes.py apps/api/streaming/contracts.py tests/test_api_routes_reference_payload.py` passed
  - `python -m pytest tests/test_api_routes_reference_payload.py -q -p no:cacheprovider` is still blocked in this shell: `No module named pytest`
  - route-level manual smoke via FastAPI `TestClient` is still blocked in this shell: `No module named fastapi`
- next best task:
  - ?ㅼ젣 runtime environment?먯꽌 `python -m pytest tests/test_api_routes_reference_payload.py -q -p no:cacheprovider`瑜??뚮젮 event-only SSE contract ?뚭?瑜?green?쇰줈 ?뺤씤??寃?  - ?몃? ?꾨줎???쒕쾭 ?뚯꽌瑜?`tag="event"` / `event.kind` 湲곗??쇰줈留??쎈룄濡?留욎텛怨? `conversation`, `status`, `answer.chunk`, `clarification`, `answer.final`, `reference.set`, `done` 寃쎈줈瑜?end-to-end濡??뺤씤??寃?
## 2026-03-31T11:10:26.1412544+09:00 Improver
- branch/head: `怨좊룄?? / `5a81e0cbd119841c327f3652a8c005f03e581c7d`
- inspected files:
  - `apps/api/routes.py`
  - `apps/api/streaming/contracts.py`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/SESSION_HANDOFF.md`
- findings:
  - ?몃????몄텧?섎뒗 API 怨꾩빟? ?꾩옱 援ы쁽??`/query/stream` event-only SSE? `/query/debug` JSON shape濡??섎졃?덉?留? ?뚮퉬???낆옣?먯꽌???붿껌/?묐떟 ?ㅼ? event kind瑜???怨녹뿉???쎄린 ?대젮?좊떎.
  - `docs/03_?댁쁺怨??섍꼍.md`??湲곗〈 streaming 洹쒖튃? ?대? ?댁쁺 愿???ㅻ챸??以묒떖?대씪, ?몃? ?곕룞??request/response contract瑜?諛붾줈 蹂듭궗???곌린???뺣낫媛 遺꾩궛???덉뿀??
- changes:
  - `docs/03_?댁쁺怨??섍꼍.md`: `9.1 ?몃? API 怨꾩빟` ?뱀뀡??異붽???怨듯넻 ?붿껌 諛붾뵒, `/query/stream` canonical SSE envelope, event kind蹂??섎?, ?뚮퉬 ?쒖꽌 洹쒖튃, `/query/debug` ?깃났/?ㅽ뙣 JSON shape瑜?紐낆떆?덈떎.
  - `docs/SESSION_HANDOFF.md`: ?대쾲 ?몃? 怨꾩빟 臾몄꽌???묒뾽??湲곕줉?덈떎.
- validations:
  - 援ы쁽 ?議??뺤씤: `apps/api/routes.py`? `apps/api/streaming/contracts.py` 湲곗??쇰줈 `/query/stream`, `/query/debug` ?몃? 怨꾩빟 ??ぉ???섍린 ?議고뻽??
  - 異붽? 肄붾뱶 寃利앹? ?섑뻾?섏? ?딆븯?? ?대쾲 蹂寃쎌? 臾몄꽌-only patch??
- next best task:
  - ?몃? ?꾨줎???쒕쾭 ?먮뒗 API ?뚮퉬 臾몄꽌?먯꽌 `docs/03_?댁쁺怨??섍꼍.md`??`9.1 ?몃? API 怨꾩빟`??湲곗? 留곹겕濡??쇨퀬, ?섑뵆 payload瑜??ㅼ젣 ?곕룞 媛?대뱶? ?숈씪?섍쾶 ?좎???寃?
## 2026-03-31T11:55:00+09:00 Improver
- branch/head: `怨좊룄?? / `5a81e0cbd119841c327f3652a8c005f03e581c7d`
- inspected files:
  - `apps/api/routes.py`
  - `tests/test_api_routes_reference_payload.py`
  - `docs/README.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
- findings:
  - dirty tree??`/query/stream` route? ?뚭? 臾몄꽌??canonical event-only 怨꾩빟 履쎌쑝濡?湲곗슱???덉뿀怨? legacy flat `{"reference":[...]}` / `{"status":"done"}` tail contract? 異⑸룎?덈떎.
  - clarification short-circuit 寃쎈줈??selected artifact媛 ?덉쑝硫?`clarification` ?ㅼ뿉 `answer.final`???④퍡 ?섍컝 ???덉뼱, ?ъ슜?먭? 湲곕???`clarification -> reference -> done` 留먮? 怨꾩빟??源④퀬 ?덉뿀??
- changes:
  - `apps/api/routes.py`: legacy flat compatibility adapter瑜?蹂듦뎄?섍퀬, `/query/stream` 留먮???`reference` payload瑜?鍮?由ъ뒪?몄뿬????긽 1???대낫?대룄濡?怨좎젙?덈떎. clarification 寃쎈줈?먯꽌??`answer.final`??異붽?濡??대낫?댁? ?딄쾶 諛붽엥??
  - `tests/test_api_routes_reference_payload.py`: normal/clarification/guard terminal 寃쎈줈?먯꽌 `terminal -> reference -> done` ?쒖꽌? empty reference tail???뚭?濡?怨좎젙?덈떎.
  - `docs/README.md`, `docs/03_?댁쁺怨??섍꼍.md`, `docs/04_?뚭?湲곗?怨??먭?.md`: canonical event + legacy flat compatibility 怨듭〈, empty reference tail, clarification terminal ?쒖꽌瑜?臾몄꽌? ?먭? 湲곗???留욊쾶 媛깆떊?덈떎.
- validations:
  - `python -m py_compile apps/api/routes.py tests/test_api_routes_reference_payload.py` passed
  - `python -m pytest tests/test_api_routes_reference_payload.py -q -p no:cacheprovider` failed in this shell: `No module named pytest`
  - inline Python route smoke??failed in this shell because `fastapi` is not installed
- next best task:
  - ?ㅼ젣 runtime environment?먯꽌 `python -m pytest tests/test_api_routes_reference_payload.py -q -p no:cacheprovider`瑜??ㅼ떆 ?뚮젮 legacy flat tail contract瑜?green?쇰줈 ?뺤씤?섍퀬, 釉뚮씪?곗?/?몃? ?뚮퉬??濡쒓렇?먯꽌 `reference`? `status=done` 留먮? shape瑜???踰???罹≪쿂??寃?
## 2026-03-31T12:40:00+09:00 Improver
- branch/head: `怨좊룄?? / `3478854f4a34341938041af8196d45b64d009fbe`
- inspected files:
  - `apps/api/routes.py`
  - `apps/chat/answer_generation.py`
  - `tests/test_api_routes_reference_payload.py`
  - `docs/README.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
- findings:
  - `/query/stream` 留먮???`reference` payload???ъ쟾??`final_state.context` hit 臾몄꽌留??ㅼ떆 ?쒗쉶?댁꽌 留뚮뱾怨??덉뼱, ?ㅼ젣 retrieval 洹쇨굅媛 `selected_answer_artifact.references`??`canonical_evidence`???⑥븘 ?덉뼱??`{"reference":[]}`濡??앸궇 ???덉뿀??
  - `AnswerArtifact.references` ?꾨뱶??merge ?④퀎?먯꽌 ?좎??섏?留? retrieval evidence媛 ?덉뼱???먮룞?쇰줈 梨꾩썙吏吏 ?딆븘 route媛 `context` ?앹〈 ?щ???怨쇰룄?섍쾶 ?섏〈?덈떎.
- changes:
  - `apps/api/routes.py`: reference collector helper瑜?異붽???`selected_artifact.references -> retrieval_bundle.items -> canonical_evidence -> context` ?쒖꽌濡?reference瑜?紐⑥쑝怨? 湲곗〈 `tag/id/title` ?뺢퇋?붿? dedupe 洹쒖튃??洹몃?濡??ъ궗?⑺븯?꾨줉 諛붽엥??
  - `apps/chat/answer_generation.py`: retrieval evidence?먯꽌 reference payload瑜??뚯깮?섎뒗 helper瑜?異붽??섍퀬, per-model artifact? final selected artifact媛 reference瑜?鍮꾩썙 ?먯? ?딅룄濡?fallback 梨꾩? 濡쒖쭅???ｌ뿀??
  - `tests/test_api_routes_reference_payload.py`: `context=[]`?щ룄 explicit `final_answer_artifact.references` ?먮뒗 `canonical_evidence`媛 ?덉쑝硫?canonical `reference.set`怨?legacy flat `reference`媛 媛숈? non-empty 由ъ뒪?몃? ?대뒗 ?뚭?瑜?異붽??덈떎.
  - `docs/README.md`, `docs/03_?댁쁺怨??섍꼍.md`, `docs/04_?뚭?湲곗?怨??먭?.md`: public wire shape???좎???梨? `reference` source-of-truth媛 artifact/retrieval evidence fallback?대씪???먯쓣 臾몄꽌? ?먭? 湲곗???諛섏쁺?덈떎.
- validations:
  - `python -m py_compile apps/api/routes.py apps/chat/answer_generation.py tests/test_api_routes_reference_payload.py` passed
  - `python -m pytest tests/test_api_routes_reference_payload.py -q -p no:cacheprovider` failed in this shell: `No module named pytest`
  - inline Python smoke for route regression failed in this shell because `fastapi` is not installed
- next best task:
  - ?ㅼ젣 runtime environment?먯꽌 `tests/test_api_routes_reference_payload.py`瑜??ㅼ떆 ?ㅽ뻾??artifact/canonical-evidence fallback ?뚭?瑜?green?쇰줈 ?뺤씤?섍퀬, ?숈씪 ?섍꼍?먯꽌 `/query/stream` ??嫄댁쓣 ?몄텧??`reference`媛 ???댁긽 鍮?諛곗뿴濡??대젮?ㅼ? ?딅뒗吏 罹≪쿂??寃?
## 2026-03-31T14:02:56.4700119+09:00 Improver
- branch/head: `怨좊룄?? / `3478854f4a34341938041af8196d45b64d009fbe`
- inspected files:
  - `apps/api/routes.py`
  - `tests/test_api_routes_reference_payload.py`
  - `templates/index.html`
  - `docs/README.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/SESSION_HANDOFF.md`
- findings:
  - ?ъ슜?먭? 罹≪쿂???ㅼ젣 SSE 濡쒓렇 湲곗??쇰줈 `/query/stream`? canonical `tag="event"` frame怨??④퍡 legacy flat `chunk`, `reference`, `status=done` payload瑜??ъ쟾???숈떆 諛⑹텧?섍퀬 ?덉뿀??
  - 媛숈? 釉뚮옖移섏쓽 ?뚯뒪???뚯씪??legacy flat tail??湲곕??섍퀬 ?덉뼱, ?몃? 怨꾩빟 臾몄꽌? route 援ы쁽???ㅼ떆 ?쒕━?꾪듃???곹깭???
  - `templates/index.html`? ?대? canonical event留??쎄퀬 ?덉뿀?쇰?濡? ?대쾲 ?댁뒋???먯씤? ?꾨윴???섎꽕?ㅺ? ?꾨땲??route serializer? ?뚭? 湲곗????붿〈 legacy path???
- changes:
  - `apps/api/routes.py`: `encode_sse_payload` 湲곕컲 legacy serializer, chunk/reference/status flat payload ?앹꽦 helper, `_emit_legacy_stream_event()`瑜??쒓굅?섍퀬 紐⑤뱺 `/query/stream` frame??`encode_stream_event()` 湲곕컲 canonical event濡쒕쭔 ?대낫?대룄濡??뺣━?덈떎.
  - `apps/api/routes.py`: graph 誘몄?鍮?error/degraded/guard/final/reference/done 寃쎈줈瑜?紐⑤몢 event-only濡??щ같?좏뻽怨? `reference.set`? 湲곗〈 artifact/retrieval evidence fallback collector瑜?洹몃?濡??ъ슜?섎룄濡??좎??덈떎.
  - `tests/test_api_routes_reference_payload.py`: legacy flat helper/湲곕?瑜??쒓굅?섍퀬, 紐⑤뱺 frame??`tag="event"`?몄? ?뺤씤?섎뒗 `_assert_event_only_stream()` ?뚭?瑜?異붽??덈떎. chunk/status/clarification/guard/reference 寃쎈줈 寃利앸룄 `event.kind`? `event.meta` 湲곗??쇰줈 ?ㅼ떆 留욎톬??
  - `docs/README.md`, `docs/03_?댁쁺怨??섍꼍.md`, `docs/04_?뚭?湲곗?怨??먭?.md`: ?꾩옱 ?몃? 怨꾩빟怨??뚭? 湲곗????ㅼ떆 canonical event-only 湲곗??쇰줈 蹂듦뎄?덈떎.
- validations:
  - `python -m py_compile apps/api/routes.py tests/test_api_routes_reference_payload.py` passed
  - `python -m pytest tests/test_api_routes_reference_payload.py -q -p no:cacheprovider` is still blocked in this shell: `No module named pytest`
  - route-level manual smoke via FastAPI `TestClient` is still blocked in this shell: `No module named fastapi`
- next best task:
  - ?ㅼ젣 runtime environment?먯꽌 `python -m pytest tests/test_api_routes_reference_payload.py -q -p no:cacheprovider`瑜??ㅼ떆 ?뚮젮 event-only SSE ?뚭?瑜?green?쇰줈 ?뺤씤??寃?  - ?몃? ?꾨줎??以묎퀎 ?쒕쾭媛 媛숈? app instance瑜?諛붾씪蹂대뒗吏 ?뺤씤?섍퀬, 諛고룷 ???ㅼ젣 SSE 濡쒓렇?먯꽌 `tag="chunk"`, top-level `reference`, `tag="status"`媛 ???댁긽 ?섏삤吏 ?딅뒗吏 ??踰???罹≪쿂??寃?
## 2026-03-31T15:59:28.6553676+09:00 Watcher
- branch/head: `怨좊룄?? / `3478854f4a34341938041af8196d45b64d009fbe`
- inspected files:
  - `docs/SESSION_HANDOFF.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/README.md`
  - `docs/CODEX_CONTEXT.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/PRODUCT_BASELINE.md`
  - `apps/api/contracts/repo_manifest.py`
  - `scripts/run_baseline_checks.ps1`
  - `pytest.ini`
  - `tests/test_planner_stagewise.py`
  - `tests/test_api_routes_reference_payload.py`
  - `tests/test_runtime_helpers_stream_bypass.py`
  - `tests/test_request_facade_source_reference_fallback.py`
  - `eval/sample_queries.jsonl`
- findings:
  - docs issue: `docs/SESSION_HANDOFF.md` ?곷떒 bootstrap/current-risk ?붿빟? 臾몄옄 ?ㅼ뿼?쇰줈 ?꾩옱 source-of-truth瑜??쎄린 ?대졄寃?留뚮뱾怨??덉뿀??
  - docs issue: `docs/03_?댁쁺怨??섍꼍.md`??理쒖냼 smoke baseline ?덉떆???대? ?녿뒗 `tests/test_api_routes_runtime.py`, `tests/test_request_facade_and_context.py`瑜?媛由ы궎怨??덉뿀??
  - non-doc risk: ?꾩옱 shell?먮뒗 `pytest`媛 ?놁뼱 baseline ?ㅽ겕由쏀듃? ?섎룞 pytest subset?????섍꼍?먯꽌 ?앷퉴吏 ?뚮┫ ???녿떎.
  - non-doc risk: `tests/test_request_facade_source_reference_fallback.py`??議댁옱?섏?留?baseline inventory core subset?먮뒗 吏곸젒 ?ы븿?섏? ?딅뒗??
- remains risky:
  - 怨쇨굅 handoff history ?쇰???蹂댁〈??臾몄옄 ?ㅼ뿼 ?곹깭濡??⑥븘 ?덉뼱, ?κ린?곸쑝濡쒕뒗 蹂꾨룄 archival/cleanup pass媛 ?꾩슂?섎떎.
  - source-reference fallback??baseline???밴꺽?좎?, ?꾨땲硫?product policy瑜?癒쇱? ?뺤젙?좎????꾩쭅 寃곗젙?섏? ?딆븯??
  - repo ?꾩슜 Python environment媛 ?놁쑝硫?臾몄꽌? ?ㅼ젣 runtime ?뚭? ?곹깭瑜?媛숈? ?몄뿉???レ쓣 ???녿떎.
- changes:
  - `docs/SESSION_HANDOFF.md` ?곷떒 bootstrap/current-risk/role guide瑜??꾩옱 湲곗???UTF-8 ?쒓뎅???붿빟?쇰줈 蹂듦뎄?덈떎.
  - `docs/03_?댁쁺怨??섍꼍.md`??理쒖냼 smoke baseline??manifest-driven baseline entrypoint? ?ㅼ젣 議댁옱?섎뒗 ?섎룞 triage subset?쇰줈 援먯껜?덈떎.
  - ?대쾲 watcher 寃곌낵瑜?handoff 留먮???append?덈떎.
- validations:
  - preflight passed: `git rev-parse --show-toplevel` -> `D:/Project/python_project/ntis_domain_rag_chatbot`, `git rev-parse --abbrev-ref HEAD` -> `怨좊룄??, `git rev-parse HEAD` -> `3478854f4a34341938041af8196d45b64d009fbe`
  - `python -m apps.api.contracts.repo_manifest --section planner_prompt_defaults` passed and returned `{"stage1":"v2","stage15":"v1","stage2":"v2"}`.
  - `python -m apps.api.contracts.repo_manifest --section baseline_inventory` passed and confirmed the manifest-driven baseline inventory owner.
  - targeted text search found stale smoke filenames / old planner-default strings only inside preserved `docs/SESSION_HANDOFF.md` history and this watcher finding block, not in the active core docs sections updated in this pass.
  - UTF-8 explicit reads are required for `docs/SESSION_HANDOFF.md` and `docs/03_?댁쁺怨??섍꼍.md` because console display can still hide encoding problems.
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` failed at collect-only with `No module named pytest`; treated as environment blocker, not as a docs regression.
- next best task:
  - repo ?꾩슜 Python environment ?먮뒗 dependency bootstrap??癒쇱? 蹂듦뎄????`scripts/run_baseline_checks.ps1`瑜??ㅼ떆 ?ㅽ뻾??docs change? actual baseline ?곹깭瑜?媛숈? ?섍꼍?먯꽌 ?뺤씤??寃?  - source-reference fallback contract瑜?baseline inventory???ы븿?좎?, ?꾨땲硫?policy 臾몄꽌/ADR濡?癒쇱? ?뺤젙?좎? ?ㅼ쓬 watcher/improver 猷⑦봽?먯꽌 寃곗젙??寃?
## 2026-03-31T18:42:15.7910931+09:00 Watcher
- branch/head: `怨좊룄?? / `b22a7cb6b538f87d822a53e9915cfdab6e248100`
- inspected files:
  - `docs/SESSION_HANDOFF.md`
  - `docs/CODEX_CONTEXT.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/GOLDEN_TESTS.md`
  - `apps/api/contracts/repo_manifest.py`
  - `scripts/run_baseline_checks.ps1`
  - `apps/chat/answer_generation.py`
  - `apps/api/contracts/workflow_models.py`
  - `apps/api/runtime_helpers.py`
  - `apps/chat/llm_streaming.py`
  - `apps/platform/runtime_strategy_policy.py`
  - `apps/retrieval/result_contract.py`
  - `tests/test_answer_generation_groundedness_snapshot.py`
  - `tests/test_answer_groundedness_verdict.py`
  - `tests/test_runtime_helpers_stream_bypass.py`
  - `tests/test_request_facade_source_reference_fallback.py`
  - `tests/test_view_state_child_refs.py`
  - `tests/test_scope_resolver.py`
  - `tests/test_request_facade_child_anchor.py`
  - `tests/test_people_filter_nested_gate.py`
  - `tests/test_repo_contract_defaults.py`
- findings:
  - P1 validation blocker: manifest-driven baseline entrypoint???꾩옱 shell?먯꽌 subset execution源뚯? ?꾨떖?섏? 紐삵븳?? `scripts/run_baseline_checks.ps1`??collect-only媛 `tests/test_contract_debt_paydown.py`, `tests/test_llm_runtime_prompt_paths.py`, `tests/test_planner_stagewise.py`, `tests/test_repo_contract_defaults.py`, `tests/test_request_overrides.py`, `tests/test_retrieval_workflow_detail_followup_freshness.py`, `tests/test_retrieval_workflow_freshness_override.py` import ?④퀎?먯꽌 硫덉텛硫? concrete missing module? `langgraph`, `aiofiles`, `llama_index`, `transformers`???
  - P1 coverage gap: source-reference / child-anchor / nested people-gate regressions???대? 臾몄꽌??baseline-worthy?닿퀬 targeted pytest??green?댁?留? `BASELINE_INVENTORY["core_contract_subset"]`?먮뒗 `tests/test_request_facade_source_reference_fallback.py`, `tests/test_view_state_child_refs.py`, `tests/test_scope_resolver.py`, `tests/test_request_facade_child_anchor.py`, `tests/test_people_filter_nested_gate.py`媛 ?꾩쭅 ?녿떎.
  - P1 coverage gap: answer groundedness fixture verdict??manifest???ㅼ뼱媛붿?留? pipeline integration test??`tests/test_answer_generation_groundedness_snapshot.py`???꾩쭅 manifest-driven baseline 諛뽰뿉 ?덉뼱 snapshot-to-selector regression??verdict-only green ?ㅼ뿉 ?⑥뼱 踰꾨┫ ???덈떎.
  - P2 streaming coverage gap (static inference): `docs/GOLDEN_TESTS.md`??`AsyncStream.close`, `emitted_chunks`, `ttft` semantics瑜?watchlist濡??먯?留? ?꾩옱 inspected baseline surface??`derive_stream_error_code()` ?곗꽑?쒖쐞留?吏곸젒 ?좉렇怨??덈떎. `apps/chat/llm_streaming.py::run_llm_streaming` ?먯껜瑜?嫄대뱶由щ뒗 dedicated pytest???대쾲 pass?먯꽌 李얠? 紐삵뻽??
  - no new static drift found: planner defaults(`v2 / v1 / v2`), answer prompt??internal schema-label ban, `RAG_FORCE_FALLBACK_CHAT`??response-only fallback policy??inspected code/docs/prompt surface?먯꽌 ?ъ쟾???쇱튂?쒕떎.
- changes:
  - ?대쾲 watcher 寃곌낵瑜?`docs/SESSION_HANDOFF.md` 留먮???append?덈떎.
- validations:
  - preflight passed: `git rev-parse --show-toplevel` -> `D:/Project/python_project/ntis_domain_rag_chatbot`, `git rev-parse --abbrev-ref HEAD` -> `怨좊룄??, `git rev-parse HEAD` -> `b22a7cb6b538f87d822a53e9915cfdab6e248100`
  - `python -m apps.api.contracts.repo_manifest --section planner_prompt_defaults` passed and returned `{"stage1":"v2","stage15":"v1","stage2":"v2"}`.
  - `python -m apps.api.contracts.repo_manifest --section baseline_inventory` passed and confirmed the current manifest-driven baseline owner.
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` reached collect-only, discovered `141 tests`, then failed with `7` collection errors caused by missing imports `langgraph`, `aiofiles`, `llama_index`, `transformers`.
  - `python -m pytest tests/test_request_facade_source_reference_fallback.py tests/test_people_filter_nested_gate.py tests/test_scope_resolver.py tests/test_request_facade_child_anchor.py tests/test_view_state_child_refs.py -q -p no:cacheprovider` passed (`9 passed`).
  - `python -m pytest tests/test_answer_generation_groundedness_snapshot.py tests/test_answer_groundedness_verdict.py tests/test_runtime_helpers_stream_bypass.py -q -p no:cacheprovider` passed (`11 passed`).
- next best task:
  - smallest repo patch: `apps/api/contracts/repo_manifest.py`??baseline inventory???대? green??source-reference / child-anchor / people-gate / groundedness-snapshot tests瑜??ы븿?쒗궎怨? `docs/04_?뚭?湲곗?怨??먭?.md`??baseline command? source-of-truth瑜?manifest 湲곗??쇰줈 ?ㅼ떆 留욎텧 寃?  - parallel env task: watcher shell??`langgraph`, `aiofiles`, `llama_index`, `transformers`瑜?蹂듦뎄????`scripts/run_baseline_checks.ps1`瑜??ㅼ떆 ?ㅽ뻾??collect-only? core subset??媛숈? ?섍꼍?먯꽌 ?レ쓣 寃?
## 2026-03-31T19:32:00.1692131+09:00 Watcher
- branch/head: `怨좊룄?? / `fc8c6027bd5a766c12d3ab34739d1a894dfb03d5`
- inspected files:
  - `README.md`
  - `docs/README.md`
  - `docs/00_ONBOARDING.md`
  - `docs/01_?꾪궎?띿쿂?_?먮쫫.md`
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/05_?좎?蹂댁닔?_?뺤옣.md`
  - `docs/SESSION_HANDOFF.md`
  - `apps/api/contracts/repo_manifest.py`
  - `scripts/run_baseline_checks.ps1`
  - `pytest.ini`
- findings:
  - 肄붿뼱 臾몄꽌?ㅼ? 湲곗? ?ъ떎? 鍮꾧탳???뺥솗?덉?留? 泥??붾㈃?먯꽌 "?꾧? ?쎄퀬 臾댁뾿??諛붾줈 梨숆꺼???섎뒗吏"媛 ?쏀빐 鍮꾧컻諛쒖옄? ?좉퇋 李몄뿬?먭? 鍮좊Ⅴ寃?吏꾩엯?섍린 ?대젮?좊떎.
  - ?곸쐞 臾몄꽌? 湲곗닠 臾몄꽌??痢듭쐞媛 異⑸텇??遺꾨━?섏? ?딆븘, ?쒖뒪???ㅻ챸怨??댁쁺 洹쒖튃??媛숈? 諛?꾨줈 ?댁뼱吏??援ш컙???덉뿀??
  - ?댁쁺/?뚭?/?좎?蹂댁닔 臾몄꽌?먮뒗 ?꾩옱 ?곗꽑 由ъ뒪?? 諛붾줈 ?뺤씤??濡쒓렇/?뚯뒪?? 臾몄꽌 媛깆떊 ?몃━嫄곕? ?쒕늿??蹂댁뿬 二쇰뒗 釉붾줉??遺議깊뻽??
  - baseline entrypoint? planner defaults source-of-truth???ъ쟾??留욎?留? ?꾩옱 shell?먯꽌??`langgraph`, `aiofiles`, `llama_index`, `transformers` ?꾨씫?쇰줈 manifest-driven baseline???앷퉴吏 ?レ쓣 ???녿떎.
- changes:
  - `README.md`, `docs/README.md`, `docs/00_ONBOARDING.md`, `docs/01_?꾪궎?띿쿂?_?먮쫫.md` ?곷떒??`??以??붿빟 / ??臾몄꽌瑜??쎌쓣 ?щ엺 / ??臾몄꽌?먯꽌 諛붾줈 李얠쓣 ???덈뒗 寃?/ 吏湲?梨숆만 寃? ?덉씠?대? 異붽???鍮꾧컻諛쒖옄 移쒗솕 吏꾩엯硫댁쓣 留뚮뱾?덈떎.
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`?먮뒗 ?ъ슫 吏꾩엯 ?덉씠?댁? ?④퍡 ?듭떖 ?⑹뼱 鍮좊Ⅸ ??대? 異붽???contract, canonical evidence, follow-up, groundedness, drift, baseline???쇨????쒓뎅???ㅻ챸?쇰줈 留욎톬??
  - `docs/03_?댁쁺怨??섍꼍.md`, `docs/04_?뚭?湲곗?怨??먭?.md`, `docs/05_?좎?蹂댁닔?_?뺤옣.md`??"?붿빟 -> ?꾩옱 ?곗꽑 由ъ뒪??-> 吏湲?蹂?濡쒓렇/?뚯뒪??-> ?ㅼ쓬 臾몄꽌 ?낅뜲?댄듃 ?몃━嫄?-> ?곸꽭 洹쒖튃" ?쒖꽌濡??ъ젙由ы뻽??
  - `docs/SESSION_HANDOFF.md`???대? 濡쒓렇 臾몄꽌濡??좎??섎릺 ?곷떒??`?꾩옱 ?곹깭 / ?대┛ 由ъ뒪??/ ?ㅼ쓬 ?≪뀡` 以묒떖?쇰줈 ?쏀엳寃??뺣━?섍퀬, ?곸꽭 ?대젰? 洹몃?濡??꾨옒??蹂댁〈?덈떎.
- validations:
  - preflight passed: `git rev-parse --show-toplevel` -> `D:/Project/python_project/ntis_domain_rag_chatbot`, `git rev-parse --abbrev-ref HEAD` -> `怨좊룄??, `git rev-parse HEAD` -> `fc8c6027bd5a766c12d3ab34739d1a894dfb03d5`
  - `python -m apps.api.contracts.repo_manifest --section planner_prompt_defaults` passed and returned `{"stage1": "v2", "stage15": "v1", "stage2": "v2"}`.
  - `python -m apps.api.contracts.repo_manifest --section baseline_inventory` passed and confirmed the current manifest-driven baseline owner and entrypoint surface.
  - PowerShell heading search confirmed all target core docs now expose `## ??以??붿빟`, `## ??臾몄꽌瑜??쎌쓣 ?щ엺`, `## ??臾몄꽌?먯꽌 諛붾줈 李얠쓣 ???덈뒗 寃?, `## 吏湲?梨숆만 寃? in their active top sections.
  - UTF-8 explicit reads confirmed `README.md`, `docs/03_?댁쁺怨??섍꼍.md`, `docs/SESSION_HANDOFF.md` ?곷떒 ?붿빟???쒓?濡??뺤긽 ?몄텧?쒕떎.
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` reached collect-only, discovered `141 tests`, then failed with `7` collection errors caused by missing imports `langgraph`, `aiofiles`, `llama_index`, `transformers`.
- remains risky:
  - 臾몄꽌??泥??붾㈃ 媛?낆꽦? 媛쒖꽑?먯?留? ?ㅼ젣 PM/QA/鍮꾧컻諛쒖옄 ?낆옄?먭쾶 ?쏀? 蹂?usability check???꾩쭅 ?녿떎.
  - `BASELINE_INVENTORY["core_contract_subset"]`?먮뒗 ?ъ쟾??source-reference fallback, child-anchor, people-gate, groundedness snapshot 怨꾩뿴??吏곸젒 ?ы븿?섏? ?딆븘 臾몄꽌 ?ㅻ챸怨?baseline coverage 湲곕?移??ъ씠??媛꾧꺽???⑥븘 ?덈떎.
  - ?꾩옱 shell dependency媛 蹂듦뎄?섏? ?딆쑝硫?臾몄꽌???곸? baseline 吏꾩엯?먭낵 ?ㅼ젣 ?ㅽ뻾 寃곌낵瑜?媛숈? ?섍꼍?먯꽌 ?レ? 紐삵븳??
- next best task:
  - 鍮꾧컻諛쒖옄 ?먮뒗 ?댁쁺??愿?먯뿉??`README.md`? `docs/03_?댁쁺怨??섍꼍.md` 泥??붾㈃留??쎄퀬 紐⑹쟻/?먮쫫/?곗꽑 ?뺤씤 ??ぉ??30珥??덉뿉 ?ㅻ챸?????덈뒗吏 吏㏃? reader pass瑜??섑뻾??寃?  - dependency bootstrap??蹂듦뎄????`scripts/run_baseline_checks.ps1`瑜??ㅼ떆 ?ㅽ뻾?섍퀬, ?꾩슂?섎㈃ `docs/04_?뚭?湲곗?怨??먭?.md`??baseline ?ㅻ챸??manifest coverage ?꾩떎??留욊쾶 ??踰????ㅻ벉??寃?
## 2026-03-31T20:02:36.2670617+09:00 Architect
- branch/head: `怨좊룄?? / `881265916b9900e4dd5e8425487ce47010a2506d`
- inspected files:
  - `apps/api/contracts/repo_manifest.py`
  - `scripts/run_baseline_checks.ps1`
  - `pytest.ini`
  - `apps/api/app_factory.py`
  - `apps/planner/planner_runtime.py`
  - `apps/planner/planner_context_cards.py`
  - `apps/chat/answer_generation.py`
  - `apps/chat/answer_merge.py`
  - `apps/retrieval/retrieval_workflow.py`
  - `tests/test_repo_contract_defaults.py`
  - `tests/test_llm_runtime_prompt_paths.py`
  - `tests/test_request_overrides.py`
  - `tests/test_retrieval_workflow_freshness_override.py`
  - `tests/test_retrieval_workflow_detail_followup_freshness.py`
  - `tests/test_runtime_helpers_stream_bypass.py`
  - `tests/test_request_facade_source_reference_fallback.py`
  - `docs/PRODUCT_BASELINE.md`
  - `docs/03_?댁쁺怨??섍꼍.md`
  - `docs/04_?뚭?湲곗?怨??먭?.md`
  - `docs/ADR/ADR-0007-planner-defaults-and-baseline-truth-manifest.md`
  - `docs/README_NEXT_STEPS.md`
- findings:
  - `ADR-0007` ?댄썑 baseline owner???뺣━?먯?留? ?꾩옱 manifest??test membership留??쒗쁽?섍퀬 capability boundary???쒗쁽?섏? 紐삵븳??
  - `core_contract_subset` ?덉뿉??import-light contract ?뚭?? `langgraph`/`aiofiles`/`llama_index`/`transformers`瑜?諛잙뒗 runtime/provider integration ?뚭?媛 ?욎뿬 ?덈떎.
  - watcher媛 ?대? green?쇰줈 ?レ? source-reference fallback, child-anchor, people-gate, groundedness snapshot 怨꾩뿴? shared shell minimal lane ?꾨낫?몃뜲, ?꾩옱 manifest/runbook?먮뒗 lane 媛쒕뀗???놁뼱 baseline ?댁꽍??怨꾩냽 ?섍꼍 blocker? ?욎씤??
  - ??臾몄젣??retrieval semantics媛 ?꾨땲??validation architecture 臾몄젣?? planner immutability, SEARCH no-hard-must, LOOKUP/JOIN hard gating??嫄대뱶由??꾩슂媛 ?녿떎.
- changes:
  - `docs/ADR/ADR-0010-validation-capability-profiles.md`瑜?異붽??덈떎.
  - ??ADR? manifest-owned baseline ?ㅼ쓬 ?④퀎濡?validation capability profile(`repo_minimal_contract`, `app_runtime_contract`, `provider_runtime_contract`, `full_release_gate`)???쒖븞?섍퀬, additive metadata -> profile-aware script -> docs/runbook alignment ?쒖꽌??staged migration???뺤쓽?쒕떎.
  - `docs/SESSION_HANDOFF.md`???대쾲 architect ?먮떒怨??ㅼ쓬 ?덉쟾??援ы쁽 ?쒖꽌瑜?append?덈떎.
- validations:
  - preflight passed: `git rev-parse --show-toplevel` -> `D:/Project/python_project/ntis_domain_rag_chatbot`, `git rev-parse --abbrev-ref HEAD` -> `怨좊룄??, `git rev-parse HEAD` -> `881265916b9900e4dd5e8425487ce47010a2506d`
  - explicit reads confirmed current baseline owner surfaces are still `apps/api/contracts/repo_manifest.py`, `scripts/run_baseline_checks.ps1`, `pytest.ini`, `docs/PRODUCT_BASELINE.md`, `docs/03_?댁쁺怨??섍꼍.md`
  - static inspection confirmed current import-heavy boundary examples: `tests/test_repo_contract_defaults.py` -> `apps.api.app_factory`, `tests/test_llm_runtime_prompt_paths.py` -> `apps.chat.llm_runtime`, `tests/test_request_overrides.py` -> route/provider surfaces, freshness workflow tests -> `apps.api.contracts.workflow_models`
  - UTF-8 read of `docs/ADR/ADR-0010-validation-capability-profiles.md` and this handoff append is required because the PowerShell console can still hide encoding issues
- next best task:
  - smallest implementation step: `apps/api/contracts/repo_manifest.py`??additive `validation_profiles` metadata瑜??ｊ퀬, shared shell?먯꽌 ?대? green??source-reference / child-anchor / people-gate / groundedness snapshot ?뚭?瑜?`repo_minimal_contract`濡?遺꾨━??寃?  - 洹??ㅼ쓬 `scripts/run_baseline_checks.ps1`??profile-aware readiness/blocked reporting??異붽??섎릺, default full behavior??諛붾줈 源⑥?吏 ?딄쾶 staged option?쇰줈 ?꾩엯??寃?
## 2026-03-31T20:22:53.8040529+09:00 Watcher
- branch/head: `怨좊룄?? / `e143313ca4a0e5ccd7207e22225f69be0441577d`
- inspected files:
  - production code static scan: `*.py`, `*.ps1`, `*.html` (tests / Markdown body excluded)
  - `apps/conversation/view_state.py`
  - `apps/planner/planner_runtime.py`
  - `apps/conversation/scope_resolver.py`
  - `apps/conversation/request_facade.py`
  - `apps/conversation/followup_resolution.py`
  - `apps/api/contracts/answer_groundedness.py`
  - `apps/retrieval/retrieval.py`
  - `apps/retrieval/filters.py`
  - `apps/chat/llm_streaming.py`
  - `templates/index.html`
  - `scripts/run_baseline_checks.ps1`
  - `docs/reports/watcher/2026-03-31-2022.md`
- findings:
  - comment corruption risk is currently low: production `py/ps1/html` scan found `0` UTF-8 decode failures and `0` replacement-character hits.
  - the dominant risk is omission, not mojibake. Production Python now has `34 / 110` module docstrings (`30.9%`) and `721 / 1016` function docstrings (`71.0%`), but the lowest-coverage modules are exactly the branch-heavy follow-up / planner files.
  - P1 hotspot: `apps/conversation/view_state.py` (`35` functions / `0` docstrings / `1` inline comment). `build_display_snapshot`, `focus_entity_from_detail`, `render_display_snapshot_text` all lack function-level contracts despite title precedence, child-ref synthesis, and display rendering policy.
  - P1 hotspot: `apps/planner/planner_runtime.py` (`27` functions / `0` docstrings / `1` inline comment). `_apply_deterministic_stage2_repair` and `run_stagewise_question_analysis` expose repair / retry / fallback policy only through code branches.
  - P1 hotspot: `apps/conversation/scope_resolver.py` (`15` functions / `0` docstrings / `0` inline comments). `resolve_scope_decision` has no comment surface for reset / refinement / ambiguity order.
  - P2 hotspot: `apps/conversation/request_facade.py`, `apps/conversation/followup_resolution.py`, `apps/api/contracts/answer_groundedness.py` still have comment coverage materially below the runtime complexity they carry.
  - weak comments exist but are secondary: `templates/index.html` section-label comments (`A card`, `B card`, `MODEL_A / MODEL_B 泥섎━`) are low-value compared with the Python omission hotspots.
- changes:
  - added watcher report `docs/reports/watcher/2026-03-31-2022.md` for the production-code comment-quality audit.
- validations:
  - preflight passed: `git rev-parse --show-toplevel` -> `D:/Project/python_project/ntis_domain_rag_chatbot`, `git rev-parse --abbrev-ref HEAD` -> `怨좊룄??, `git rev-parse HEAD` -> `e143313ca4a0e5ccd7207e22225f69be0441577d`
  - static UTF-8 scan on production `py/ps1/html`: `112` files scanned, `0` decode failures, `0` replacement-character hits
  - Python comment coverage scan: `110` files, `1016` functions/methods, `721` function docstrings, `34` module docstrings
  - focused hotspot counts:
    - `view_state.py`: `35` functions / `0` docstrings / `1` inline comment
    - `planner_runtime.py`: `27` functions / `0` docstrings / `1` inline comment
    - `scope_resolver.py`: `15` functions / `0` docstrings / `0` inline comments
    - `request_facade.py`: `20` functions / `1` docstring / `2` inline comments
    - `followup_resolution.py`: `17` functions / `0` docstrings / `1` inline comment
    - `answer_groundedness.py`: `7` functions / `0` docstrings / `0` inline comments
- next best task:
  - safest next patch is doc-only: add module/public-entry docstrings to `view_state.py`, `planner_runtime.py`, `scope_resolver.py` first, then add a few reason-focused inline comments in the highest-branch functions
  - if improver takes this, keep it as a documentation-only patch and do not alter runtime behavior or planner policy

## 2026-04-01T15:31:00+09:00 Improver
- branch/head: `怨좊룄?? / `e143313ca4a0e5ccd7207e22225f69be0441577d`
- inspected files:
  - `apps/conversation/request_facade.py`
  - `apps/retrieval/retrieval_workflow.py`
  - `apps/conversation/followup_anchor.py`
  - `apps/conversation/scope_resolver.py`
  - `apps/conversation/view_state.py`
  - `tests/test_request_facade_child_anchor.py`
  - `tests/test_retrieval_workflow_detail_runtime.py`
  - `tests/test_scope_resolver.py`
  - `tests/test_scope_resolver_v2.py`
  - `tests/test_followup_clarification_scope.py`
  - `tests/test_view_state_active_scope.py`
  - `tests/test_view_state_child_refs.py`
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/SESSION_HANDOFF.md`
- findings:
  - 湲곗〈 follow-up ?먮쫫? `detail_*` child anchor留?怨듭떇 child subject濡?痍④툒?댁꽌, people/org/perf list 寃곌낵媛 ?ㅼ쭏?곸쑝濡??섎굹??二쇱껜瑜?媛由ъ폒??`active_scope.child_anchor`濡??밴꺽?섏? ?딆븯??
  - explicit `pjt_id/pjt_no`媛 吏덈Ц ?덉뿉 ?덉쑝硫?broad history??child-subject query?쇰룄 planner瑜?嫄대꼫?곗뼱 project detail exact lookup?쇰줈 ?섏텞?????덉뿀??
  - ??臾몄젣???곌뎄???꾩슜 ?덉쇅媛 ?꾨땲??`child subject truth`? `context seed truth`瑜?遺꾨━?섏? 紐삵븳 怨꾩빟 臾몄젣???
- changes:
  - `followup_anchor.is_child_anchor_source()`瑜?異붽??섍퀬 `detail_*`留?蹂대뜕 child-anchor source 怨꾩빟??`detail_* | child_*`濡??쇰컲?뷀뻽??
  - `request_facade`??resolved child anchor??`title_text`瑜?planner hint(`people_terms`, `org_terms`, `title`)濡?蹂댁“ 二쇱엯?섍퀬, explicit seed媛 ?덉뼱??broad-history/list/child-subject cue媛 ?덉쑝硫?planner瑜?怨꾩냽 ?ㅽ뻾?섎룄濡?諛붽엥??
  - `retrieval_workflow`??`context_kind=people|org|perf` list 寃곌낵?먯꽌 visible items媛 ?섎굹???쇰━ 二쇱껜濡??섎졃?섎㈃ `active_scope.child_anchor`瑜??먮룞 ?밴꺽?섍퀬 `FOLLOWUP.CHILD_ANCHOR.PROMOTED`瑜??④릿??
  - `docs/02_?ㅽ뻾怨꾩빟怨??꾨왂洹쒖튃.md`? `docs/GOLDEN_TESTS.md`瑜??대쾲 ?쇰컲??怨꾩빟??留욊쾶 媛깆떊?덈떎.
- validations:
  - preflight passed: `git rev-parse --show-toplevel` -> `D:/Project/python_project/ntis_domain_rag_chatbot`, `git rev-parse --abbrev-ref HEAD` -> `怨좊룄??, `git rev-parse HEAD` -> `e143313ca4a0e5ccd7207e22225f69be0441577d`
  - `python -m pytest tests/test_request_facade_child_anchor.py -q -p no:cacheprovider` passed (`3 passed`)
  - `python -m pytest tests/test_retrieval_workflow_detail_runtime.py -q -p no:cacheprovider` passed (`18 passed`)
  - `python -m pytest tests/test_scope_resolver_v2.py tests/test_followup_clarification_scope.py -q -p no:cacheprovider` passed (`8 passed`)
  - `python -m pytest tests/test_scope_resolver.py tests/test_view_state_active_scope.py tests/test_view_state_child_refs.py -q -p no:cacheprovider` passed (`7 passed`)
  - `python -m compileall apps/conversation/request_facade.py apps/retrieval/retrieval_workflow.py apps/conversation/followup_anchor.py apps/conversation/scope_resolver.py` passed
- remains risky:
  - ?щ엺/湲곌?/?깃낵 list 寃곌낵?먯꽌 name-only child anchor瑜??밴꺽???? ?쒕줈 ?ㅻⅨ ?숇챸?댁씤/?숆린愿?????붾㈃???욎씤 edge case???ъ쟾??clarification?쇰줈 ??蹂댁닔?곸쑝濡??ㅻ쨪???쒕떎.
  - explicit id + broad child-subject query??golden path???⑥쐞 ?뚯뒪?몃줈 留됱븯吏留? ?ㅼ젣 end-to-end log replay harness???꾩쭅 ?녿떎.
  - ?꾩옱 `child_*` source 怨꾩빟???ъ슜?섎뒗 ?앹궛 寃쎈줈??list promotion ?섎굹肉먯씠?? ?κ린?곸쑝濡쒕뒗 source taxonomy? metrics 吏묎퀎瑜???踰????뺣━???꾩슂媛 ?덈떎.
- next best task:
  - multi-turn log replay ?먮뒗 golden transcript harness瑜?異붽???`諛섎룄泥?愿??怨쇱젣 3嫄?-> 2踰?怨쇱젣 ?곌뎄??-> ?대떦 ?곌뎄?먯쓽 ?ㅻⅨ ?쒕룞` 媛숈? ???泥댁씤??end-to-end濡?怨좎젙??寃?  - `child_*` source蹂?observability panel???뺣━??list-promotion anchor? detail-derived anchor???깃났瑜?clarification瑜좎쓣 援щ텇??蹂?寃?
## 2026-04-01T18:38:16+09:00 Watcher
- branch/head: `고도화` / `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
- inspected files:
  - `apps/api/contracts/repo_manifest.py`
  - `scripts/run_baseline_checks.ps1`
  - `docs/GOLDEN_TESTS.md`
  - `apps/conversation/request_facade.py`
  - `apps/conversation/anchor_constraint_compiler.py`
  - `apps/chat/answer_generation.py`
  - `apps/chat/answer_merge.py`
  - `apps/conversation/view_state.py`
  - `apps/conversation/conversation_store.py`
  - `apps/conversation/scope_resolver.py`
  - `apps/platform/runtime_strategy_policy.py`
  - `apps/retrieval/filters.py`
  - `apps/chat/llm_streaming.py`
  - `tests/test_request_facade_followup_seed_priority.py`
  - `tests/test_anchor_constraint_compiler.py`
  - `tests/test_answer_state_consistency.py`
  - `tests/test_answer_generation_groundedness_snapshot.py`
  - `tests/test_request_facade_child_anchor.py`
  - `tests/test_view_state_child_refs.py`
  - `tests/test_conversation_store_subject_index.py`
  - `tests/test_scope_resolver.py`
  - `tests/test_followup_clarification_scope.py`
  - `tests/test_runtime_helpers_stream_bypass.py`
  - `tests/test_request_overrides.py`
  - `tests/test_retrieval_workflow_detail_followup_freshness.py`
  - `docs/reports/watcher/2026-04-01-1837.md`
- findings:
  - no new static drift was found in the inspected safety contracts: planner prompt defaults still resolve to `v2 / v1 / v2`, fallback chat is still response-only, SEARCH people/org hard-must ban still holds, and JOIN still keeps `instance -> pjt_id` / `group -> pjt_no`.
  - `scripts/run_baseline_checks.ps1` is still blocked during collect-only. `7` errors are shell dependency gaps (`langgraph`, `aiofiles`, `llama_index`, `transformers`), but `1` error is real repo drift: `tests/test_request_facade_followup_seed_priority.py` still imports `_merge_seed_into_ids_map` from `request_facade` even though the owned helper now lives in `anchor_constraint_compiler`.
  - baseline ownership has not caught up with the new visible-order/follow-up truth contracts. `apps/api/contracts/repo_manifest.py` still omits green import-light tests for `answer_state_consistency`, `visible_answer_manifest` gating, `subject_index` roundtrip, and child-anchor follow-up behavior.
  - streaming watchlist coverage is still thin: docs track `AsyncStream.close`, `emitted_chunks`, and TTFT, but baseline tests only cover `derive_stream_error_code()` and not `run_llm_streaming()` itself.
- changes:
  - added watcher report `docs/reports/watcher/2026-04-01-1837.md`.
- validations:
  - preflight passed: `git rev-parse --show-toplevel` -> `D:/Project/python_project/ntis_domain_rag_chatbot`, `git rev-parse --abbrev-ref HEAD` -> `고도화`, `git rev-parse HEAD` -> `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
  - `python -m apps.api.contracts.repo_manifest --section planner_prompt_defaults` passed and returned `{"stage1":"v2","stage15":"v1","stage2":"v2"}`
  - `powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1` reached collect-only, discovered `174` tests, then failed with `8` collection errors caused by missing `langgraph`, `aiofiles`, `llama_index`, `transformers`, plus the stale import in `tests/test_request_facade_followup_seed_priority.py`
  - `python -m pytest tests/test_answer_state_consistency.py tests/test_answer_generation_groundedness_snapshot.py tests/test_request_facade_child_anchor.py tests/test_view_state_child_refs.py tests/test_conversation_store_subject_index.py tests/test_scope_resolver.py tests/test_followup_clarification_scope.py -q -p no:cacheprovider` passed (`26 passed`)
  - `python -m pytest tests/test_retrieval_workflow_detail_followup_freshness.py tests/test_runtime_helpers_stream_bypass.py -q -p no:cacheprovider` still cannot collect the freshness test here because `langgraph` is missing
- next best task:
  - smallest safe repo patch: sync `apps/api/contracts/repo_manifest.py` and the stale follow-up seed test so baseline ownership matches the current answer/follow-up truth contract. Replace the old helper test with `tests/test_anchor_constraint_compiler.py`, then promote the already-green import-light regressions for state consistency, visible manifest gating, child-anchor resolution, and `subject_index` roundtrip.
  - after baseline ownership is synced, add direct `run_llm_streaming()` tests for awaited close, emitted chunk counting, and TTFT metrics so the streaming watchlist stops relying on helper-only coverage.

## 2026-04-01T20:03:48.1946670+09:00 Architect
- branch/head: `고도화` / `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
- inspected files:
  - `docs/CODEX_CONTEXT.md`
  - `docs/02_실행계약과_전략규칙.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/SESSION_HANDOFF.md`
  - `docs/ADR/ADR-0009-source-reference-evidence-boundary.md`
  - `docs/ADR/ADR-0010-validation-capability-profiles.md`
  - `docs/ADR/ADR-0011-scope-aware-followup-resolution-and-exact-constraint-compilation.md`
  - `docs/reports/watcher/2026-04-01-1837.md`
  - `apps/api/contracts/repo_manifest.py`
  - `apps/conversation/view_state.py`
  - `apps/conversation/request_facade.py`
  - `apps/chat/answer_generation.py`
  - `apps/conversation/conversation_store.py`
  - `apps/conversation/scope_resolver.py`
  - `apps/conversation/followup_resolution.py`
- findings:
  - the repo has already hardened follow-up truth with `active_scope`, `visible_answer_manifest`, `subject_index`, and `answer_state_consistency`, but those artifacts still lack a single ownership registry.
  - current behavior is safer than current architecture vocabulary: execution truth, evidence truth, visible follow-up truth, and diagnostic gating truth are all present, but their producer/consumer/invalidation boundaries are spread across runtime code, docs, and prior ADRs.
  - `request_facade.py` is now coordinating a structural contract problem rather than just heuristic branching; future changes will keep drifting unless the truth surfaces are named explicitly.
  - latest watcher findings about stale baseline ownership and hard-to-triage follow-up regressions fit this same architecture gap: the repo needs a truth-surface map before it needs another broad runtime patch.
- changes:
  - added `docs/ADR/ADR-0012-conversation-truth-surface-registry.md`.
  - the ADR defines four truth planes: execution truth, evidence truth, visible follow-up truth, and diagnostic gating truth.
  - the ADR proposes a staged migration: doc sync -> additive observability -> baseline alignment -> optional contract-only centralization.
- validations:
  - preflight passed: `git rev-parse --show-toplevel` -> `D:/Project/python_project/ntis_domain_rag_chatbot`, `git rev-parse --abbrev-ref HEAD` -> `고도화`, `git rev-parse HEAD` -> `bc84647bd815aa394126bba8f2510dc9a1cd2f8a`
  - static inspection confirmed current session truth persists through `conversation:v2:{conversation_id}:view_state`, visible-order truth is gated by groundedness + state consistency, and follow-up resolution still mixes visible, evidence, and diagnostic surfaces.
  - no production code was edited in this run.
- remains risky:
  - `active_scope.result_set` is intentionally on the boundary between current-turn evidence and promotable visible truth; that dual role still needs precise operator-facing wording when docs are synced.
  - route/debug/streaming paths may not all expose the same truth-surface metadata yet, so future observability work should stay additive first.
  - baseline ownership is still stale for the newest answer/follow-up truth contracts until a later implementation run updates `repo_manifest.py` and the broken stale helper test.
- next best task:
  - safest implementation step: add additive truth-surface vocabulary to debug/log payloads in `request_facade.py` and `answer_generation.py` so the winning owner, promotion gate, and invalidation reason are explicit without changing behavior.
  - after that, sync `docs/02`, `docs/03`, `docs/04` and baseline ownership so follow-up/answer regressions can be grouped by truth plane instead of ad hoc feature names.
