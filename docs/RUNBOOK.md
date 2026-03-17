# RUNBOOK — NTIS Domain RAG 운영 / 트리아지 가이드

> 목적: 장애나 품질 저하를 "감"이 아니라 **계약(Contract) + trace/log** 기준으로 진단하기 위함이다.
>
> 범위: 이 문서는 운영 대응과 triage를 다룬다. 계약 자체는 `docs/CONTRACT.md`, 테스트 기준은 `docs/GOLDEN_TESTS.md`를 함께 본다.

---

## 0) 2026-03-16 안정화 메모

- Planner skip 정책: planner는 `ids_map`에 explicit identifier seed가 있을 때만 건너뛴다. non-id heuristic hint는 계속 planner를 태운다.
- Retrieval 실패: runtime retrieval exception은 empty-success가 아니라 request error 또는 degraded failure로 다룬다.
- Health 의미: `/health`는 compiled graph가 있으면 ready이며, Redis/KV degradation은 payload에만 노출한다.
- Streaming 의미: direct-answer mode는 answer stream 하나만 내보낸다.
- JOIN 의미: relation query는 `action=list`, `output_type=relation`으로 정규화되며, JOIN `head`는 반드시 `relation[1]`과 같아야 한다.

---

## 1) 빠른 트리아지(3분 코스)

### Step 1. Planner / Strategy 확인

요청 1건에 대해 아래 정보를 먼저 모은다.
- 원문 질의(query)
- `NormalizedIntent`
- `StrategySpec`

체크 항목:
- mode가 `search|lookup|join` 중 하나인가
- JOIN인데 relation이 비어 있지 않은가
- relation이 `project_perf|perf_project|null` 외 값(특히 people/org 포함)으로 들어가지 않았는가
- lookup / join인데 `ids_map.pjt_id`와 `ids_map.pjt_no`가 동시에 들어가지 않았는가

관련 코드:
- `apps/core/planner_contract.py::planner_contract_mode()`
- `apps/core/planner_contract.py::validate_planner_contract()`
- `apps/core/filters.py::validate_planner_join_keys()`
- `apps/api/contracts/workflow_models.py::QuestionAnalysisV2.validate_join_contract()`

### Step 2. Filter compile 결과 확인

- planner filter spec(JSON)이 Qdrant Filter로 어떻게 compile됐는지 확인한다.
- SEARCH에서 must가 과도하게 생기지 않았는지 본다.

관련 코드:
- `apps/core/planner_contract.py::StrategyCompiler.compile()`
- `apps/core/filters.py::compile_filter()`

### Step 3. Retrieval hit 분포 확인

- dense hit / lexical hit가 모두 0인지
- collection 단위 hit가 정상인지
- hybrid retrieval이 사실상 꺼져 BM25-only 우회 상태인지 확인한다

관련 코드:
- `apps/core/retrieval.py::dense_retrieve_hybrid_multi()`

### Step 4. rerank / 결과 계약 확인

- reranked 결과가 없거나 근거가 약하면 결과 계약 실패 가능성을 본다.

관련 코드:
- `apps/core/result_contract.py::enforce_reranked_contract()` (`RAG_EMPTY_RESULT_CONTRACT`)

---

## 2) Observability 최소 스키마

요청 단위에서는 `trace_id` 또는 `request_id` 기준으로 아래 이벤트를 따라간다.

### 운영 기본(`RAG_LOG_LEVEL=normal`)

- `REQ.START`
- `PLANNER.PIPELINE`
- `KS.RESULT`
- `RAG.PLAN`
- `RAG.RETRIEVE`
- `RAG.RESULT.TOP`
- `RAG.CONTEXT`
- `RAG.RESULT`
- `LLM.RESULT`
- `REQ.SUMMARY`
- `REQ.ERROR`
- `REQ.END`

`REQ.SUMMARY.total_ms`는 요청 시작 시점부터의 end-to-end 시간이다.

### 디버그(`RAG_LOG_LEVEL=debug`)

- `STREAM.DONE`
- `RAG.STRATEGY.DIFF.*`
- `RAG.STRATEGY.POLICY`
- `RAG.PLAN.MODE_CONFLICT`
- `RAG.PLAN.JOIN_EXECUTED`
- `RAG.PLAN.RELATION_LOOKUP_POLICY`
- `RAG.FILTER.COMPILED.QDRANT`
- `RAG.JOIN.HOP1.TOP`
- `RAG.JOIN.HOP2.TOP`
- `RAG.MERGED_RRF.TOP`

### trace(`RAG_LOG_LEVEL=trace`)

- 점수 분포
- 세부 payload 직렬화
- 계약 위반 / filter miss / 이상치 추적용 샘플 로그

---

## 3) 코드 버전 불일치 점검

- 서버 시작 직후 `CODE.FINGERPRINT` 이벤트에서 `app_main_sha256`, `rag_pipeline_sha256`, `stage=startup`을 확인한다.
- 요청 단위 이벤트(`REQ.START`, `REQ.END`, `REQ.ERROR`)와 RAG context 이벤트에도 같은 fingerprint가 포함되는지 본다.
- 같은 시각 다른 인스턴스의 fingerprint가 다르면 롤링 배포 불일치 또는 핫패치 흔적을 의심한다.

---

## 4) 디버그를 켜는 방법

### 4.1 RAG 내부 로그 레벨

- `RAG_LOG_LEVEL=normal|debug|trace` (기본 `normal`)
- `RAG_DEBUG=1`이면 `debug`로 승격

예시:

```bash
export RAG_LOG_LEVEL=debug
```

### 4.2 결과 TopN 로그

- normal tier: `RAG.RESULT.TOP`, `RAG_LOG_TOPN_NORMAL`(기본 3)
- debug tier: `RAG.RESULT.TOP.DEBUG`, `RAG_LOG_TOPN_DEBUG`(기본 12)

예시:

```bash
export RAG_LOG_LEVEL=debug
export RAG_LOG_TOPN_NORMAL=3
export RAG_LOG_TOPN_DEBUG=12
```

### 4.3 stagewise planner 활성 확인

- 이벤트: `APP.CONFIG`
- 필드: `planner_stagewise_enabled`
- 기대값: `1`

보조 확인 이벤트:
- `PLANNER.PIPELINE`
- `PLANNER.ASSEMBLE`

---

## 5) Stagewise Planner 전환 RUNBOOK

### 필수 환경 변수

```bash
PLANNER_STAGE1_PROMPT_VERSION=v1
PLANNER_STAGE2_PROMPT_VERSION=v1
PLANNER_TEMPERATURE=0.0
```

### 정상 시 관측해야 할 로그

- `PLANNER.STAGE1`
- `PLANNER.GATE`
- `PLANNER.STAGE2`
- `PLANNER.ASSEMBLE`
- 필요 시 `PLANNER.REGATE`

### 중단 / 롤백 신호

- `PLANNER_ACTION_MODE_MISMATCH`
- `PLANNER_PARSE_FINAL_FAILED`
- `RAG_EMPTY_RESULT_CONTRACT`
- `mode=JOIN`인데 `docs_found=0`
- `PLANNER.IDS_MAP.INVALID_VALUE` 급증

### stagewise triage 절차

#### Step A. stage1 확인
- `action`, `head`, `relation_candidate`, `referential_followup`, `confidence`를 본다.

#### Step B. deterministic gate 확인
- 최종 `mode`, `relation`, `join_key_mode`, `target_cols`가 어떻게 결정되었는지 본다.

#### Step C. stage2 채움 확인
- `ids_map`에 사람명/기관명이 들어가지 않았는지 확인한다.
- 기관 role field가 섞이지 않았는지 확인한다.

#### Step D. assemble 이후 확인
- `PLANNER.ASSEMBLE` 이후 `apply_planner_strategy()`에서 mismatch가 나는지 본다.

#### Step E. post-stage2 re-gate 확인
- `PLANNER.REGATE`의 `regate_eligible`, `regate_changed`, `before_*`, `after_*`를 확인한다.

---

## 6) 자주 보는 장애 패턴과 대응

### (A) JOIN인데 결과가 0

확인:
- `validate_planner_contract()`
- `validate_resolved_join_keys()`
- `validate_group_join_runtime_keys()`
- `RAG.JOIN.GROUP.RESOLVE`

### (B) SEARCH인데 결과가 지나치게 좁아짐

확인:
- compile 결과의 must / should 구조
- `search_filter_enabled`

대응:
- SEARCH hard filter 금지 규칙을 validator에서 강제한다.

### (C) LOOKUP인데 결과가 너무 넓거나 엉뚱함

확인:
- `ids_map`
- compile된 filter
- `lookup_filter_policy`
- `lookup_filter_min_should`

### (D) LLM streaming에서 content가 늦거나 비어 보임

확인:
- `ttft_any_ms`, `ttft_content_ms`
- `reasoning_chars`, `content_chars`
- `stream_content_emitted_chunks`
- answer 경로의 `include_reasoning=false`

---

## 7) 장애 대응 체크리스트

```txt
[ ] query / hint / request_id 확보
[ ] normalized_intent 확인
[ ] strategy(mode/action/relation/join_key_mode) 확인
[ ] ids_map(pjt_id vs pjt_no XOR) 확인
[ ] compile 결과(qdrant_filter must/should/min_should) 확인
[ ] retrieval hit 분포(dense/lexical/collection) 확인
[ ] group JOIN의 resolved_pjt_ids_count 확인
[ ] rerank score(avg/max) 확인
[ ] contract_fail_reason / error_code 기록
[ ] 재현용 최소 입력(query + hint + env) 정리
```

---

## 8) 추가 메모

- `RAG.ORG.MATCH.POLICY`, `RAG.SERVER_FILTER.ORG_GATE`, `RAG.JOIN.HOP2.ORG_GATE.SKIP` 로그로 기관 필터 정책을 추적한다.
- title 필터는 hard gate가 아니라 soft rerank로만 동작해야 한다.
- sparse encoder warmup은 `RAG_FASTEMBED_WARMUP_ON_BOOT=false`로 끌 수 있다.
- Conversation cache는 `last_canonical_evidence`, `last_render_profile`을 저장한다.

