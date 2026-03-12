# GOLDEN TESTS — 계약(Contract) 회귀용 질의 셋

> 목적: “정답 문장”을 고정하지 말고, **전략/계약 불변성(invariants)** 을 고정한다.  
> 즉, 아래 테스트는 “결과가 무엇이냐”보다 **mode / relation / join_key_mode / ids_map / 필터 게이트**가 유지되는지를 본다.

---

## 1) 기본 골든 케이스(초기)
아래 표는 현재 코드(`rag_parts/query_intent.py` + `rag_pipeline._select_mode_policy`) 기준으로 산출한 “초기 골든”입니다.

|ID|질의|action|relation|expect mode|mode reason|join_key_mode|ids_map(요약)|
|---|---|---|---|---|---|---|---|
|G001|AI 관련 과제|topic|None|search|topic_search||{}|
|G002|양자컴퓨팅 기술 동향 보고서|topic|None|search|topic_search||{}|
|G003|1711015550 과제 상세|id_exact|None|lookup|id_or_exact|instance|{"pjt_id": ["1711015550"]}|
|G004|과제번호: PJT-2020-1234-5678 상세|detail|('project', 'perf')|join|relation_ids|group|{"pjt_no": ["PJT-2020-1234-5678"], "issn": ["2020-1234"]}|
|G005|김재수 참여 과제|list|None|lookup|people_org_name_lookup||{}|
|G006|ETRI 수행 과제|content|None|lookup|people_org_name_lookup||{}|
|G007|삼성 참여 과제|list|None|lookup|people_org_name_lookup||{}|
|G008|1711015550 성과 목록|list|('project', 'perf')|join|relation_ids|instance|{"pjt_id": ["1711015550"]}|
|G009|PJT-2020-XXXX 성과 전체|relation|('project', 'perf')|join|relation_action|group|{"pjt_no": ["PJT-2020-XXXX"]}|
|G010|논문 DOI 10.1000/xyz123 관련 과제|relation|('project', 'perf')|join|relation_action||{"doi": ["10.1000/xyz123"]}|
|G011|지원사업 공고 목록|list|None|lookup|people_org_name_lookup||{}|
|G012|과학기술 학술정보서비스의 연계 및 융합에 관한 연구 논문은 어느 과제에 포함되어있는지?|relation|('perf', 'project')|join|relation_action|instance|{}|


> 주의: 데이터/인덱스 상태에 따라 “검색 결과”는 변해도 되지만, **전략 결정(mode reason)과 계약 위반 여부는 안정적이어야** 합니다.

---

## 2) 우리가 실제로 고정해야 하는 invariants(체크리스트)

### SEARCH invariants
- [ ] SEARCH에서 사람/기관 이름이 server-side must로 컴파일되지 않는다.
- [ ] SEARCH는 후보(topK)를 넓게 확보하고, 태그/이름은 rerank signal로 처리한다.

### LOOKUP invariants
- [ ] ID 기반이면 server-side must로 좁힌다.
- [ ] 이름 기반(사람/기관)은 should + min_should 게이트가 적용된다(정책에 따라).

### JOIN invariants
- [ ] relation이 있으면 mode는 join이 된다(특히 action=relation).
- [ ] JOIN에서는 head == relation target(두 번째 엔티티) 이어야 한다.
- [ ] join_key_mode=instance면 pjt_no 금지, pjt_id는 선택(Hop1 source 추출 경로 허용)
- [ ] join_key_mode=group이면 pjt_no가 필수(입력 단계), hop2 실행엔 pjt_no 또는 pjt_id 확장 결과 중 하나는 반드시 존재
- [ ] JOIN Hop2는 org/title gate 없이 join key 필터만 사용한다.

---

## 3) 테스트 자동화(권장)
추천 구현:
- `tests/test_contract_golden.py`를 추가해서,
  - intent 추출 → normalize_intent → `_select_mode_policy` 결과를 assert 한다.
  - (추가로) `validate_planner_contract()` 위반이 없는지 assert 한다.

> 실제 retrieval(Qdrant) 없이도 “전략 결정/계약 위반”은 충분히 회귀 테스트 가능합니다.



---

## 4) LLM 스트리밍 계약(invariants) 골든
목표: “모델이 뭘 답했냐”가 아니라, **스트리밍 출력 계약이 깨지지 않는지**를 회귀로 잡는다.

### Streaming invariants
- [ ] `stream_field=reasoning` 청크는 최종 사용자 응답 문자열에 절대 포함되지 않는다(집계만).
- [ ] `ttft_any_ms`는 “첫 청크(=reasoning 포함)” 기준, `ttft_content_ms`는 “첫 content 청크” 기준으로 기록된다.
- [ ] deadline/char limit 발생 시에도, `stream_content_emitted_chunks == 0`로 fallback한 경우에는 “부분 반환 안내문”이 붙지 않는다.
- [ ] `stream_field=reasoning` 이고 `message.content==""`인 케이스에서 reasoning 텍스트가 별도 필드로 전달되더라도(`additional_kwargs.reasoning_text`) `reasoning_chars`가 정상 집계된다.

### 추천 테스트(최소)
- `tests/test_llm_streaming.py`
  - reasoning 필터링 + ttft_any/ttft_content 메트릭 검증
  - deadline 후 fallback 시 안내문 부착 금지 검증
- (추가 권장) `tests/test_openai_compat_reasoning_chunk_contract.py`
  - openai_compat_llm가 reasoning을 content에 섞지 않는지(=SSE/최종 문자열 오염 방지)

---

## 5) 정책 축/질의 축 분리 매트릭스 (신규)

### 정책 축(Policy Axis)
- `strict`: planner 계약 위반 시 `StrategyViolation`으로 즉시 실패(fail-close).
- `planner_invalid_fallback`: planner 계약 위반 시 lookup/search로 보정(fail-open compat).
- `promotion_mode`: 후처리 승격 모드(`search` → `lookup`/`join`) 추적.
- `force_fallback_chat`: 강제 fallback 응답 정책 적용 여부.

### 질의 축(Query Axis)
- ID 질의: `1711015550 과제 상세`
- relation 질의: `PJT_NO 동일과제 성과`
- people/org 혼합: `김재수 참여 과제`
- 연도/성과유형: `2021~2023 ETRI 논문 통계`
- 후속 참조형: `김재수 과제의 논문`

---

## 6) 골든 최소 세트(단계별 고정)

> 아래는 “planner output → normalized_intent → filter compile → hop1/hop2” 단계에서 회귀 고정하는 최소 스냅샷입니다.

### G-MIN-001: `1711015550 과제 상세`
- planner output: `mode=lookup`, `relation=None`, `target_cols=[ntis_project_v1]`
- normalized_intent: `action=id_exact`, `base_route=project`, `ids_map.pjt_id=[1711015550]`
- filter compile: lookup `qdrant_filter` 사용
- hop1/hop2: `None / None`

### G-MIN-002: `PJT_NO 동일과제 성과`
- planner output: `mode=join`, `relation=(project, perf)`, `target_cols=[ntis_project_v1, ntis_perf_v1]`
- normalized_intent: `action=relation`, `base_route=project`, `relation=(project, perf)`
- filter compile: `join_hop1_filter + join_filter` 사용
- hop1/hop2: `project / perf`

### G-MIN-003: `김재수 참여 과제`
- planner output: `mode=lookup`, `relation=None`, `target_cols=[ntis_project_v1]`
- normalized_intent: `action=list`, `base_route=project`, `relation=None`
- filter compile: people/org 혼합 `should + min_should` 조건 허용
- hop1/hop2: `None / None`

### G-MIN-004: `2021~2023 ETRI 논문 통계`
- planner output: `mode=lookup`, `relation=None`, `target_cols=[ntis_perf_v1]`
- normalized_intent: `action=stats`, `base_route=perf`
- filter compile: `year range + perf tag(논문)` 조건 결합
- hop1/hop2: `None / None`

### G-MIN-005: `김재수 과제의 논문`
- planner output: `mode=join`, `relation=(project, perf)`, `target_cols=[ntis_project_v1, ntis_perf_v1]`
- normalized_intent: `action=relation`, `base_route=project`, `relation=(project, perf)`
- filter compile: hop1(연구자명), hop2(논문 태그) 분리
- hop1/hop2: `project / perf`

---

## 7) 영향 범위 메모(이번 세션)
- people/org relation JOIN late guard는 기본 warning 경로가 남아 있지만, 정상 경로의 주 방어선은 upstream(`query_intent`, parser) 차단임.
- 따라서 회귀 테스트는 executor late guard 단독보다 upstream 차단 + executor 안전장치를 함께 검증해야 함.

## 8) Stagewise Planner Invariants

### G-SP-001 — stage1 출력 범위 제한
- stage1은 아래 필드만 출력해야 한다.
  - `action`
  - `head`
  - `relation_candidate`
  - `referential_followup`
  - `confidence`
- stage1은 전략 필드(`mode`, `join_key_mode`, `target_cols`, `ids_map`, `filters`)를 출력하면 안 된다.

### G-SP-002 — stage2 출력 범위 제한
- stage2는 아래 필드만 출력해야 한다.
  - `ids_map`
  - `filters`
  - `retrieval_query`
  - `limit`
  - `confidence`
- stage2는 전략 필드(`mode`, `head`, `action`, `relation`, `join_key_mode`, `target_cols`, `strategy_version`)를 출력하면 안 된다.

### G-SP-003 — action 우선 invariant
- `action=topic`이면 final `mode=SEARCH`
- `action in {list,detail,stats,download}`이면 기본 final `mode=LOOKUP`
- JOIN은 relation candidate + join seed가 있는 경우에만 허용

### G-SP-004 — broad perf topic blind JOIN 금지
질의:
- `스마트 제조 관련 특허 성과`
기대:
- final `head=perf`
- final `action=topic`
- final `relation=null`
- blind `JOIN` 금지

### G-SP-005 — 기관/사람 텍스트 ids_map 오염 금지
질의:
- `ETRI 수행 과제`
- `김재수 참여 과제`
기대:
- `ids_map`는 비워도 됨
- 각각 `filters.lead_org_name=["ETRI"]`, `filters.participant_researcher_name=["김재수"]`
- `ids_map.pjt_id=["ETRI"]`, `ids_map.person_no=["김재수"]` 금지

### G-SP-006 — referential follow-up + seed가 있을 때만 JOIN 허용
질의:
- `이 과제의 논문`
기대:
- `prev_context`에서 단일 `pjt_id` 또는 `pjt_no` seed가 있을 때만 `JOIN` 허용
- seed가 없으면 blind JOIN 금지

### G-SP-007 — support target invariant
질의:
- `회원가입 방법`
기대:
- final `head=support`
- support 컬렉션(`ntis_supports_v1`) 사용

### G-SP-008 — stage2 IDs는 후속 전략 재판정 후보
질의:
- stage1 시점에는 seed가 없었으나 stage2가 `doi/issn/rst_id/pjt_id/pjt_no`를 새로 추출한 케이스
기대:
- 현재 구현상 open issue로 관리하되, 향후 `post-stage2 re-gate` 대상임을 회귀 메모에 남긴다.

## 9) 이력(History)

|일자|변경유형|내용|
|---|---|---|
|2026-03-12|추가|stagewise planner invariants(G-SP-001~008) 추가|
|2026-03-09|추가|정책 축(`strict/planner_invalid_fallback/promotion_mode/force_fallback_chat`)과 질의 축 분리 정의 추가|
|2026-03-09|추가|최소 골든 5케이스(G-MIN-001~005) 단계별 기대 산출 고정|
|2026-03-09|추가|strict/compat 동일 입력 비교 테스트(`예외 vs 보정`) 회귀 기준 반영|
|2026-03-09|추가|people/org relation 금지의 영향 범위 메모(upstream 차단 우세, executor는 안전장치) 추가|
