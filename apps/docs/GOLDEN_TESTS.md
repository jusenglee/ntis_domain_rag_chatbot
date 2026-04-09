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
- expected keep: source reference axis preserved, canonical/reference-context owner wins when evidence exists
- expected fallback: if canonical/reference-context owner returns `missing_context|none`, display snapshot ordinal may seed the follow-up while keeping `followup_reference_kind=source_reference`

### 11A. Freshness/new-data cue forces fresh retrieval
- query: `그 과제 최신 정보`
- expected mode: retrieval required
- expected keep: knowledge sufficiency LLM returns `prefer_fresh_retrieval=true`, and the retrieval query uses the current question while preserving any active anchor seed
- expected ban: stale `prev_context` only 답변, stale detail cache hit, anchor-only exact lookup 고정

### 11B. Cache-backed detail follow-up still re-probes freshness
- setup: previous turn answered from detail cache so `prev_context=[]`, but active anchor/view_state remains
- query: `그 과제 최신 정보`
- expected keep: knowledge sufficiency still calls the LLM freshness judgment and can emit `prefer_fresh_retrieval=true`
- expected ban: `detail` action early-exit that skips the freshness probe

### 11C. Broad-history deterministic repair keeps semantic cue
- query: `2024 신동구(한국과학기술정보연구원) 연구자의 활동이력`
- expected keep: deterministic repair preserves people/org/year filters and keeps the semantic cue `활동이력` in `retrieval_query`
- expected ban: immediate raw-query fallback that drops the broad-history cue

### 11D. Unique child-subject list follow-up stays on child axis
- query chain:
  - `반도체 관련 과제 3건`
  - `2번 과제 연구자`
  - `해당 연구자의 다른 활동은?`
- expected keep: people list result promotes a stable child anchor when visible items collapse to one logical subject
- expected keep: deictic follow-up reuses that child anchor and preserves the `people` axis
- expected ban: clarification caused only by duplicate rows for the same logical subject

### 11E. Explicit project id does not collapse broad child-subject query to project detail
- query: `김봉준 (PJT_ID=1711135956) 의 다른 활동을 보여줘`
- expected keep: planner still runs because the query is broad-history/child-subject scoped
- expected keep: explicit `pjt_id` remains context seed only
- expected ban: `resolved_retrieval_query=1711135956` exact-detail collapse unless the final contract is truly `detail`
### 11F. Detail child subject survives Redis/session roundtrip
- query chain:
  - `3D 반도체 공급망 강화를 위한 실증 기반구축 과제의 상세정보를 알려줘`
  - `해당 과제에 참여한 최성순 연구자가 다른 과제에도 참여한 이력이 있는지?`
- expected keep: detail turn persists child subject truth into `subject_index` and the next turn resolves the named subject after session-memory reload
- expected keep: `conversation:v2:{cid}:view_state` roundtrip preserves `active_scope`, `visible_answer_manifest`, `subject_index`
- expected keep: old unversioned Redis key alone does not restore session truth
- expected ban: answer-visible child name disappearing just because per-person exact id was absent in the previous detail row

### 11G. Visible answer manifest owns ordinal truth
- setup: raw retrieval snapshot order and final visible answer order differ
- query chain:
  - list turn with grounded supported answer
  - `2번째 과제의 연구자는 누구야?`
- expected keep: ordinal follow-up seeds from `visible_answer_manifest`, not raw retrieval wrapper order
- expected ban: unsupported list answer updating visible ordinal truth for the next turn

### 11I. Answer-state consistency guard blocks wrong visible order
- setup: `active_scope.result_set`는 A -> B 순서인데 한 모델 answer는 A -> Wrong, 다른 모델 answer는 A -> B
- expected keep: state-consistent model만 선택된다.
- expected keep: selected answer meta에 `answer_state_consistency_status=supported`, `visible_answer_manifest_status=approved`가 남는다.
- expected ban: groundedness만 맞는 잘못된 visible order answer가 ordinal truth를 덮어쓰는 것

### 11J. Both models state-inconsistent fall back and clear manifest
- setup: 두 모델 answer 모두 현재 `active_scope.result_set`와 item order/title이 다르다.
- expected keep: `selection_reason=both_models_state_inconsistent`
- expected keep: `visible_answer_manifest_status=blocked_state_consistency`
- expected keep: current turn 이후 `visible_answer_manifest`는 비워진다.
- expected ban: blocked turn 뒤 `2번째`, `출처 2`, `그 항목` follow-up이 stale manifest로 resolve되는 것

### 11H. Single provisional child subject continues, multi provisional subjects clarify
- query: named child subject follow-up from project detail where child refs have names only
- expected keep: one provisional `people | org | perf` subject resolves as `child_entity_followup`
- expected keep: two or more same-kind provisional candidates raise `clarification_required`
- expected ban: unconditional fresh search or unconditional clarification for every name-only child follow-up

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
- passive groundedness verdict must emit `unsupported_project_id`, `unsupported_perf_id`, `unsupported_year`, `unsupported_org_name`, `unsupported_count` for clear structured claims.
- list/relation/comparison/series/stats turn에서는 groundedness와 `answer-state consistency`가 둘 다 통과해야만 `visible_answer_manifest`가 승인된다.

### 19. No detail answer for broad people/org query
- broad people/org query must not collapse into a fake single-detail answer.

## Streaming reliability watchlist
### 20. async close handled correctly
- no `AsyncStream.close was never awaited`

### 21. emitted chunks recognized
- chunks arriving should not end with emitted_chunks=0 unless truly empty.

### 22. TTFT deadline not tripped by parser bug
- if text chunks arrive, ttft should be set.
