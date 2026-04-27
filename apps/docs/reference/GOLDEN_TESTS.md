# GOLDEN_TESTS.md

이 문서는 골든 시나리오 카탈로그다. 현재 실행 가능한 검증 명령의 source of truth는 `../04_회귀기준과_점검.md`다.

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

### 6A. Explicit PJT_ID stays on the instance axis
- query: `PJT_ID=1711015550 과제 상세`
- expected keep: `ids_map.pjt_id`
- expected ban: `ids_map.pjt_no`

### 6B. Explicit PJT_NO stays on the group axis
- query: `PJT_NO=PJT-2020-XXXX 과제 성과`
- expected keep: `ids_map.pjt_no`
- expected ban: `ids_map.pjt_id`

### 6C. Unknown project-key alias does not auto-resolve
- query: `RJT_ID=RJT-2024-001 과제 상세`
- expected keep: unsupported alias is surfaced as invalid/clarify-safe input
- expected ban: auto-mapping `RJT_ID` to `pjt_id` or `pjt_no`

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
- expected keep: `conversation:v3:{cid}:session_memory.current_context` roundtrip preserves the official next-turn truth
- expected keep: legacy `conversation:v2:{cid}:history|last_canonical_evidence|last_render_profile|view_state` keys alone do not restore session truth
- expected ban: answer-visible child name disappearing just because per-person exact id was absent in the previous detail row

### 11G. Visible answer manifest owns ordinal truth
- setup: raw retrieval snapshot order and final visible answer order differ
- query chain:
  - list turn with grounded supported answer
  - `2번째 과제의 연구자는 누구야?`
- expected keep: ordinal follow-up seeds from `visible_answer_manifest`, not raw retrieval wrapper order
- expected ban: unsupported or `withheld_partial` list answer updating visible ordinal truth for the next turn

### 11H. Trigger LLM classifies follow-up, but policy gate owns reuse rights
- setup: previous turn state exists in `ConversationViewState.last_query_contract`
- expected keep: the trigger may emit `fresh | followup | ambiguous`, but only the deterministic turn policy may choose `reuse_manifest | reuse_anchor | fresh_retrieval | clarification`
- expected keep: `answer_publishability=publishable` plus manifest presence are both required before ordinal/source follow-up can reuse prior visible truth
- expected ban: trigger-only `followup` classification directly reusing stale manifest without checking previous publishability

### 11H-1. Candidate builder and interpreter stay bounded by view state
- setup: `ConversationViewState` already contains `visible_answer_manifest`, `active_scope`, `subject_index`, and `recent_mentions`
- expected keep: turn candidates are built only from those state objects and carry stable `candidate_id`, ids, display name, source, and ranking hints
- expected keep: the interpreter may rewrite user intent, but `selected_candidate_ids` must only reference provided candidates
- expected ban: interpreter inventing a new subject/id outside the candidate list

### 11H-2. Hard signal still wins before interpreter
- query: `출처 2 보여줘`, `2번째 과제 자세히`, `방금 본 논문만`
- expected keep: source/ordinal/guarded relative references resolve through deterministic hard-signal handling before ambiguous LLM interpretation
- expected keep: `최근 3년 과제` is treated as a temporal filter, not a relative-last follow-up
- expected ban: hard-signal wording being reinterpreted into a different candidate by the interpreter

### 11H-3. Recent mentions recover project follow-up without changing planner strategy ownership
- setup: `ConversationViewState.recent_mentions` contains retrieval-owned project mentions but there is no reusable manifest/focus snapshot
- query chain:
  - summary or list-like project response
  - `마지막 과제 상세`
- expected keep: recent mention recovery restores only the project anchor seed
- expected keep: `strategy_meta.context_router_*` records the rescue path when router fallback is needed
- expected ban: router choosing `SEARCH/LOOKUP/JOIN`, `relation`, `target_cols`, or `join_key_mode`

### 11H-4. Year-axis follow-up uses project recent mentions conservatively
- setup: no active project scope, but recent project mentions exist with year metadata
- query: `2025년꺼`
- expected keep: active scope가 있으면 기존 refinement가 우선하고, active scope가 없을 때만 recent project mentions의 year 축으로 복원한다
- expected keep: latest recent project group(`pjt_no`) 안에서 단일 candidate가 있으면 그 candidate를 우선한다
- expected ban: `pjt_id` / `pjt_no` 혼용 또는 router 단계에서 group/detail semantics를 새로 발명하는 것

### 11H-5. Project detail does not materialize from group-only mention
- setup: recent project mention has `pjt_no` only and no `pjt_id`
- query: `마지막 과제 상세`
- expected keep: clarification or existing detail-fail-close path
- expected ban: recent mention만 보고 instance detail을 확정하거나 `pjt_no`를 `pjt_id`처럼 쓰는 것

### 11H-6. Named subject reseed and subject refinement are distinct
- query chain:
  - `신동구 연구자의 24년도 활동내역은?`
  - `2010~2015년도의 활동내역은?`
  - `신동구 연구자의 2010~2015년도의 활동내역은?`
  - `2번째 과제는?`
- expected keep: explicit named subject questions are `fresh_retrieval` and do not reuse stale manifest/project context
- expected keep: year-only activity follow-up keeps the current people/org subject as a refinement seed, not as ordinal/source reference
- expected keep: people 질문의 후보 필터는 people 후보만 보며, people 후보가 없으면 clarification 후보를 비워 둔다
- expected keep: ordinal/source references such as `2번째 과제` still use visible manifest truth only when publishable
- expected ban: people 질문이 people 후보 부재 시 project 후보 예시로 fail-open 되는 것
- expected ban: named subject reseed가 trigger LLM 또는 previous publishability/followup_rights 때문에 reference follow-up으로 되돌아가는 것

### 11I. Answer-state consistency guard blocks wrong visible order
- setup: `active_scope.result_set`는 A -> B 순서인데 한 모델 answer는 A -> Wrong, 다른 모델 answer는 A -> B
- expected keep: state-consistent model만 선택된다.
- expected keep: selected answer meta에 `answer_state_consistency_status=supported`, `visible_answer_manifest_status=approved`가 남는다.
- expected ban: groundedness만 맞는 잘못된 visible order answer가 ordinal truth를 덮어쓰는 것

### 11K. Generic list may accept a prefix subset but must withhold manifest
- setup: `active_scope.result_set` is `A -> B -> C`, and the user did not ask for an explicit count such as `3건`, `상위 3건`, or `모두 3개`.
- answer: the model explains only `A -> B`, and those returned items still match the snapshot prefix identity/order.
- expected keep: the final answer is accepted instead of degrading to the state-consistency fallback.
- expected keep: selected answer meta keeps `answer_state_consistency_status=supported`, `answer_state_consistency_subset_accepted=true`, and `visible_answer_manifest_status=withheld_partial`.
- expected keep: `visible_answer_manifest_publication.publication_status=withheld_partial` and `published_manifest` is absent.
- expected ban: a partial-safe answer publishing `visible_answer_manifest` or becoming ordinal/source truth for the next turn.
- expected ban: a follow-up like `2번째`, `출처 2`, or `그 항목` reusing a withheld manifest as truth.

- expected keep: if `turn_contract.count_contract=partial_ok`, this policy wins even when the raw question text still contains count-like surface tokens.
- expected keep: if `turn_contract.count_contract=exact`, exact-count guard stays on even when the raw question text alone would not trigger the legacy regex heuristic.

### 11N. Broad-history people/org list uses snapshot repair only after state inconsistency
- setup: `active_scope.result_set.context_kind` is `people` or `org`, and the visible list already reflects the canonical activity rows that follow-up truth should reuse.
- answer: a model output that is already state-consistent is selected normally.
- expected keep: supported LLM list answers are not replaced by `deterministic_snapshot` just because the snapshot context is `people` or `org`.
- expected keep: if both model answers are state-inconsistent but a verified projection summary can be rendered, the LLM answer remains the final user-visible body and the deterministic text is exposed as `verified_projection_summary`.
- expected keep: if that verified projection summary is state-consistent and not groundedness-unsupported, `visible_answer_manifest_status=approved` with `visible_answer_manifest_publication_source=verified_projection_summary`.
- expected keep: approved publication writes `visible_answer_manifest_publication.published_manifest` and that manifest is the only answer-owned publish artifact.
- expected keep: summary or intro text is non-authoritative and must not own item count/order truth.
- expected ban: degrading broad-history people/org list answers only because the LLM collapsed multiple rows into fewer top-level bullets.
- expected ban: replacing the final user-visible body with `model_key=deterministic_snapshot`.

### 11N-2. Activity list order failure augments with snapshot-owned rendering
- setup: list-family active snapshot rows are activity-like rows carrying project, result, and actor ids such as `pjt_id`, `rst_id`, and `person_no`.
- answer: both model answers contain the right family of items but swap visible ranks or miss a tail row.
- expected keep: if projection lineage matches, answer-stage keeps the selected LLM text and attaches the deterministic visible snapshot list as `verified_projection_summary`.
- expected keep: publication status is `approved` only when the verified projection summary is snapshot-owned and supported.
- expected ban: model-generated reordered activity rows becoming `visible_answer_manifest` truth.

### 11N-3. Grouped project list count failure augments with snapshot-owned rendering
- setup: project list active snapshot has `visible_count == len(items)` and multiple visible rows share the same `pjt_no`, while each row still has its own `pjt_id`.
- answer: both model answers collapse grouped `pjt_no` rows with rank citations such as `[1, 5]`, causing row-level state consistency to report `unsupported_count`.
- expected keep: answer-stage keeps the selected LLM text but attaches the snapshot-owned row list as `verified_projection_summary`.
- expected keep: `visible_answer_manifest_publication_source=verified_projection_summary` and published manifest remain row-level `pjt_id` truth.
- expected ban: treating grouped `pjt_no` summaries as the next-turn ordinal/source truth.

### 11L. Invalid planner count contract fails closed instead of runtime overwrite
- query: explicit-count list where planner assembled `display_limit` does not match the requested count
- expected keep: `PLANNER.COUNT_CONTRACT source=planner_invalid`, `invalid_reason=explicit_count_mismatch`
- expected keep: retrieval short-circuits with `clarification_type=planner_count_contract`
- expected ban: request facade rewriting `question_analysis.limit/display_limit` from question regex or defaults

### 11M. Display canonical promotion uses planner requested_count only
- setup: `docs` is a collection wrapper, `canonical_evidence` has the real visible items, `requested_count=2`, raw question explicit-count diagnostic is larger
- expected keep: display normalization promotes canonical axis when it satisfies planner `requested_count`
- expected keep: `active_scope.result_set` is built from the promoted canonical axis, so rank/title/ids match the answer context instead of the collection wrapper text.
- expected keep: the projected item identity keeps `rank`, `entity_kind`, `canonical_title`, `ids_map`, and `identity_key` together before validator input is built.
- expected keep: when a projection bundle is present, answer references, state validation, and manifest publication use projection lineage instead of raw `retrieval_bundle.items`.
- expected keep: projection lineage mismatch yields `visible_answer_manifest_publication.publication_status=blocked_projection_lineage`.
- expected ban: collection wrapper rows owning visible rank or mixing wrapper titles with canonical item ids in state-consistency snapshots.
- expected ban: publishing a visible manifest when the active snapshot `projection_id/request_id/turn_id` does not match the current projection bundle.
- expected ban: route reconstruction resurrecting stale `view_state.visible_answer_manifest` when publication status is blocked or withheld.
- expected ban: raw question explicit-count inflating the threshold and suppressing canonical promotion

### 11J. Both models state-inconsistent fall back and clear manifest
- setup: 두 모델 answer 모두 현재 `active_scope.result_set`와 item order/title이 다르다.
- expected keep: `selection_reason=both_models_state_inconsistent`
- expected keep: `visible_answer_manifest_status=blocked_state_consistency`
- expected keep: `visible_answer_manifest_publication.publication_status=blocked_state_consistency` and no `published_manifest`.
- expected keep: 최종 사용자 answer는 state-consistency 차단을 설명하는 degraded message이며, empty-answer notice가 아니어야 한다.
- expected keep: current turn 이후 `visible_answer_manifest`는 비워진다.
- expected ban: blocked turn 뒤 `2번째`, `출처 2`, `그 항목` follow-up이 stale manifest로 resolve되는 것

### 11H. Single provisional child subject continues, multi provisional subjects clarify
- query: named child subject follow-up from project detail where child refs have names only
- expected keep: one provisional `people | org | perf` subject resolves as `child_entity_followup`
- expected keep: two or more same-kind provisional candidates raise `clarification_required`
- expected ban: unconditional fresh search or unconditional clarification for every name-only child follow-up

### 11H-3. Follow-up entity registry and candidate recall stay contract-bound
- query: people-axis follow-up when only project candidates exist
- expected keep: same-kind fail-closed clarification with no project example candidates.
- expected ban: people 질문이 project 후보로 fallback되어 project clarification을 출력하는 것
- query: more than 16 visible candidates where a named same-kind tail candidate is referenced
- expected keep: deterministic/prompt candidate selection recalls the named tail candidate before the LLM prompt limit is applied.
- expected ban: `_MAX_CANDIDATES_FOR_PROMPT`가 전체 후보 회수 한계처럼 동작하는 것
- query: single same-kind anchor candidate with no ordinal/source reference
- expected keep: `policy_source=aggressive_auto` only after candidate id validation.
- expected ban: non-publishable manifest, wrong entity kind, or unknown candidate id를 aggressive auto가 우회하는 것
- expected keep: `route_context()` is deterministic-only; constrained LLM fallback is only available through `run_context_router()`.
- expected keep: registry entries are Pydantic-validated; duplicate kind, invalid regex, unapproved keyword collision, or same-priority shared keyword fails at validation time.
- expected keep: approved shared keyword resolution follows registry priority.
- expected keep: router year matching uses registry `temporal_keys`, not a hardcoded candidate field.
- expected keep: context-router prompt requires `selected_candidate_index` to be one of the provided candidate `index` values or returns `unresolved`.

### 11H-4. SessionMemory v3 is the only persisted follow-up truth
- expected keep: load reads only `conversation:v3:{cid}:session_memory`; missing or corrupt v3 starts with `EmptyContext`.
- expected keep: save writes only v3 session memory and best-effort deletes v2 `history`, `last_canonical_evidence`, `last_render_profile`, and `view_state` keys.
- expected keep: `SubjectQueryContext` restores subject focus and refinement rights without restoring a visible manifest for ordinal/source reuse.
- expected keep: `PublishedManifestContext` is the only context that restores visible manifest ordinal/source rights.
- expected keep: `turn_trigger`, turn-interpreter prompt summaries, and `turn_policy` consume `SessionMemory.current_context` directly when present; stale `view_state.last_query_contract` cannot grant reuse rights.
- expected keep: persistence prefers explicit `next_current_context` from runtime/answer nodes and fails closed to `EmptyContext` if missing; save-time view-state inference is forbidden.
- expected ban: v2 `view_state.visible_answer_manifest` resurrecting stale ordinal/source follow-up truth.

## Safety / contract bans
### 12. No fallback chat
- when retrieval is weak, system must not silently switch to fallback chat mode.

### 13. No strategy rewrite downstream
- executor / answer stage must not rewrite planner mode/relation/target_cols.

### 14. No BM25-only shortcut in LOOKUP/JOIN
- LOOKUP/JOIN path must keep hybrid retrieval.

### 15. No mixed join keys
- `pjt_id` and `pjt_no` must not be mixed in one JOIN plan.

### 15A. No implicit project-key alias coercion
- unknown project-key aliases must not be coerced into `pjt_id` or `pjt_no` without an explicit alias contract.

### 16. No people-name must in SEARCH
- person/org token in SEARCH can be bonus only, not hard must.

## Answer quality tests
### 17. Weak evidence => conservative answer

### 18. Broad-history SEARCH may widen only the internal search preset
- setup: `RAG_ENABLE_SEARCH_POLICY_VARIANT=1`, resolved mode is `SEARCH`, `soft_strategy_hints.semantic_kind=broad_history`, and hard contract has no explicit/locked project key plus no active anchor.
- expected keep: top-level mode stays `SEARCH`.
- expected keep: runtime prelude may choose `search_policy_variant=broad_semantic`, increasing only internal preset/rerank breadth.
- expected ban: changing the top-level strategy to `LOOKUP` or `JOIN` just to widen recall.

### 19. Explicit project axis or anchor blocks broad search variant
- setup: same as above, but explicit `PJT_ID`/`PJT_NO`, resolved project-key axis, candidate project key, or previous anchor exists.
- expected keep: `search_policy_variant=standard`.
- expected ban: broad semantic widening overriding hard contract or active anchor truth.
- answer must separate confirmed facts / unknowns / what more is needed.

### 18. No unsupported IDs or dates
- answer must not invent pjt_id, perf_id, year, org, count.
- passive groundedness verdict must emit `unsupported_project_id`, `unsupported_perf_id`, `unsupported_year`, `unsupported_org_name`, `unsupported_count` for clear structured claims.
- commercial policy note: strict list/relation/comparison/series turns require groundedness plus `answer-state consistency` before manifest publication.
- commercial policy note: generic list prefix-subset answers may still be accepted, but `visible_answer_manifest_status` must stay `withheld_partial`.
- commercial policy note: `detail`, `clarification`, `no_result`, cache/direct answer, and most `stats` turns bypass the strict state-consistency gate.
- list/relation/comparison/series/stats turn에서는 groundedness와 `answer-state consistency`가 둘 다 통과해야만 `visible_answer_manifest`가 승인된다.

### 19. No detail answer for broad people/org query
- broad people/org query must not collapse into a fake single-detail answer.

### 19A. Perf SEARCH is observation-only and stays on perf
- query: `배터리 관련 성과`
- expected mode: `SEARCH`
- expected head: `perf`
- expected keep: `ExecutionManager` ownership with `policy_name=PERF_SEARCH_OBSERVATION`
- expected keep: `target_cols == [COL_PERF]`
- expected ban: automatic relax, mode promotion, legacy retry re-entry

### 19B. Instance JOIN may retry group only from active anchor pjt_no
- setup: active anchor already contains `pjt_no`
- query: project instance-key-based JOIN that returns 0 rows on the primary step
- expected keep: one bounded `group` retry only
- expected keep: `recovery_source=active_anchor`
- expected ban: invented key, free-text `pjt_no` reconstruction, second retry

### 19C. Explicit project id blocks JOIN group recovery
- query: explicit `PJT_ID=...` JOIN request
- expected keep: instance axis remains locked
- expected ban: `group` fallback even when active anchor has `pjt_no`

### 19D. Support SEARCH stays legacy
- query: `지원 과제`
- expected keep: legacy retriever path
- expected ban: `ExecutionManager` ownership until support contract is explicitly migrated

## Streaming reliability watchlist
### 20. async close handled correctly
- no `AsyncStream.close was never awaited`

### 21. emitted chunks recognized
- chunks arriving should not end with emitted_chunks=0 unless truly empty.

### 22. TTFT deadline not tripped by parser bug
- if text chunks arrive, ttft should be set.
