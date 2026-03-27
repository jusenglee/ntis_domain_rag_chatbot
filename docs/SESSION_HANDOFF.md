# SESSION_HANDOFF.md

## Bootstrap checkpoint
- repo: `ntis_domain_rag_chatbot`
- working branch expected: `고도화`
- created_for: Codex Watcher / Improver / Architect

## What is confirmed
- 이 프로젝트는 NTIS RAG다.
- retrieval strategy는 SEARCH / LOOKUP / JOIN으로 분리된다.
- planner는 단일·불변 strategy를 내려야 한다.
- 사람/기관 질의는 기본 LOOKUP이다.
- group join은 `pjt_no`, instance join은 `pjt_id`를 쓴다.
- 운영 기본은 promotion disable, fallback chat off 이다.
- planner live path는 `apps/api/app_factory.py -> apps/api/services/query_analysis.py -> apps/api/services/planner_runtime.py` 이고 기본 prompt version은 `v2 / v1 / v2` 다.
- answer live path는 `apps/api/app_factory.py::_generate_answer -> apps/api/services/answer_generation.py -> apps/api/services/llm_runtime.py` 이고 system prompt asset은 `prompts/ntis_chatbot*.md` 다.
- 별도 answer verifier/repair prompt stack은 없다. answer gate는 `apps/api/services/answer_merge.py`, query repair는 `apps/api/services/rag_retriever.py` 에 있다.
- streaming path는 `apps/api/routes.py`, `apps/core/llm_streaming.py`, `apps/api/streaming/*` 이고 route 계층은 `clarification`, `answer.final`, `done` terminal sequence를 직접 보장한다.
- observability/logging path는 `apps/api/app_factory.py::_log_event` 와 `apps/api/services/runtime_helpers.py::setup_file_logging` 이며 기본 log file은 `logs/app.log` 다.
- baseline/source-of-truth 검증 명령은 `pytest.ini` 와 `scripts/run_baseline_checks.ps1` 에 있다.

## What remains risky
- `docs/03_운영과_환경.md` 가 planner 기본값을 `v1` 과 `v2` 로 동시에 적고 있고 smoke 예시는 현재 없는 테스트 파일을 가리킨다.
- `tests/test_eval_fixture_schema.py` 는 fixture shape만 검사하며 `docs/PRODUCT_BASELINE.md` 가 요구하는 `project / perf / people / org / follow-up / id` 축 보장을 강제하지 않는다.
- `tests/test_runtime_helpers_stream_bypass.py` 는 non-empty stream의 `EMPTY_STREAM` 회피만 본다. route-level `answer.final -> done` ordering, `ttft_deadline_exceeded` 우선순위, degraded terminal payload는 아직 회귀 테스트가 약하다.
- 현재 watcher 로컬 Python 환경에는 `pytest` 가 없어 런타임 테스트 실행 검증이 막혀 있다.

## First tasks for Watcher
1. branch / head 일치 여부 확인
2. planner / answer / verifier / repair 파일 위치 식별
3. fallback / degraded response 로직 위치 식별
4. strategy drift 가능 경로 식별
5. docs와 코드의 불일치 목록화

## First tasks for Improver
1. Watcher 보고서에서 가장 작은 P0 또는 가장 값싼 P1 1건 선택
2. 관련 검증 명령 확인
3. 1개 패치만 수행
4. regression 또는 golden test 추가
5. 다음 위험 포인트 handoff 남기기

## First tasks for Architect
1. 현재 answer pipeline 문서화
2. prompt stack과 verifier stack을 시스템 구성요소로 정리
3. staged ADR 작성
4. production code 수정은 하지 않기

## Candidate first improvement
- 스트리밍 경로에서 async close await 누락 여부 확인
- emitted_chunks=0 판정 로직과 ttft deadline 상호작용 확인

## Update rule
이 파일은 매 실행 후 아래를 append 한다.
- date/time
- branch/head
- inspected files
- findings
- changes
- validations
- next best task

---

### 2026-03-27 15:50:44 +09:00
- branch/head: `고도화` / `224f3c3ec28474674535efdb21086723b371f005`
- inspected files:
  - `apps/api/services/request_overrides.py`
  - `apps/api/routes.py`
  - `tests/test_oracle_request_overrides.py`
  - `docs/03_운영과_환경.md`
- findings:
  - Oracle defaults loader는 기존에도 `IRD_PARAM` 조회 경로를 갖고 있었지만, 서버 기동 시 connector 요약 로그와 요청별 lookup 상태 로그가 없어 운영 중 성공/실패 판별이 어려웠다.
  - `from_env()` 동작은 env 미설정 시 `None` 반환이 아니라 코드 기본값(`ird` / `ird_12#$` / KNTIS DSN) 사용이다.
  - 현재 작업 Python 환경에는 `pytest`, `fastapi`가 없어 route-level 통합 검증은 직접 실행하지 못했다.
- changes:
  - Oracle defaults loader에 connector summary와 lookup meta(`oracle_lookup_status`, `oracle_loaded_keys` 등)를 추가했다.
  - 서버 기동 시 `[request_overrides] Oracle defaults connector configured ...` 로그가 남도록 했다.
  - `/query/stream`, `/query/debug`에서 `REQ.ORACLE.DEFAULTS` 구조화 로그를 남기도록 했다.
  - 관련 테스트를 보강하고 운영 문서에 새 로그 해석 기준을 추가했다.
- validations:
  - `python -m py_compile apps/api/services/request_overrides.py apps/api/routes.py tests/test_oracle_request_overrides.py`
  - custom Python validation: `request_overrides_validation_ok`
  - 미실행: `python -m pytest ...` (`pytest` 미설치), FastAPI TestClient 기반 route smoke (`fastapi` 미설치)
- next best task:
  - 실제 서버 런타임 Python 환경에서 `/query/stream` 1회 호출 후 `REQ.ORACLE.DEFAULTS`가 `loaded` 또는 `query_failed`로 남는지 확인
  - 필요 시 Oracle lookup 실패 시 error code/latency까지 함께 남기도록 확장

## 2026-03-27T15:21:12+09:00 Improver
- branch/head: `고도화` / `224f3c3ec28474674535efdb21086723b371f005`
- inspected files: `docs/02_실행계약과_전략규칙.md`, `docs/03_운영과_환경.md`, `docs/04_회귀기준과_점검.md`, `docs/SESSION_HANDOFF.md`, `apps/api/services/detail_contract.py`, `apps/api/services/retrieval_workflow.py`, `apps/api/services/view_state.py`, `apps/api/services/context_helpers.py`, `apps/core/canonical_evidence.py`, `tests/test_detail_contract.py`, `tests/test_retrieval_workflow_detail_runtime.py`
- findings:
  - strict detail coverage still let a polluted anchor/display label override the hydrated project title, which explains logs like `title: 1. 김봉준` in `[Reference Context]`.
  - NTIS project detail coverage was not hydrating `summary/goal/period/budget` from raw `meta_basic/content_*` fields even when the payload clearly contained those values.
  - helper/canonical title precedence had drifted from the documented contract because some paths still preferred `title_text` ahead of `title1`.
- changes:
  - `apps/api/services/detail_contract.py`: changed project detail title precedence to prefer hydrated/raw title axes before anchor fallback, and hydrated `summary/goal/period/budget` from `meta_basic` and `content_*`.
  - `apps/api/services/context_helpers.py`: changed payload title preference to `title1 -> title_text -> title2`.
  - `apps/core/canonical_evidence.py`: changed canonical fact title collection to `title1 -> title_text -> title2`.
  - `tests/test_detail_contract.py`: added regression coverage for polluted anchor labels, NTIS `meta_basic` hydration, helper title preference, and canonical evidence title precedence.
- validations:
  - `python -m py_compile apps/api/services/detail_contract.py apps/api/services/context_helpers.py apps/core/canonical_evidence.py tests/test_detail_contract.py` passed
  - `python -m pytest tests/test_detail_contract.py tests/test_retrieval_workflow_detail_runtime.py -q` failed in local shell: `No module named pytest`
  - inline import/runtime validation could not run in local shell because project deps such as `pydantic` are absent from the shared Windows Python environment
- next best task: run the touched detail tests inside the real app/runtime environment (the one that has `pytest` and `pydantic`), then capture one strict debug request to confirm `[Reference Context]` now shows the canonical project title plus hydrated `summary/goal/period/budget`.

## 2026-03-27T15:17:38.4825738+09:00 Watcher
- branch/head: `고도화` / `224f3c3ec28474674535efdb21086723b371f005`
- inspected files: `docs/CODEX_CONTEXT.md`, `docs/SESSION_HANDOFF.md`, `docs/03_운영과_환경.md`, `docs/GOLDEN_TESTS.md`, `docs/PRODUCT_BASELINE.md`, `pytest.ini`, `scripts/run_baseline_checks.ps1`, `apps/api/app_factory.py`, `apps/api/routes.py`, `apps/api/services/query_analysis.py`, `apps/api/services/planner_runtime.py`, `apps/api/services/llm_runtime.py`, `apps/api/services/answer_merge.py`, `apps/api/services/runtime_helpers.py`, `apps/core/result_contract.py`, `apps/api/services/request_facade.py`, `tests/test_llm_runtime_prompt_paths.py`, `tests/test_answer_merge_bypass.py`, `tests/test_runtime_helpers_stream_bypass.py`, `tests/test_eval_fixture_schema.py`, `tests/test_planner_prompt_cards.py`
- findings:
  - P1 docs drift: `docs/03_운영과_환경.md` 는 `PLANNER_STAGE1/2_PROMPT_VERSION=v1` 를 예시로 남겨 둔 채 후반부에는 V2 defaults를 다시 선언하고, smoke 예시는 없는 `tests/test_api_routes_runtime.py`, `tests/test_request_facade_and_context.py` 를 가리킨다.
  - P1 coverage gap: `tests/test_eval_fixture_schema.py` 는 axis list 존재만 확인한다. 현재 fixture가 우연히 required axis를 포함하더라도 CI는 baseline 축 누락을 잡지 못한다.
  - P2 latent drift: `apps/api/services/request_facade.py` 의 exported helper default는 아직 `planner_stage15_prompt_version='v1'`, `planner_stage2_prompt_version='v1'` 이다. 현재 app wiring은 explicit version을 넘겨 active bug는 아니지만 재사용 시 stale default가 다시 살아날 수 있다.
- changes: bootstrap facts와 현재 위험 목록을 최신 코드 기준으로 갱신했다.
- validations:
  - `python -m py_compile apps/api/routes.py apps/api/services/runtime_helpers.py apps/api/services/answer_merge.py tests/test_eval_fixture_schema.py tests/test_runtime_helpers_stream_bypass.py` 통과
  - `python -m pytest --collect-only -q` 실패: `No module named pytest`
- next best task: `docs/03_운영과_환경.md` 의 planner default / smoke 예시를 현재 코드와 baseline script에 맞게 정리하고, 이어서 `tests/test_eval_fixture_schema.py` 에 required axis subset 검증을 추가한다.

## 2026-03-27T16:08:00+09:00 Improver
- branch/head: `고도화` / `224f3c3ec28474674535efdb21086723b371f005`
- inspected files: `apps/api/services/planner_runtime.py`, `apps/core/planner_validation.py`, `apps/api/services/rag_retriever.py`, `apps/api/services/retrieval_workflow.py`, `prompts/planner_stage15_v1.md`, `prompts/planner_stage2_v2.md`, `tests/test_planner_stagewise.py`, `tests/test_eval_fixture_schema.py`, `eval/sample_queries.jsonl`, `docs/02_실행계약과_전략규칙.md`, `docs/03_운영과_환경.md`, `docs/04_회귀기준과_점검.md`, `docs/GOLDEN_TESTS.md`, `docs/PRODUCT_BASELINE.md`
- findings:
  - stage2 retry 실패 뒤에는 곧바로 raw-question fallback만 있어 planner miss를 deterministic하게 복구할 경로가 없었다.
  - `Stage2ValidationResult` 는 `missing_must_keep_terms` 를 결과로 들고 다니지 않아 quoted title 같은 exact phrase 보정을 runtime이 수행할 수 없었다.
  - eval fixture schema는 새 planner 위험 축(`broad_history`, `quoted_title`, `source_ref`, `ordinal`)을 baseline으로 강제하지 못했다.
- changes:
  - `apps/core/planner_validation.py`: `missing_must_keep_terms` 를 validation 결과에 추가했다.
  - `apps/api/services/planner_runtime.py`: stage2 retry 실패 뒤 deterministic repair 단계와 raw-query fallback reason log를 추가했다.
  - `apps/api/services/retrieval_workflow.py`: retrieval query resolution log에 `query_resolution_reason` 를 추가했다.
  - `prompts/planner_stage15_v1.md`, `prompts/planner_stage2_v2.md`: broad-history, quoted-title, ordinal/source-reference follow-up 보존 규칙과 예시를 강화했다.
  - `tests/test_planner_stagewise.py`, `tests/test_eval_fixture_schema.py`, `eval/sample_queries.jsonl`: deterministic repair, drift, fixture axis baseline 회귀를 추가했다.
  - 관련 contract/ops/regression/baseline 문서를 현재 동작에 맞게 갱신했다.
- validations:
  - pending
- next best task: shared Windows Python 환경에서 `pytest` 와 app dependency가 준비된 런타임으로 planner subset 회귀를 실제 실행해 deterministic repair 경로가 green인지 확인한다.
