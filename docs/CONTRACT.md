# CONTRACT — Strategy/Planner/Executor 불변 계약

> 이 문서는 `NTIS_RAG_Search_Strategy_v1_1.md`(설계 원문) + 실제 코드의 “강제 지점”을 합쳐서,
> 구현/운영에서 흔들리지 않게 만든 **실행 가능한 계약(Contract)** 입니다.

---

## 1) 절대 불변 원칙(Non‑negotiable)

### 1.1 Planner 단일 Strategy 원칙
- 플래너는 질의마다 **단 하나의 Strategy**를 확정한다.
- 실행 레이어는 Strategy를 **재해석/재결정하지 않는다.**
- 실행 레이어가 할 수 있는 건 오직 **compile(strategy) → 실행** 뿐이다.

코드 근거(예시):
- mode/action 일치성 검증: `rag_parts/planner_contract.py::planner_contract_mode()`
- 실행 직전 계약 위반 수집: `rag_parts/planner_contract.py::validate_planner_contract()`
- 결과 계약 위반 시 예외: `rag_parts/result_contract.py::enforce_reranked_contract()`

---

## 2) Strategy 데이터 모델(내부 표준)

### 2.1 StrategySpec(실행 단계 스냅샷)
|필드(StrategySpec)|타입|
|---|---|
|mode|str|
|action|str|
|relation|Optional[Tuple[str, str]]|
|join_key_mode|Optional[str]|
|people_terms|Tuple[str, ...]|
|target_collections|Tuple[str, ...]|
|search_filter_enabled|bool|
|lookup_filter_enabled|bool|
|relation_lookup_enforce|bool|
|lookup_filter_policy|Optional[str]|
|lookup_title_filter_policy|Optional[str]|
|title_match_mode|Optional[str]|


### 2.2 NormalizedIntent(의도 분석/정규화 산출물)
|필드(NormalizedIntent)|타입|
|---|---|
|action|str|
|base_route|str|
|relation|Optional[Tuple[str, str]]|
|is_id_query|bool|
|mode|Optional[str]|
|output_type|Optional[str]|
|join_key_mode|Optional[Literal['instance', 'group']]|
|planner_limit|Optional[int]|
|retrieval_query|Optional[str]|
|planner_confidence|Optional[float]|
|people_terms|List[str]|
|org_terms|List[str]|
|lead_org_terms|List[str]|
|participant_org_terms|List[str]|
|people_affiliation_org_terms|List[str]|
|perf_types|List[str]|
|title|List[str]|
|tag_filters|List[str]|
|ids_map|Dict[str, List[str]]|
|ids_flat|List[str]|
|lookup_filter_policy|Optional[str]|


> **권장**: 운영 로그/트레이스에 `NormalizedIntent`와 `StrategySpec`를 “그대로” 남기면,
> 왜 SEARCH였는지/왜 JOIN이었는지/왜 필터가 이렇게 되었는지를 재현하기 쉬워집니다.

---

## 3) Mode 계약(SEARCH / LOOKUP / JOIN)

### 3.1 SEARCH (탐색형)
목표: **누락 방지**. 후보군을 넓게 확보하고, 소프트 랭킹/보너스로 정렬한다.

금지:
- server-side must를 만들지 말 것(특히 사람/기관 이름 hard filter)
- JOIN relation 없이 JOIN 모드를 선택하지 말 것

허용:
- `must_not`로 명백한 제외
- 태그/사람/기관은 rerank signal(soft)로 사용

관련 코드:
- 모드/액션 불일치 검출: `planner_contract_mode()`
- SEARCH filter on/off는 컴파일 단계에서만 결정: `StrategyCompiler.compile()`

### 3.2 LOOKUP (정확형)
목표: **정확성**. 서버단 필터로 후보를 먼저 좁힌다.

우선순위(권장):
1) pjt_id (instance)  
2) pjt_no (group)  
3) 성과 식별자(doi/issn/patent_no/rst_id 등)

주의:
- 사람 이름만 있는 LOOKUP은 should + min_should 게이트를 권장(오염 방지)

관련 코드:
- lookup filter 정책/타이틀 정책 정규화: `normalize_lookup_filter_policy()`, `normalize_lookup_title_filter_policy()`
- title hard filter는 detail lookup에서만 허용(컴파일러에서 soft로 강등 가능): `StrategyCompiler.compile()`

### 3.3 JOIN (관계형 2-hop)
목표: 관계 오염 차단을 위해 **server-side must 필터가 필수**.

필수:
- mode=join이면 relation 필수
- join_key_mode는 `instance|group` 중 하나

관련 코드:
- join_without_relation 검출: `planner_contract_mode()`
- relation/head/target_cols 일치성 검증: `validate_planner_contract()`

---

## 4) JOIN key 계약(가장 많이 터지는 지점)

### 4.1 XOR 규칙 (pjt_id vs pjt_no)
- lookup/join에서 `ids_map.pjt_id`와 `ids_map.pjt_no` **동시 입력 금지**
- 이건 “정확도” 문제가 아니라 **의미가 달라서** 계약 위반이다.

관련 코드:
- `rag_parts/planner_contract.py::validate_planner_contract()` → `PLANNER_MIXED_PROJECT_KEYS`
- `rag_parts/filters.py::validate_planner_join_keys()` → ValueError(`PLANNER_MIXED_PROJECT_KEYS`)

### 4.2 join_key_mode 규칙
- join_key_mode=instance → pjt_id 필수, pjt_no 금지
- join_key_mode=group → pjt_no 필수, pjt_id 금지(입력 단계)

관련 코드:
- planner 단계 위반: `PLANNER_JOIN_KEY_MODE_IDS_MISMATCH`
- executor 단계 위반: `validate_resolved_join_keys()`, `validate_join_mode_key_inputs()`

---

## 5) 컴파일(compile) 규칙 — “변경이 아니라 변환”
컴파일러는 의미를 바꾸지 말고 실행 가능한 스펙으로 **변환**만 한다.

허용:
- `filter_spec(JSON)` → Qdrant Filter 변환(중첩 포함)
- topK/limit 적용
- rerank preset 적용

금지:
- mode 변경(SEARCH↔LOOKUP↔JOIN)
- join_key_mode 변경
- SEARCH에서 must 생성
- 하이브리드 비활성화(BM25-only 등)

관련 코드:
- `rag_parts/planner_contract.py::StrategyCompiler.compile()`

---

## 6) 공통 에러 코드(StrategyViolation)
|에러코드|주 사용 위치(파일)|
|---|---|
|EXECUTOR_JOIN_KEY_INPUT_INVALID|rag_parts/filters.py|
|EXECUTOR_JOIN_KEY_MODE_INVALID|rag_parts/filters.py|
|JOIN_GROUP_KEYS_UNRESOLVED|rag_pipeline.py|
|JOIN_KEYS_INVALID|rag_pipeline.py|
|PLANNER_INTENT_PAYLOAD_INVALID|rag_pipeline.py|
|PLANNER_INTENT_PAYLOAD_REQUIRED|rag_pipeline.py|
|PLANNER_INVALID_STRATEGY|rag_pipeline.py|
|PLANNER_JOIN_HOP1_COLLECTION_MISMATCH|rag_pipeline.py|
|PLANNER_JOIN_HOP2_COLLECTION_MISMATCH|rag_pipeline.py|
|PLANNER_JOIN_KEY_MODE_EXECUTION_MISMATCH|rag_pipeline.py|
|PLANNER_JOIN_KEY_MODE_IDS_MISMATCH|rag_parts/planner_contract.py, rag_pipeline.py|
|PLANNER_JOIN_KEY_MODE_INVALID|rag_pipeline.py|
|PLANNER_JOIN_RELATION_HEAD_TARGET_MISMATCH|rag_parts/planner_contract.py|
|PLANNER_JOIN_RELATION_UNRESOLVED|rag_parts/planner_contract.py, rag_pipeline.py|
|PLANNER_MIXED_PROJECT_KEYS|rag_parts/planner_contract.py, rag_pipeline.py|
|PLANNER_PARSE_FINAL_FAILED|server3.py|
|PLANNER_PEOPLE_RELATION_FORBIDDEN|rag_pipeline.py|
|PLANNER_TARGET_COLS_ALLOWLIST_VIOLATION|rag_pipeline.py|
|RAG_EMPTY_RESULT_CONTRACT|rag_parts/result_contract.py|
|STRATEGY_MISMATCH|rag_pipeline.py|


> 참고: 일부 규칙은 `ValueError("PLANNER_...")` 같은 형태로도 발생합니다.  
> 운영/테스트에서는 “문자열 코드”도 표준화해 로그에 남기도록 권장합니다.

---

## 7) 구현 체크리스트(문서↔코드 동기화)
- [ ] `NTIS_RAG_Search_Strategy_v1_1.md`의 규칙이 코드의 단일 지점에서 강제되는가?
- [ ] JOIN key 관련 에러가 “조용히 fallback” 되지 않고, 명시적으로 드러나는가?
- [ ] SEARCH에서 사람/기관이 must로 들어가면 즉시 계약 위반으로 실패하는가?
- [ ] 결과가 0일 때(혹은 점수가 낮을 때) 어떤 정책으로 fallback 하는지 명문화돼 있는가?



## 8) LLM 스트리밍 출력 계약

- 청크 파싱은 `message.content` + `message.additional_kwargs.stream_field`를 기준으로 한다.
- `stream_field=reasoning` 청크는 사용자 응답으로 emit/append하지 않고, `reasoning_chars`로만 집계한다.
- `stream_field=content` 또는 `None`만 최종 응답에 포함한다.
- TTFT는 두 축으로 기록한다.
  - `ttft_any_ms`: reasoning 포함 첫 청크
  - `ttft_content_ms`: 실제 사용자 content 첫 청크
  - 하위 호환을 위해 `ttft_ms`는 `ttft_content_ms`와 동일 의미로 유지한다.
- deadline 초과여도 `stream_content_emitted_chunks == 0`이면 fallback(완성 응답) 라우팅을 허용한다.
- truncation 안내문은 “실제 content 일부가 스트리밍된 경우”에만 부착하며, fallback으로 완성 응답을 얻은 경우에는 부착하지 않는다.


## Streaming Contract (vLLM / OpenAI-Compatible)

### Invariant: User-facing output MUST be "content-only"
- vLLM Solar 스트리밍에서 `choices[0].delta.reasoning` (또는 `reasoning_content`)가 먼저 출력될 수 있다.
- **사용자에게 노출되는 텍스트는 반드시 `choices[0].delta.content`로부터만 구성**되어야 한다.
- reasoning 텍스트는 어떤 경우에도 사용자 출력에 포함하지 않는다.

### Invariant: Stream field tagging
- 스트리밍 chunk는 `additional_kwargs["stream_field"]`로 유형을 태깅해야 한다.
  - `"reasoning"`: keepalive/관측용 (final_text 누적 금지)
  - `"content"`: 최종 답변 텍스트 (final_text 누적 허용)

### Invariant: EmptyStream 판단 기준
- 스트리밍에서 `"content"`가 한 번도 나오지 않으면 **EmptyStreamContentError**로 간주한다.
  - reasoning만 나온 것은 “최종답변 없음”으로 실패 처리한다.

### Invariant: TTFT metrics are split
- `ttft_any_ms`: reasoning 포함 “첫 텍스트(chunk)” 기준
- `ttft_content_ms`: `content`가 처음 나타난 시점 기준
- 운영/트리아지는 `ttft_any_ms`(스트림 정지 여부)와 `ttft_content_ms`(content 지연)로 분리 판단한다.

### Forbidden: Exposing reasoning to clients
- `/query/stream` 등 사용자 스트림 응답에서 `stream_field=="reasoning"`인 chunk는 반드시 drop 한다.
