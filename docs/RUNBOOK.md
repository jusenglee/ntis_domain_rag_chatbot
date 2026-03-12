# RUNBOOK — NTIS Domain RAG 운영/트리아지

> 목적: 장애/품질 저하 시 “촉”이 아니라 **계약(Contract) + 트레이스**로 원인을 좁히기.

---

## 1) 빠른 트리아지(3분 컷)

### Step 1 — Planner/Strategy 확인
요청 1건에 대해 아래를 확보:
- 원문 질의(query)
- `NormalizedIntent` (의도 분석 결과)
- `StrategySpec` (실행 전략 스냅샷)

체크:
- mode가 `search|lookup|join` 중 하나인가?
- join인데 relation이 비어있지 않은가?
- relation이 `project_perf|perf_project|null` 이외 값(특히 people/org 포함)으로 들어오지 않았는가?
- lookup/join인데 `ids_map.pjt_id`와 `ids_map.pjt_no`가 섞이지 않았는가?

관련 계약/코드:
- `rag_parts/planner_contract.py::planner_contract_mode()`
- `rag_parts/planner_contract.py::validate_planner_contract()`
- `rag_parts/filters.py::validate_planner_join_keys()`
- `server3.py::QuestionAnalysisV2.validate_join_contract()` (`PLANNER_RELATION_FORBIDDEN_PEOPLE_ORG`, `PLANNER_RELATION_INVALID`)

운영 기본값:
- `RAG_STRICT_STRATEGY_CONSISTENCY=1`(fail-close) 기준으로 planner 계약 위반은 기본 차단.
- 필요 시에만 명시적으로 `0`으로 내려 compat 모드로 완화.

### Step 2 — 필터 컴파일 결과 확인
- planner filter spec(JSON)이 어떤 Qdrant Filter로 컴파일됐는지 확인
- SEARCH에서 must가 생기지 않았는지 확인(특히 사람/기관)

관련 코드:
- `rag_parts/planner_contract.py::StrategyCompiler.compile()`
- `rag_parts/filters.py::compile_filter()` (filter_spec → Qdrant Filter)

### Step 3 — Retrieval hit 분포 확인
- dense hit / lexical hit이 0인지
- 컬렉션별 hit 수가 정상인지
- 하이브리드가 “사실상 꺼진” 상태인지(BM25-only 우회 등)

관련 코드:
- `retrieval.py::dense_retrieve_hybrid_multi()`

### Step 4 — rerank/결과 계약 확인
- reranked 결과가 너무 적거나 점수가 너무 낮으면 계약 실패가 난다.

관련 코드:
- `rag_parts/result_contract.py::enforce_reranked_contract()` (에러코드: `RAG_EMPTY_RESULT_CONTRACT`)

---

## 2) 관측성(로그) 최소 스키마 — 운영/디버그/포렌식 3단 분리
요청 단위 trace_id(예: request_id)로 아래 이벤트를 유지합니다.

- 운영 기본(`RAG_LOG_LEVEL=normal`):
  - `REQ.START`
  - `PLANNER.PIPELINE` (`step=parse|intent_merge|intent_build`, `status=success|retry|final_failed`)
  - `KS.RESULT`
  - `RAG.PLAN`
  - `RAG.RETRIEVE`
  - `RAG.RESULT.TOP`
  - `RAG.CONTEXT`
  - `RAG.RESULT`
  - `LLM.RESULT`
  - `REQ.SUMMARY` (`request_id/mode/relation/target_cols/docs_found/selected_model/rendered_context_used/fallback_context_used/degraded/total_ms` 요약)
  - `REQ.ERROR` (요청 실패 경로의 `error_code/reason/degraded` 기록)
  - `REQ.END`

`total_ms`는 요청 시작 시점(`query_stream()`/`query_debug()`에서 기록한 monotonic timestamp)부터 계산한 end-to-end 시간이다. `state.latencies`는 노드별 단계 진단용 지표이며, 합산값을 `total_ms`와 혼용하지 않는다.
- 디버그(`RAG_LOG_LEVEL=debug`):
  - `STREAM.DONE`(solar/gemma `ttft_any_ms`, `ttft_content_ms`, `elapsed_ms`, `content_chars`, `error_code`)
  - planner→executor diff(`RAG.STRATEGY.DIFF.*`)
  - 정책/충돌/진단 이벤트(`RAG.STRATEGY.POLICY`, `RAG.PLAN.MODE_CONFLICT`, `RAG.PLAN.JOIN_EXECUTED`, `RAG.PLAN.RELATION_LOOKUP_POLICY`)
  - compiled qdrant filter(`RAG.FILTER.COMPILED.QDRANT`)
  - hop1/hop2 topN(`RAG.JOIN.HOP1.TOP`, `RAG.JOIN.HOP2.TOP`)
  - 중간 merge 상위(`RAG.MERGED_RRF.TOP`)
- 트레이스(`RAG_LOG_LEVEL=trace`):
  - 점수 분포, 원시 페이로드 직렬화, 포렌식 샘플(계약 위반/필터 미스 의심 구간)

운영 기본 로그는 객체 전체 문자열 dump를 피하고, 필터는 카운트/적용 여부 중심으로 남깁니다.

### 운영 기본 로그 길이 축소 점검(샘플 비교 기준)
- 목적: `RAG_LOG_LEVEL=normal` 기본값에서 디버그성 이벤트(`RAG.JOIN.*`, `RAG.RERANK.*`, `RAG.TAG_FILTER.*`, `RAG.PERF_TYPES.*`)가 제거되어 요청 1건당 로그 길이가 줄었는지 확인.
- 샘플링: 동일 질의 10건(SEARCH 4, LOOKUP 3, JOIN 3)을 릴리즈 전/후로 각각 수집.
- 비교 지표(필수):
  - 요청당 로그 라인 수 중앙값(`median(lines_per_request)`) 20% 이상 감소.
  - 요청당 로그 바이트 수 중앙값(`median(bytes_per_request)`) 20% 이상 감소.
  - 운영 필수 이벤트(`RAG.STRATEGY.POLICY`, `RAG.PLAN*`, `RAG.RETRIEVE`, `RAG.RESULT.TOP|RAG.RESULT`, `RAG.CONTEXT`, `RAG.ERROR.*`) 누락 0건.
  - debug 전용 이벤트(`RAG.RESULT.TOP.DEBUG`)는 운영 필수 대상에서 제외(필요 시 debug 모드에서만 확인).
- 판정:
  - 길이 지표는 감소했지만 필수 이벤트가 누락되면 실패로 간주(관측성 회귀).
  - 길이 감소가 20% 미만이면 debug tier 분류 누락 여부를 재점검.

### normal/debug 분류표 (RAG 핵심 이벤트)

| 이벤트 | tier | 비고 |
|---|---|---|
| `RAG.PLAN` | normal | 운영 기본 화이트리스트 |
| `RAG.RETRIEVE` | normal | 운영 기본 화이트리스트 |
| `RAG.RESULT.TOP` | normal | 운영 기본 화이트리스트 |
| `RAG.CONTEXT` | normal | 운영 기본 화이트리스트 |
| `RAG.ERROR.*` | normal | 장애/실패 경로 보존 |
| `RAG.STRATEGY.POLICY` | debug | 정책 진단성 이벤트 |
| `RAG.PLAN.MODE_CONFLICT` | debug | 모드 충돌 진단 |
| `RAG.PLAN.RELATION_LOOKUP_POLICY` | debug | relation+lookup 정책 진단 |
| `RAG.PLAN.JOIN_EXECUTED` | debug | join 실행 분기 진단 |



## 코드 버전 불일치(version mismatch) 점검 절차
- 서버 시작 직후 `CODE.FINGERPRINT` 이벤트에서 `server3_sha256`, `rag_pipeline_sha256`, `stage=startup`를 확인합니다.
- 요청 단위 이벤트(`REQ.START`, `REQ.END`, `REQ.ERROR`)와 RAG 컨텍스트 이벤트(`REQ.CONTEXT`)에도 동일 fingerprint 필드가 포함되는지 확인합니다.
- 서로 다른 파드/인스턴스에서 같은 시각의 fingerprint를 비교해 해시가 다르면 롤링 배포 불일치(또는 핫패치 잔존)로 판단합니다.
- 장애 재현 시에는 `request_id`, `conversation_id`와 함께 두 해시를 티켓에 첨부해 정확히 동일 코드 기준으로 재현합니다.
- 해시 재계산은 프로세스 시작 시 1회 캐시되므로, 런타임 중 파일 변경(예: 컨테이너 내부 수동 수정)은 fingerprint에 반영되지 않습니다. 반드시 프로세스를 재기동해 다시 확인합니다.

---

## 3) 디버그를 켜는 방법(권장)
### 3.1 RAG 내부 로그 레벨
- `RAG_LOG_LEVEL=normal|debug|trace` (기본: `normal`)
- 하위 호환: `RAG_DEBUG=1`이면 `debug`로 승격
- TopN 출력은 기본 `debug` tier에서만 활성화

예:
```bash
export RAG_LOG_LEVEL=debug
```

### 3.2 결과 계약 실패 시 fallback 정책

### 3.3 결과 TopN 로그(2단계)
- normal tier
  - 이벤트: `RAG.RESULT.TOP`
  - 개수: `RAG_LOG_TOPN_NORMAL` (기본 3)
- debug tier
  - 이벤트: `RAG.RESULT.TOP.DEBUG`
  - 개수: `RAG_LOG_TOPN_DEBUG` (기본 12, 권장 10 이상)
- 하위호환(legacy)
  - `RAG_LOG_TOPN_FINAL`은 normal 키가 없을 때만 사용
  - 우선순위: `RAG_LOG_TOPN_NORMAL` > `RAG_LOG_TOPN_FINAL` > 기본값(3)

예:
```bash
export RAG_LOG_LEVEL=debug
export RAG_LOG_TOPN_NORMAL=3
export RAG_LOG_TOPN_DEBUG=12
```
- `RAG_FORCE_FALLBACK_CHAT=true`면 계약 실패를 예외로 던지지 않고 reason을 반환(운영 정책용)
- 스트리밍 실패(`EmptyStreamContentError`, `stream_content_emitted_chunks == 0`) 시에는 non-stream 재시도를 하지 않는다. 장애 판정은 `ttft_any_ms`, `ttft_content_ms`, `deadline_exceeded`, `stream_content_emitted_chunks` 조합으로 수행한다.

### 3.4 Planner stagewise 부팅 확인

배포 후 부팅 로그에서 아래 키를 운영 로그 이벤트 기준으로 확인합니다.

- 이벤트: `APP.CONFIG`
- 키: `planner_stagewise_enabled`
- 기대값: `1`

예시 쿼리(로그 시스템에서 JSON 필드 검색):

```sql
SELECT ts, event, planner_stagewise_enabled
FROM app_logs
WHERE event = 'APP.CONFIG'
  AND planner_stagewise_enabled = 1
ORDER BY ts DESC
LIMIT 100;
```

보조 확인 이벤트:
- `PLANNER.PIPELINE`
- `PLANNER.ASSEMBLE`

위 이벤트에서도 `planner_stagewise_enabled=1`이 일관되게 관측되어야 합니다.

### 3.5 Stagewise Planner 전환 RUNBOOK

#### 1) 배경
- planner 내부 생성 경로가 one-shot giant prompt에서 stagewise(stage1 분류 → deterministic gate → stage2 슬롯 추출 → assemble)로 확장되었다.
- stagewise는 feature flag(`PLANNER_STAGEWISE_ENABLED`) 뒤에서 운영할 수 있다.

#### 2) 필수 환경 변수
```bash
PLANNER_STAGEWISE_ENABLED=true
PLANNER_STAGE1_PROMPT_VERSION=v1
PLANNER_STAGE2_PROMPT_VERSION=v1
PLANNER_TEMPERATURE=0.0
```

#### 3) 기대 관측성(정상 시)
요청 1건에 대해 아래 로그가 순서대로 보여야 한다.
- `PLANNER.STAGE1`
- `PLANNER.GATE`
- `PLANNER.STAGE2`
- `PLANNER.ASSEMBLE`

추가 진단 로그:
- `PLANNER.IDS_MAP.INVALID_VALUE`

#### 4) 핵심 장애 신호
아래 중 하나라도 증가하면 canary를 중지하거나 즉시 rollback 검토:
- `PLANNER_ACTION_MODE_MISMATCH`
- `PLANNER_PARSE_FINAL_FAILED`
- `RAG_EMPTY_RESULT_CONTRACT`
- `mode=JOIN` 이면서 `docs_found=0`
- `PLANNER.IDS_MAP.INVALID_VALUE` 급증

#### 5) stagewise triage 절차
##### Step A — stage1 확인
- `action`, `head`, `relation_candidate`, `referential_followup`, `confidence`를 확인
- broad topic인데 relation_candidate가 붙었는지 점검
- 사람/기관 질의에서 head가 `people/org`로 잘못 붙지 않았는지 확인

##### Step B — deterministic gate 확인
- stage1 결과 대비 최종 `mode / relation / join_key_mode / target_cols`가 어떻게 잠겼는지 확인
- `action=topic`인데 `mode=LOOKUP` 또는 `action=list`인데 `mode=SEARCH`로 잠겼다면 gate/후단 보정 충돌을 의심

##### Step C — stage2 슬롯 확인
- `ids_map`에 사람명/기관명이 들어갔는지 확인
- 기관 role(`lead_org_name / participant_org_name / people_affiliation_org_name`)이 올바른지 확인
- `retrieval_query`가 locked strategy와 충돌하지 않는지 확인

##### Step D — assemble 이후 후단 재실패 확인
- `PLANNER.ASSEMBLE` 이후에도 `apply_planner_strategy()`에서 `PLANNER_ACTION_MODE_MISMATCH`가 나는지 확인
- 이 경우 stagewise 결과가 old strict merge layer에 의해 재검증/재실패하는 구조 문제로 분류

##### Step E — post-stage2 re-gate 동작 확인
- stage2가 `pjt_id/pjt_no/doi/rst_id/patent_*`를 새로 뽑았을 때 `PLANNER.REGATE`의 `regate_eligible`, `regate_changed`, `before_*`, `after_*`를 함께 확인
- `regate_eligible=1`인데도 기대한 JOIN 전환이 일어나지 않으면, 구현 부재가 아니라 gate 조건/seed mapping/회귀 버그로 분류

#### 6) rollback 기준
아래 중 하나라도 만족하면 우선 `RAG_PLANNER_PIPELINE=legacy` 또는 `PLANNER_STAGEWISE_ENABLED=false`를 검토:
- canary 대비 `PLANNER_ACTION_MODE_MISMATCH` 증가
- `RAG_EMPTY_RESULT_CONTRACT` 증가
- 평균 planner latency 50% 이상 증가
- `mode=JOIN & docs_found=0` 빈도 급증

#### 7) 현재 알려진 운영 갭
- 기본 경로는 `RAG_PLANNER_PIPELINE=staged`이지만, 배포 설정이 분산돼 있으면 legacy fallback 경로가 혼입될 수 있다.
- `PLANNER.REGATE`는 구현돼 있으므로, 현재 이슈는 “부재”보다 “조건/효과 검증 부족”에 가깝다.
- stagewise assembled 결과가 이후 strict merge layer와 충돌하지 않도록 회귀 테스트를 유지해야 한다.

---

## 4) 자주 터지는 패턴과 처방

### (A) JOIN인데 결과가 0 (또는 hop2=0)
우선 `RAG.JOIN.HEAD.SEMANTICS`에서 `planner_head == expected_head`와 `target_cols` 정합성을 먼저 확인합니다.
원인 후보:
- join_key_mode/ids_map 불일치 (instance인데 pjt_no만 있음 등)
- hop2 filter가 잘못된 key(pjt_id vs pjt_no)를 must로 만들었음
- hop1에서 키 확장 실패

즉시 확인:
- `validate_planner_contract()` 위반 목록
- `validate_resolved_join_keys()` / `validate_group_join_runtime_keys()`
- hop1/hop2 filter key
- `RAG.JOIN.GROUP.RESOLVE` 로그의 `resolved_pjt_ids_count`(group Hop1에서 pjt_id 해석 성공 여부)
- `RAG.JOIN.GROUP.RESOLVE` 로그의 `resolve_input_count`, `resolve_keep_effective`(resolve 입력 규모/보정 keep 확인)

처방:
- JOIN gate 규칙을 CONTRACT에 명문화하고, 위반 시 “조용한 fallback” 금지
- hop1 확장 결과(키 리스트)를 로그에 남기기
- group JOIN은 seed `pjt_no`를 계약 키로 유지하고, Hop1(project)에서 `resolved_pjt_ids`를 확보한 뒤 Hop2(perf) fallback에 사용

튜닝 가이드(resolve ON):
- `RAG_JOIN_GROUP_RESOLVE_PROJECT_IDS=1`일 때 `RAG_JOIN_GROUP_RESOLVE_KEEP`를 `30~100` 범위에서 시작해 조정합니다.
- 최종 resolve 입력 수는 `resolve_keep_effective = max(RAG_HOP1_KEEP, min(RAG_JOIN_GROUP_RESOLVE_MAX_IDS, RAG_JOIN_GROUP_RESOLVE_KEEP))`이며, 실제 투입량은 `resolve_input_count`로 확인합니다.
- 경고: `RAG_JOIN_GROUP_RESOLVE_MAX_IDS`만 증가시키면 `available_hop1_points` 또는 `resolve_keep_effective`가 병목인 경우 효과가 제한될 수 있습니다.
- `resolved_pjt_ids_count`가 지속적으로 0에 가까우면 `RAG_JOIN_GROUP_RESOLVE_KEEP` 또는 `RAG_JOIN_GROUP_RESOLVE_TOPK`를 단계적으로 상향합니다.
- 점검 로그 키: `resolved_pjt_ids_count`, `resolve_input_count`를 함께 확인해 resolve 투입 대비 해석 효율을 판단합니다.

### (B) SEARCH인데 결과가 엉뚱하게 좁아짐
원인 후보:
- SEARCH에서 must 조건이 생김(특히 사람/기관)
- filter_signal이 너무 공격적으로 켜짐

즉시 확인:
- compile 결과에서 must/should 구조
- `search_filter_enabled`가 켜진 근거(filter_conf_ok 등)

처방:
- SEARCH hard filter 금지 규칙을 validator에서 강제(실패 처리)

### (C) LOOKUP인데 결과가 과하게 넓음/오염됨
원인 후보:
- ID 기반인데 ID filter가 누락
- 이름 기반인데 min_should gate가 없음

즉시 확인:
- ids_map 및 compile된 filter
- `lookup_filter_policy`, `lookup_filter_min_should`

처방:
- LOOKUP 정책을 `hard` 또는 `must_one_then_should`로 고정(운영 정책)


### (D) LLM 스트리밍에서 content가 늦게 나오거나(또는 비어보이는) 현상 — vLLM reasoning 계열
증상:
- `ttft_any_ms`는 존재하지만 `ttft_content_ms`가 `None`이거나 매우 큼
- `reasoning_chars > 0` 인데 `content_chars == 0` 또는 `stream_content_emitted_chunks == 0`
- 로그에서 `content_delayed` / `empty_stream_content` / `deadline_exceeded`가 함께 보일 수 있음

즉시 확인:
- (서버) vLLM이 reasoning parser를 켠 모델인지 확인(`--reasoning-parser ...`). reasoning 모델은 스트리밍에서 reasoning이 먼저 나올 수 있음.
- (클라이언트/래퍼) Answer 경로에서 `reasoning_effort="low"`, `include_reasoning=false`를 요청 단위로 강제했는지 확인.
- (파서/메트릭) `stream_field=reasoning` 청크를 최종 답변에 append하지 않는지, 그리고 reasoning 텍스트가 `message.content`가 아니라 별도 필드(`additional_kwargs.reasoning_text`)로 올 때도 집계되는지 확인.
- (SSE) `stream_field in {None,"content"}`만 브라우저로 emit하고 있는지 확인(=reasoning이 사용자 출력에 섞이면 안 됨).

처방:
- Answer(Solar) 경로: thinking/reason 출력은 기본 OFF(요청 단위로 `reasoning_effort=low`, `include_reasoning=false`, `chat_template_kwargs.enable_thinking=false` 권장).
- Planner 경로: 필요 시 요청 단위로 thinking ON(`reasoning_effort=high` 등)하되, 최종 출력(JSON)이 깨지지 않도록 reasoning은 별도 필드로만 처리.

---

## 5) 장애 대응 체크리스트(복붙)
```txt
[ ] query / hint / request_id 확보
[ ] normalized_intent 출력 확보
[ ] strategy(mode/action/relation/join_key_mode) 확인
[ ] ids_map(pjt_id vs pjt_no XOR) 확인
[ ] compile 결과(qdrant_filter must/should/min_should) 확인
[ ] retrieval hit 분포(dense/lexical/collection별) 확인
[ ] group JOIN 시 `resolved_pjt_ids_count`가 `hop1_keep` 근처에서 고정되는지 확인(상한 병목 여부 점검)
[ ] rerank score(avg/max) 확인
[ ] contract_fail_reason / error_code 기록
[ ] 재현용 최소 입력(질의+hint+env) 정리
```


## 기관/제목 필터 운영 관측성
- `RAG.ORG.MATCH.POLICY`: 기관 필터 확장 정책 로그(prefix_len/min_len/terms preview).
- `RAG.SERVER_FILTER.ORG_GATE`: LOOKUP에서 planner_org_filter_present=true 이면서 `lookup_filter_enabled=false`인 경우에만 gate 적용.
- `RAG.JOIN.HOP2.ORG_GATE.SKIP`: JOIN Hop2(perf)에서 org gate를 의도적으로 생략했는지 확인.
- 제목 필터는 server-side 하드게이트를 사용하지 않고 soft rerank로만 동작한다.

## fastembed(BM25) 부팅 워밍업
- 기본값으로 서버 시작 시 sparse encoder(`Qdrant/bm25`)를 1회 warmup 하여 첫 요청 지연을 완화한다.
- 환경변수 `RAG_FASTEMBED_WARMUP_ON_BOOT=false`로 비활성화 가능.
- warmup 로그 키:
  - 성공: `[retrieval] sparse encoder warmup success: model=...`
  - 실패: `[retrieval] sparse encoder warmup failed: ...`

---

## 전략 불변성 정합성 트리아지 (신규)

증상:
- 같은 질의에서 strict/compat 실행 결과가 다름(예: strict는 예외, compat는 lookup/search 보정).
- planner 출력과 최종 실행 스냅샷의 `mode/relation/join_key_mode/target_cols`가 단계 중간에 바뀜.

즉시 확인:
- `policy_mode`, `planner_invalid_fallback`, `strict_strategy_consistency`, `promotion_mode`, `force_fallback_chat`
- `strategy_mutation_stage`, `changed_by`, `changed_strategy_fields`, `changed_filter_fields`
- stagewise planner 사용 시 `PLANNER.GATE`(초기 판정)와 `PLANNER.REGATE`(stage2 seed 반영 후 재판정)의 `before_*`/`after_*` 및 `regate_changed`를 함께 확인

판단 가이드:
- 문서상 기대(strict): 계약 위반 시 즉시 `StrategyViolation`
- 현재 운영 기본값: staged 경로에서는 invalid fallback과 promotion 재실행을 비활성화하고, 계약 위반은 `StrategyViolation`으로 종료하는 방향이다.

처방 우선순위:
1) 관측성 고정(로그 키 표준화)
2) 기본값 정합화(`RAG_PLANNER_PIPELINE=staged`, `RAG_PLANNER_INVALID_FALLBACK=0`)
3) 전략 재작성 지점 단일화(parser/validator/normalizer 정리)

## openai_compat_llm request_id 점검 샘플
운영 중 `openai_compat_llm` 로거에서 request_id/conversation_id 전파 여부를 빠르게 확인할 때 아래 포맷을 기준으로 점검합니다.

```text
[openai_compat_llm] non-stream summary: request_id=<conversation_id>-<suffix> conversation_id=<conversation_id> model=<model_name> dt_ms=<...> message_n=<...> content_char_n=<...> reasoning_char_n=<...> usage=<...> base_url=<...>
```

- 정상 예시: `request_id=6f7f7d6c-6f0d-4c8c-9f5d-31f9b189d7a9-2a4f1c3e conversation_id=6f7f7d6c-6f0d-4c8c-9f5d-31f9b189d7a9`
- 비정상 예시: `request_id=` (빈 문자열)

## FILTER_MISS_SUSPECTED 진단 포인트

`FILTER_MISS_SUSPECTED` 발생 시 아래 필드로 필터 스키마 불일치를 우선 진단합니다.

- `values`, `topN`, `matched`: 탐지 기본 지표
- `probe_docs[].doc_id`, `probe_docs[].title`: 상위 문서 식별
- `probe_docs[].prtcp_mp_hm_nm`, `probe_docs[].prtcp_mp_blng_org_nm`: flatten된 참여연구자/소속기관 값

점검 순서:
1) `prtcp_mp[].hm_nm`와 `prtcp_mp.hm_nm` 혼용 여부 확인
2) 필터 입력값(`values`)이 topN 원문 데이터(`probe_docs`)와 실제로 맞는지 확인
3) lookup/hydrate 단계의 payload 키 경로를 동일 규약으로 통일

## Planner latency 튜닝 운영 가이드

1. **기본값 권장**
   - `PLANNER_DISABLE_THINKING=true`
   - `PLANNER_TIMEOUT_MS=4500`
   - `PLANNER_V2_RETRY_ATTEMPTS=1`, `PLANNER_V2_RETRY_ATTEMPTS_MAX=2`
2. **지연 증가 시 조정 순서**
   - (1) `timeout_ms` 대비 `final_failed` 비율 확인
   - (2) timeout 여유가 충분하면 `PLANNER_TIMEOUT_MS`를 500ms 단위로 상향
   - (3) 파싱 실패가 간헐적이면 `PLANNER_V2_RETRY_ATTEMPTS`를 2로 상향(상한은 `..._MAX`)
3. **관측 필드(고정)**
   - `PLANNER.PIPELINE`에서 `timeout_ms`, `disable_thinking`, `attempt`, `backoff_sec`를 표준 필드로 수집
   - 성공 이벤트에도 `attempt`, `backoff_sec=0.0`을 남겨 샘플링 편향을 제거

## 이벤트 스키마 (Planner/Intent 단일 체계)

| event | step | status | 필수 필드 | 설명 |
|---|---|---|---|---|
| `PLANNER.PIPELINE` | `parse` | `success` | `request_id`, `conversation_id`, `mode`, `head`, `action`, `relation`, `planner_retry_count`, `timeout_ms`, `disable_thinking`, `attempt`, `backoff_sec` | planner 파싱 성공 이벤트 |
| `PLANNER.PIPELINE` | `parse` | `retry` | `attempt`, `max_attempts`, `retry`, `timeout_ms`, `disable_thinking`, `backoff_sec`, `error_type` | planner 파싱 실패 후 재시도 이벤트 |
| `PLANNER.PIPELINE` | `parse` | `final_failed` | `attempt`, `max_attempts`, `planner_fallback`, `timeout_ms`, `disable_thinking`, `backoff_sec`, `error_type`, `error` | planner 파싱 최종 실패 이벤트 |
| `PLANNER.PIPELINE` | `intent_merge` | `success` | `applied`, `changed_strategy_fields`, `changed_filter_fields`, `changed_by` | planner→intent 전략 병합 diff 이벤트 |
| `PLANNER.PIPELINE` | `intent_build` | `success` | `planner_applied`, `planner_failed`, `schema_fields` | 최종 intent_payload.v2 빌드 이벤트 |
| `PLANNER.REGATE` | - | - | `regate_eligible`, `regate_changed`, `before_mode`, `after_mode`, `before_relation`, `after_relation` | stage2 신규 seed로 gate 전략을 최소 재판정한 결과 이벤트 |

## 로그 키 사전 (공통)

| 키 | 정의 | 예시 | 알람 조건 |
|---|---|---|---|
| `policy_mode` | strict/compat 정책 모드 | `strict`, `compat` | `compat` 비율 급증(일평균 대비 +20%p) |
| `execution_mode` | 실행 전략 모드 | `search`, `lookup`, `join` | 특정 모드 편향(예: `lookup` 90%+) |
| `planner_invalid_fallback` | planner 무효 전략 fallback 허용 여부 | `0`, `1` | `1` 상태에서 `RAG.PLAN.FALLBACK_ON_INVALID_PLANNER` 급증 |
| `strict_strategy_consistency` | 전략 불일치 시 fail-fast 여부 | `0`, `1` | `0` 상태에서 mismatch 누적 증가 |
| `promotion_mode` | 최종 rerank/contract 적용 모드 | `search`, `lookup`, `join` | staged 경로에서는 promotion이 강제 비활성화되므로 legacy/experimental 경로에서만 해석 |
| `force_fallback_chat` | 결과 계약 실패를 chat fallback으로 전환 | `0`, `1` | `1` 상태에서 계약 실패(reason) 증가 |
| `strategy_mutation_stage` | 전략 변형 발생 단계 | `parser`, `validator`, `normalizer`, `planner_merge`, `executor` | `validator`/`executor` 단계 변형 급증 |
| `changed_by` | 변형 주체 태그 | `parser` 등 | 특정 주체의 변형 비율 급증 |
| `rendered_context_used` | 최종 답변 생성 시 렌더링 컨텍스트(문서/메모리)가 실제 프롬프트에 포함되었는지 | `0`, `1` | `docs_found > 0`인데 `0` 비율 급증 시 문맥 렌더링 실패 의심 |
| `fallback_context_used` | 최종 merge 시점 기준, 문서 컨텍스트가 없고 fallback_context만 남아 fallback 경로를 사용했는지 | `0`, `1` | `1` 비율 급증 시 검색 미스 증가/프롬프트 fallback 경로 점검 |
| `degraded` | 정책 위반 또는 생성 실패로 저하 응답(예: StrategyViolation 안내문, 듀얼모델 공통 fallback 문구) 여부 | `0`, `1` | `1` 급증 시 `REQ.ERROR`/`LLM.RESULT`의 사유와 동시 확인 |

### 변형 추적 필드
- `changed_strategy_fields`: mode/action/relation/join_key_mode/target_cols 등의 전략 변경 필드.
- `changed_filter_fields`: ids_map/filter_spec 계열 변경 필드.
- 각 변경 필드에는 `changed_by`를 포함해 단계 추적 가능.

### 운영 집계 쿼리 예시 (일 단위)

```sql
-- strict/compat 비율 (일 단위)
SELECT
  DATE(ts) AS d,
  SUM(CASE WHEN policy_mode = 'strict' THEN 1 ELSE 0 END) AS strict_cnt,
  SUM(CASE WHEN policy_mode = 'compat' THEN 1 ELSE 0 END) AS compat_cnt,
  ROUND(100.0 * SUM(CASE WHEN policy_mode = 'strict' THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 2) AS strict_ratio,
  ROUND(100.0 * SUM(CASE WHEN policy_mode = 'compat' THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 2) AS compat_ratio
FROM rag_logs
WHERE event IN ('RAG.STRATEGY.POLICY', 'PLANNER.PIPELINE')
GROUP BY DATE(ts)
ORDER BY d DESC;
```

```sql
-- 전략 변형 발생률 + 단계별 분포 (일 단위)
SELECT
  DATE(ts) AS d,
  COUNT(*) AS total_events,
  SUM(CASE WHEN JSON_LENGTH(changed_strategy_fields) > 0 OR JSON_LENGTH(changed_filter_fields) > 0 THEN 1 ELSE 0 END) AS mutated_events,
  ROUND(100.0 * SUM(CASE WHEN JSON_LENGTH(changed_strategy_fields) > 0 OR JSON_LENGTH(changed_filter_fields) > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 2) AS mutation_rate,
  SUM(CASE WHEN changed_by = 'parser' THEN 1 ELSE 0 END) AS by_parser,
  SUM(CASE WHEN changed_by = 'validator' THEN 1 ELSE 0 END) AS by_validator,
  SUM(CASE WHEN changed_by = 'normalizer' THEN 1 ELSE 0 END) AS by_normalizer,
  SUM(CASE WHEN changed_by = 'planner_merge' THEN 1 ELSE 0 END) AS by_planner_merge,
  SUM(CASE WHEN changed_by = 'executor' THEN 1 ELSE 0 END) AS by_executor
FROM rag_logs
WHERE event IN ('PLANNER.PIPELINE', 'RAG.STRATEGY.DIFF.PLANNER_TO_CONTEXT', 'RAG.STRATEGY.DIFF.PLAN_TO_EXECUTION_CONTEXT')
  AND (step = 'intent_merge' OR step IS NULL)
GROUP BY DATE(ts)
ORDER BY d DESC;
```
