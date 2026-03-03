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
- lookup/join인데 `ids_map.pjt_id`와 `ids_map.pjt_no`가 섞이지 않았는가?

관련 계약/코드:
- `rag_parts/planner_contract.py::planner_contract_mode()`
- `rag_parts/planner_contract.py::validate_planner_contract()`
- `rag_parts/filters.py::validate_planner_join_keys()`

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

## 2) 관측성(로그) 최소 스키마 — “이거 없으면 디버깅이 안 됨”
요청 단위 trace_id(예: request_id)로 아래를 한 덩어리로 남기세요.

- input:
  - query
  - hint(있다면)
- planner:
  - normalized_intent
  - strategy(mode/action/relation/join_key_mode/ids_map 요약)
  - planner_confidence
- compile:
  - qdrant_filter 요약(must/should/min_should)
  - hop1/hop2 filter(Join이면)
  - topK/limit
- retrieval:
  - collection별 hit count
  - dense/lexical 각각 hit, top score
  - latency
- rerank:
  - rerank preset
  - final_keep, 상위 N의 _final_total 통계(avg/max)
- output:
  - 반환 문서 수
  - 컨텍스트 토큰/문자
  - LLM finish_reason(스트리밍 이슈 추적)
  - 스트리밍 메트릭: `ttft_any_ms`, `ttft_content_ms`, `ttft_ms(=content)`, `reasoning_chars`, `content_chars`, `stream_content_emitted_chunks`, `deadline_exceeded`, `char_limited`
  - `fallback_used=false`, `fallback_emit_mode=None`는 하위 호환용 고정 메트릭(점진 제거 예정)

---

## 3) 디버그를 켜는 방법(권장)
### 3.1 RAG 내부 디버그
- `RAG_DEBUG_LEVEL` / `RAG_DEBUG_TOPN` / `RAG_DEBUG_MAX_KWS`

`rag_parts/debug.py`의 설명:
- 0: off
- 1: 핵심 결정/요약
- 2: retrieve 요약
- 3: 매우 자세히

예:
```bash
export RAG_DEBUG_LEVEL=2
export RAG_DEBUG_TOPN=5
```

### 3.2 결과 계약 실패 시 fallback 정책
- `RAG_FORCE_FALLBACK_CHAT=true`면 계약 실패를 예외로 던지지 않고 reason을 반환(운영 정책용)

---

## 4) 자주 터지는 패턴과 처방

### (A) JOIN인데 결과가 0 (또는 hop2=0)
원인 후보:
- join_key_mode/ids_map 불일치 (instance인데 pjt_no만 있음 등)
- hop2 filter가 잘못된 key(pjt_id vs pjt_no)를 must로 만들었음
- hop1에서 키 확장 실패

즉시 확인:
- `validate_planner_contract()` 위반 목록
- `validate_resolved_join_keys()` / `validate_group_join_runtime_keys()`
- hop1/hop2 filter key

처방:
- JOIN gate 규칙을 CONTRACT에 명문화하고, 위반 시 “조용한 fallback” 금지
- hop1 확장 결과(키 리스트)를 로그에 남기기

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

---

## 5) 장애 대응 체크리스트(복붙)
```txt
[ ] query / hint / request_id 확보
[ ] normalized_intent 출력 확보
[ ] strategy(mode/action/relation/join_key_mode) 확인
[ ] ids_map(pjt_id vs pjt_no XOR) 확인
[ ] compile 결과(qdrant_filter must/should/min_should) 확인
[ ] retrieval hit 분포(dense/lexical/collection별) 확인
[ ] rerank score(avg/max) 확인
[ ] contract_fail_reason / error_code 기록
[ ] 재현용 최소 입력(질의+hint+env) 정리
```

