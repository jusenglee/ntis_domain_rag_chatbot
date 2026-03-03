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
- [ ] join_key_mode=instance면 pjt_id만 허용(pjt_no 금지)
- [ ] join_key_mode=group이면 pjt_no가 필수(입력 단계), hop2 실행엔 pjt_no 또는 pjt_id 확장 결과 중 하나는 반드시 존재

---

## 3) 테스트 자동화(권장)
추천 구현:
- `tests/test_contract_golden.py`를 추가해서,
  - intent 추출 → normalize_intent → `_select_mode_policy` 결과를 assert 한다.
  - (추가로) `validate_planner_contract()` 위반이 없는지 assert 한다.

> 실제 retrieval(Qdrant) 없이도 “전략 결정/계약 위반”은 충분히 회귀 테스트 가능합니다.



## Streaming Invariants (Solar vLLM)

### GT-SOLAR-STREAM-001: Reasoning-first stream must not be treated as dead
- Preconditions
  - vLLM Solar streaming에서 reasoning 토큰이 먼저 출력되고 content가 뒤늦게 출력되는 모델/설정
- Expected
  - `ttft_any_ms`는 수 초 이내로 기록된다(스트림 시작 확인)
  - `ttft_content_ms`는 늦게 기록될 수 있으나, content가 나오면 최종 응답이 정상 생성된다
  - 사용자 출력에는 reasoning이 포함되지 않는다
- Assert
  - `reasoning_chars > 0`
  - `content_chars > 0` (정상 케이스)
  - `/query/stream` SSE payload에 reasoning chunk가 포함되지 않음

### GT-SOLAR-STREAM-002: Content never appears → fallback/empty handling
- Preconditions
  - 스트림에서 reasoning만 나오고 content가 끝까지 나오지 않는 케이스
- Expected
  - EmptyStreamContentError → fallback policy에 따라 non-stream 대체 또는 에러 처리
  - “안내문만 단독 반환”은 금지
- Assert
  - `stream_content_emitted_chunks == 0`
  - `fallback_used == True` (`allow_empty_stream_fallback=True`일 때)
  - 최종 `final_text`가 빈 문자열이 아님
