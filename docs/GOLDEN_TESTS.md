# GOLDEN_TESTS.md

## Purpose
이 문서는 SEARCH / LOOKUP / JOIN 계약과 answer-stage groundedness 회귀를 잡기 위한 골든 테스트 초안이다.

## Contract tests
### 1. SEARCH stays SEARCH
- query: `AI 관련 과제`
- expected mode: `SEARCH`
- expected ban: 사람/기관 hard must 없음

### 2. People query is LOOKUP
- query: `김재수 참여 과제`
- expected mode: `LOOKUP`
- expected filter: `participant_researcher_name`
- expected gate: `min_should=1`
- expected ban: `SEARCH`

### 3. Org query is LOOKUP
- query: `ETRI 수행 과제`
- expected mode: `LOOKUP`
- expected filter: `lead_org_name`

### 4. Project detail is LOOKUP
- query: `1711015550 과제 상세`
- expected mode: `LOOKUP`
- expected ids_map: `pjt_id`

### 5. Project to perf instance join
- query: `1711015550 성과`
- expected mode: `JOIN`
- expected relation: `project_perf`
- expected join_key_mode: `instance`
- expected ids_map key: `pjt_id`

### 6. Project to perf group join
- query: `PJT-2020-XXXX 성과 전체`
- expected mode: `JOIN`
- expected relation: `project_perf`
- expected join_key_mode: `group`
- expected ids_map key: `pjt_no`

### 7. Stats query stays LOOKUP
- query: `2021~2023 ETRI 논문 통계`
- expected mode: `LOOKUP`
- expected action: `stats`

### 8. Parenthesized broad history keeps people/org axis
- query: `신동구(한국과학기술정보연구원) 연구자의 활동이력 5건`
- expected mode: `LOOKUP`
- expected filter: `participant_researcher_name + people_affiliation_org_name`
- expected ban: perf detail 축 추가 금지

### 9. Quoted title detail keeps title axis
- query: `'단일 반도체물질 기반 3진 논리 게이트 개발' 과제 상세정보`
- expected mode: `LOOKUP`
- expected output: `detail`
- expected keep: quoted title phrase preserved in retrieval query

### 10. Ordinal follow-up keeps anchor
- query: `2번 과제의 상세정보`
- expected mode: `LOOKUP`
- expected keep: follow-up anchor preserved

### 11. Source reference follow-up keeps reference axis
- query: `출처 2의 연구자 정보`
- expected mode: follow-up resolution dependent
- expected keep: source reference axis preserved
## Safety / contract bans
### 12. No fallback chat
- when retrieval is weak, system must not silently switch to fallback chat mode.

### 13. No strategy rewrite downstream
- executor / answer stage must not rewrite planner mode/relation/target_cols.

### 14. No BM25-only shortcut in LOOKUP/JOIN
- LOOKUP/JOIN path must keep hybrid retrieval.

### 15. No mixed join keys
- `pjt_id` and `pjt_no` must not be mixed in one JOIN plan.

### 16. No people-name must in SEARCH
- person/org token in SEARCH can be bonus only, not hard must.

## Answer quality tests
### 17. Weak evidence => conservative answer
- answer must separate confirmed facts / unknowns / what more is needed.

### 18. No unsupported IDs or dates
- answer must not invent pjt_id, perf_id, year, org, count.

### 19. No detail answer for broad people/org query
- broad people/org query must not collapse into a fake single-detail answer.

## Streaming reliability watchlist
### 20. async close handled correctly
- no `AsyncStream.close was never awaited`

### 21. emitted chunks recognized
- chunks arriving should not end with emitted_chunks=0 unless truly empty.

### 22. TTFT deadline not tripped by parser bug
- if text chunks arrive, ttft should be set.
