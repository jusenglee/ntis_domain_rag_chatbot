# RUNBOOK — NTIS Domain RAG 운영 / 트리아지 가이드

> 목적: 장애나 품질 저하를 retrieval-first 계약 기준으로 진단하기 위함입니다.
>
> 범위: 이 문서는 운영 대응과 triage를 다룹니다. 계약 자체는 `docs/CONTRACT.md`, 테스트 기준은 `docs/GOLDEN_TESTS.md`를 함께 봅니다.

---

## 0) 운영 기준선

- 시스템 정체성: `retrieval-first search system with chat UX`
- triage 우선순위는 answer wording보다 retrieval correctness입니다.
- planner invalid / contract violation은 strict fail-close로 다룹니다.
- retrieval failure를 empty-success로 숨기지 않습니다.
- chat UX 문제는 retrieval correctness 점검 이후에 봅니다.

---

## 1) 빠른 트리아지(3분 코스)

단계별 시스템 흐름 자체는 `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`를 기준으로 보고,
이 문서는 각 단계에서 무엇을 확인해야 하는지에 집중합니다.

### Step 1. Query / Intent 확인

확인 로그:
- `REQ.START`
- `PLANNER.PIPELINE`

확인 필드:
- 원문 질의
- `NormalizedIntent`
- explicit identifier seed 유무

정상 / 이상 판정:
- 정상: broad topic / exact lookup / relation query 의도가 retrieval 의미로 남아 있음
- 이상: non-id hint 때문에 planner가 불필요하게 skip되거나 retrieval 의미가 유실됨

다음 액션:
- `apps/api/services/request_facade.py`
- `apps/core/query_intent.py`
- `apps/core/pipeline_steps.py`

운영 메모:
- cheap precheck와 explicit hint는 identifier, year, perf type, title 같은 구조 신호만 전달해야 합니다.
- 사람 이름, 기관명, 기관 역할은 precheck에서 복원하지 말고 planner가 해석해야 합니다.

### Step 2. Strategy 확인

확인 로그:
- `PLANNER.ASSEMBLE`
- `RAG.PLAN`

확인 필드:
- `mode`
- `relation`
- `join_key_mode`
- `target_cols`
- `strategy_summary`

정상 / 이상 판정:
- 정상: `SEARCH / LOOKUP / JOIN` 중 하나로 일관되게 확정됨
- 이상: planner layer와 execution layer가 다른 전략을 가리킴

다음 액션:
- `/query/debug`에서는 `question_analysis`보다 `strategy_summary`를 우선 확인합니다.
- `apps/core/planner_contract.py`
- `apps/core/rag_runtime_prelude.py`

### Step 3. Filter compile 확인

확인 로그:
- `RAG.FILTER.COMPILED.QDRANT`
- `RAG.COL.RETRIEVE`

확인 필드:
- must / should 구조
- identifier / role filter 반영 여부
- JOIN hop filter의 join key contract 일치 여부

정상 / 이상 판정:
- 정상: retrieval intent와 compiled filter 의미가 일치함
- 이상: SEARCH에 hard must가 생기거나 LOOKUP/JOIN 제약이 누락됨

다음 액션:
- `apps/core/planner_contract.py::StrategyCompiler.compile()`
- `apps/core/filters.py::compile_filter()`
- `apps/core/rag_compile_runtime.py`

### Step 4. Retrieval hit 분포 확인

확인 로그:
- `RAG.RETRIEVE`
- `RAG.COL.STATS`

확인 필드:
- dense hit / lexical hit
- collection 단위 hit
- hybrid retrieval 사용 여부

정상 / 이상 판정:
- 정상: 질의 유형에 맞는 hit 분포가 나옴
- 이상: dense/lexical이 모두 0이거나 BM25-only 우회 상태가 지속됨

다음 액션:
- `apps/core/retrieval.py::dense_retrieve_hybrid_multi()`
- `apps/core/rag_collection_retrieval.py`

### Step 5. Evidence 확인

확인 로그:
- `RAG.RESULT.TOP`
- `RAG.CONTEXT`
- `RAG.CTX`

확인 필드:
- `canonical_evidence`
- `render_profile`
- `output_type`

정상 / 이상 판정:
- 정상: `pjt_id`/`pjt_no`, 기관 역할 의미, context shape가 유지됨
- 이상: canonicalization 또는 render profile이 retrieval semantics를 깨뜨림

다음 액션:
- `apps/core/canonical_evidence.py`
- `apps/api/services/rag_result_assembly.py`
- `apps/api/services/context_build_policy.py`

### Step 6. Answer / Streaming 확인

확인 로그:
- `LLM.RESULT`
- `STREAM.DONE`

확인 필드:
- `ttft_any_ms`
- `ttft_content_ms`
- direct-answer mode의 단일 stream 여부

정상 / 이상 판정:
- 정상: retrieval correctness를 유지한 채 answer/stream이 이어짐
- 이상: reasoning chunk 누출, 중복 stream, 늦은 relay가 발생함

다음 액션:
- `apps/api/routes.py`
- `apps/api/services/answer_generation.py`
- `apps/api/services/answer_merge.py`

---

## 2) Observability 최소 스키마

요청 단위에서는 `trace_id` 또는 `request_id` 기준으로 아래 이벤트를 따라갑니다.

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

`REQ.SUMMARY` 해석 원칙:
- `mode`, `relation`, `target_cols`는 execution strategy 기준으로 봅니다.
- `strategy_source=execution_strategy`가 정상 기본 경로이고, `planner_fallback`은 strategy 부재 시에만 허용되는 보조 경로입니다.
- `planner_mode`, `planner_relation`, `planner_target_cols`는 planner/question layer drift 진단용 보조 필드입니다.

`RAG.CONTEXT` / `RAG.CTX` 해석 원칙:
- `execution_mode`, `execution_base_route`, `execution_output_type`는 execution-layer context assembly 기준입니다.
- `strategy_source=execution_request`는 planner artifact가 아니라 execution request 기준으로 context가 조립됐다는 뜻입니다.

### 디버그(`RAG_LOG_LEVEL=debug`)

- `RAG.STRATEGY.DIFF.*`
- `RAG.PLAN.MODE_CONFLICT`
- `RAG.PLAN.JOIN_EXECUTED`
- `RAG.FILTER.COMPILED.QDRANT`
- `RAG.JOIN.HOP1.TOP`
- `RAG.JOIN.HOP2.TOP`
- `RAG.MERGED_RRF.TOP`
- `STREAM.DONE`

### trace(`RAG_LOG_LEVEL=trace`)

- 점수 분포
- 세부 payload 직렬화
- 계약 위반 / filter miss / 이상치 추적용 샘플 로그

---

## 3) 코드 버전 불일치 점검

확인 로그:
- `CODE.FINGERPRINT`
- `REQ.START`
- `REQ.END`
- `REQ.ERROR`

확인 필드:
- `app_main_sha256`
- `rag_pipeline_sha256`

정상 / 이상 판정:
- 정상: 요청 단위 이벤트와 startup fingerprint가 일치함
- 이상: 같은 시각 인스턴스 간 fingerprint가 다름

다음 액션:
- 롤링 배포 불일치 또는 핫패치 흔적을 의심합니다.

---

## 4) Stagewise Planner 점검

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
- `PLANNER.IDS_MAP.INVALID_VALUE`

### 중단 / 롤백 신호

- `PLANNER_ACTION_MODE_MISMATCH`
- `PLANNER_PARSE_FINAL_FAILED`
- `PLANNER_JOIN_FIELDS_MISSING`
- `RAG_EMPTY_RESULT_CONTRACT`
- `mode=JOIN`인데 `docs_found=0`

### triage 절차

- Stage 1: `action`, `head`, `relation_candidate`, `referential_followup`, `confidence`
- Deterministic gate: `mode`, `relation`, `join_key_mode`, `target_cols`, `output_type`
- Stage 2: `ids_map`, `filters`, `retrieval_query`, `limit`
- Assemble 이후: planner merge가 전략 의미를 바꾸지 않았는지 확인
- Runtime prelude: strict contract gate에서 fail-close가 나는지 확인

---

## 5) 자주 보는 장애 패턴

### (A) JOIN인데 결과가 0

증상:
- `JOIN_KEYS_MISSING` 또는 `mode=join`인데 `docs_found=0`

우선 확인:
- `PLANNER.IDS_MAP.INVALID_VALUE`에 사람 이름이나 주제 토큰이 `pjt_id`/`person_no`로 들어갔는지
- `PLANNER.REGATE`가 `sanitized_instance_seed_missing` 또는 `sanitized_group_seed_missing`를 남겼는지
- 질의가 `과제번호`처럼 모호한 project key 표현만 포함하는지

판정:
- `과제번호`만 있고 `pjt_id`/`pjt_no` 라벨이 없으면 seed를 확정하지 않는 것이 정상입니다. 이 경우 strict JOIN을 강행하지 않고 `lookup` + followup 경로로 남겨야 합니다.
- stage2가 `AI_SEMICONDUCTOR_2023` 같은 토큰을 `pjt_id`로 넣더라도 sanitize 뒤 제거되면, assemble 단계에서 `lookup`으로 다시 낮아져야 합니다.

다음 액션:
- `apps/core/query_intent.py`에서 라벨 기반 추출 규칙 확인
- `apps/api/contracts/runtime_contracts.py::sanitize_ids_map_semantics()` 확인
- `apps/api/services/planner_runtime.py::assemble_question_analysis()`의 post-sanitize re-gate 확인

### (B) LOOKUP/JOIN 0건인데 에러인지 정상 no-result인지 헷갈림

증상:
- `docs_found=0`, `canonical_evidence_found=0`
- summary에는 `contract_fail_reason=no_reranked`가 있는데 route 오류는 없거나, 반대로 route 오류로만 보임

우선 확인:
- `REQ.SUMMARY.contract_fail_reason`
- `REQ.SUMMARY.empty_result_policy`
- `REQ.ERROR.contract_fail_reason`
- `REQ.ERROR.empty_result_policy`
- `REQ.ERROR.reranked_count`

판정:
- `lookup`/`join`에서 `reason=no_reranked`이면 현재 baseline은 `empty_result_policy=normal_no_result`가 정상입니다. 이 경우 route exception이 아니라 deterministic no-result 답변으로 내려와야 합니다.
- `search`에서 0건은 여전히 `strict_search` 예외 경로가 정상입니다.

다음 액션:
- `apps/core/result_contract.py`에서 mode별 empty-result 정책 확인
- `apps/api/services/rag_retriever.py`의 no-result 메시지 short-circuit 확인
- `apps/api/services/answer_generation.py`의 no-result short-circuit 확인

### (B) SEARCH인데 결과가 지나치게 좁아짐

증상:
- broad query인데 결과가 지나치게 적음

확인 로그:
- `RAG.FILTER.COMPILED.QDRANT`
- `RAG.COL.RETRIEVE`

확인 필드:
- compile 결과의 must / should 구조
- `search_filter_enabled`

정상 / 이상 판정:
- 정상: SEARCH는 recall 우선이며 과도한 must gate가 없음
- 이상: SEARCH인데 lookup성 hard must가 들어감

다음 액션:
- SEARCH hard filter 금지 규칙이 validator에서 유지되는지 확인합니다.

### (C) LOOKUP인데 결과가 너무 넓거나 엉뚱함

증상:
- LOOKUP인데 결과가 넓거나 엉뚱함

확인 로그:
- `RAG.FILTER.COMPILED.QDRANT`
- `RAG.RESULT.TOP`

확인 필드:
- `ids_map`
- compile된 filter
- `lookup_filter_policy`
- `lookup_filter_min_should`

정상 / 이상 판정:
- 정상: identifier 또는 제약 필드가 filter에 정확히 반영됨
- 이상: lookup 제약이 누락되거나 search처럼 동작함

다음 액션:
- planner payload와 compiled filter 사이에서 누락된 constraint를 추적합니다.

### (D) Evidence는 맞는데 답변이 이상함

증상:
- retrieval 결과는 맞는데 답변 문장이나 follow-up이 이상함

확인 로그:
- `RAG.CONTEXT`
- `RAG.CTX`
- `LLM.RESULT`

확인 필드:
- canonical evidence 내용
- render profile
- answer generation이 `question_analysis`보다 canonical evidence / render profile / strategy를 우선하는지
- retrieval query가 이상하면 `knowledge_sufficiency.retrieval_query -> normalized_intent.retrieval_query -> question_analysis.retrieval_query` 우선순위가 지켜지는지

정상 / 이상 판정:
- 정상: answer는 canonical evidence / render profile 기준으로 생성됨
- 이상: planner artifact나 history만 보고 답변 방향이 바뀜

다음 액션:
- history는 UX 맥락으로만 보고, retrieval anchor 판단은 canonical evidence / render profile 기준으로 다시 확인합니다.

---

## 6) 장애 대응 체크리스트

```txt
[ ] query / request_id 확보
[ ] normalized_intent 확인
[ ] strategy(mode/action/relation/join_key_mode) 확인
[ ] ids_map(pjt_id vs pjt_no XOR) 확인
[ ] compile 결과(qdrant_filter must/should/min_should) 확인
[ ] retrieval hit 분포(dense/lexical/collection) 확인
[ ] canonical evidence 확인
[ ] render profile / output_type 확인
[ ] contract_fail_reason / error_code 기록
[ ] 재현용 최소 입력(query + env) 정리
```

---

## 7) 추가 메모

- `RAG.ORG.MATCH.POLICY`, `RAG.SERVER_FILTER.ORG_GATE`, `RAG.JOIN.HOP2.ORG_GATE.SKIP` 로그로 기관 필터 정책을 추적합니다.
- title 필터는 hard gate가 아니라 soft rerank로만 동작해야 합니다.
- conversation cache는 `history`, `last_canonical_evidence`, `last_render_profile`을 저장할 수 있습니다.
- retrieval anchor 판단은 history가 아니라 canonical evidence / render profile를 우선 기준으로 봅니다.
- `FILTER_MISS_SUSPECTED`는 raw payload의 `prtcp_mp[]`를 직접 읽어 확인 가능한 경우에만 신뢰합니다.
- `prtcp_mp_hm_nm` 같은 집계/flatten preview 필드는 운영 관측용일 뿐, 사람 이름 filter miss 판정 근거로 쓰면 안 됩니다.

## Empty Result / JOIN Triage
- `RAG_EMPTY_RESULT_CONTRACT`가 발생하면 `info.contract_fail_reason`, `info.empty_result_policy`, `info.reranked_count`를 먼저 확인한다. 현재 정책은 `lookup/join`은 `fail_close`, `search`는 `strict_search`로 기록된다.
- `JOIN_KEYS_MISSING`는 planner가 관계형 질의로 해석했지만 Hop1에서 `pjt_id`/`pjt_no`를 만들지 못한 경우다. seed 없는 `JOIN(instance)`는 merge 단계에서 `lookup`으로 downgrade되므로, 여전히 이 오류가 나면 Hop1 filter 또는 key extraction 경로를 우선 본다.
