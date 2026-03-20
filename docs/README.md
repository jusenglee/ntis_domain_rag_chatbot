# NTIS RAG 문서 안내

이 문서 묶음은 현재 NTIS Domain RAG의 정본 운영 문서다.
실행 기준은 `apps/* + v3`이며, 과거 `server3.py` 계열이나 v2 문서는 기준선이 아니다.

## 먼저 읽을 문서

1. `01_아키텍처와_흐름.md`
2. `02_실행계약과_전략규칙.md`
3. `03_운영과_환경.md`
4. `04_회귀기준과_점검.md`
5. `05_유지보수와_확장.md`

## 현재 기준선

- `IntentPayloadV3`가 활성 transport 계약이다.
- `intent_payload_version="v3"`와 `strategy_version="v3"`가 함께 간다.
- 정본 구현은 planner stage 1 / deterministic gate / assembled question analysis / execution strategy 순서다.
- retrieval-first, strict contract, staged-only baseline을 사용한다.
- `candidate_keys.project_key`는 unresolved exact project key다.
- `project_key_policy=ambiguous_or`는 exact OR exact discovery를 뜻하며 자유 텍스트 fallback을 뜻하지 않는다.
- `join_key_mode=deferred`는 gate가 아니라 assembled/runtime에서 합법인 mode다.
- `lookup` / `join` + `reason=no_reranked`는 `normal_no_result`이고, `search` + `reason=no_reranked`는 `strict_search`다.

## 3층 artifact 경계

### 1. Gate artifact
- 파일: `apps/core/planner_staged.py`
- 역할: deterministic gate artifact를 만든다.
- gate join mode는 `instance | group`만 허용한다.

### 2. Assembled question analysis
- 파일: `apps/api/services/planner_runtime.py`, `apps/api/services/planner_service.py`, `apps/api/contracts/workflow_models.py`
- 역할: planner payload를 assembled question analysis로 완성한다.
- assembled/runtime join mode는 `instance | group | deferred`를 허용한다.
- ambiguous project key의 deferred legalize는 fallback이 아니라 assembly legalize다.

### 3. Execution strategy
- 파일: `apps/core/rag_runtime_prelude.py` 이후 runtime
- 역할: execution-ready strategy를 compile하고 retrieval, join, aggregation, bundle, reverse trace를 실행한다.
- lower layer는 새 SEARCH / LOOKUP / JOIN 전략을 발명하지 않는다.

## 확장 runtime capability

현재 기준 문서는 아래 확장 경로도 포함한다.

- `comparison`
- `series`
- `perf_to_project_to_perf`
- `anchor_resolution`
- `pattern_analysis`
- `multi_hop_bundle`

## 코드 Source of Truth

- request understanding: `apps/api/services/request_facade.py`
- planner assembly: `apps/api/services/planner_service.py`, `apps/api/services/planner_runtime.py`
- contract validation: `apps/core/planner_contract.py`
- runtime prelude: `apps/core/rag_runtime_prelude.py`
- runtime dispatcher: `apps/core/rag_pipeline.py`
- join runtime: `apps/core/rag_join_runtime.py`, `apps/core/rag_join_orchestration.py`
- result assembly: `apps/api/services/rag_result_assembly.py`

## 문서 사용 원칙

- raw payload, canonical evidence, prompt view를 서로 다른 artifact로 다룬다.
- `pjt_id`와 `pjt_no`를 같은 값처럼 취급하지 않는다.
- answer fallback과 strategy fallback을 구분한다.
- authoritative docs는 현재 파일명 기준으로 유지한다.
