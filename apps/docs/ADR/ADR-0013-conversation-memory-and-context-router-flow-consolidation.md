# ADR-0013: Conversation Memory & Context Router Flow Consolidation

## Status
Proposed

## Date
2026-04-21

## Context

NTIS RAG 시스템의 "대화 플로우 + 메모리" 축은 다음 구성요소로 분산되어 있다.

- `apps/conversation/conversation_store.py` — history, canonical_evidence, render_profile, view_state 저장 (Redis TTL)
- `apps/conversation/raw_payload_store.py` — 원본 payload (gzip+base64) 보관, active 1 + inactive N (`RAW_PAYLOAD_RECENT_ANCHOR_LIMIT`) + TTL
- `apps/conversation/view_state.py` — focus_entity, subject_index, recent_mentions, visible_answer_manifest
- `apps/conversation/context_router.py` — recent_mentions에서 deterministic matcher + 제약형 LLM fallback
- `apps/conversation/fact_followup_resolver.py` — raw_payload 기반 팩트 short-circuit (retrieval 직전 호출)
- `apps/conversation/request_facade.py` — turn_trigger → candidate builder → turn_interpreter → turn_policy → scope_resolver → context_router → followup_resolution 조율

호출 진입점:
- `apps/api/workflow_nodes.py::node_load_memory` — 양 store 복원
- `apps/api/workflow_nodes.py::node_save_history` — 양 store 저장
- `apps/retrieval/retrieval_workflow.py::node_rag_search` — fact short-circuit hook + upsert_raw_payload_records

이 ADR은 retrieval semantics, planner strategy, L1 의도 계약, pjt_id/pjt_no 분리 등 **모든 불변 조건을 보존**하며, 대화 플로우 계층의 (a) 메모리 retention/정리 가시화, (b) follow-up 재사용률 개선, (c) 책임 경계 재정리, (d) observability 보강을 다룬다.

## current_state_summary

### 메모리 수명주기
- `raw_payload_store`는 Redis에 `conversation:v2:{conversation_id}:raw_payload_store` 키로 저장되며, `ex=history_ttl_seconds` TTL이 적용된다 (`REDIS_TTL`=3600s).
- `_prune_records`는 만료된 record 제거 + active 1 + inactive N(기본 3) LRU(`updated_at` 기준) 정책을 적용한다.
- `sync_active_anchor_record`는 view_state의 활성 entity에 맞춰 `active` 플래그를 동기화한다.
- `conversation_store`는 한 번 save 시 history/canonical_evidence/render_profile/view_state 전체를 JSON으로 덮어쓴다.

### Follow-up 재사용 경로
1. `request_facade.build_intent_payload` → hard signal precheck → turn_trigger/interpreter/policy
2. `scope_resolver` → `context_router`(recent_mentions deterministic + LLM fallback) → router_anchor 또는 resolved_anchor 확정
3. `node_rag_search` 진입 → `resolve_followup_from_facts` 호출 (active anchor + raw_payload_record 존재 시 팩트로 직접 답변)
4. fact miss → retrieval 정상 수행 → `upsert_raw_payload_records`로 원본 보관

### 관측성 (현행)
- `LOAD.MEMORY`: history_turns, prev_context_docs, turn_id
- `CONTEXT.ROUTER`: status, source(deterministic_recent_mentions / llm_recent_mentions / none), confidence, recent_mentions_count, clarification_avoided
- `SCOPE.DECISION`, `TURN.INTERPRETATION`, `FOCUS.ENTITY.SET`, `FOLLOWUP.FACT_RESOLVED` (hit만)

## pain_points

### 1. 메모리 성장 가시성 부재

`_prune_records`는 실제로 LRU + TTL을 수행하지만, "몇 개가 유입되어 몇 개가 드롭됐는가"는 로그로 남지 않는다. 운영 중 raw_payload_memory가 상한선까지 차는 빈도, 평균 직렬화 크기, active/inactive 분포가 보이지 않아 retention 튜닝 판단 근거가 없다. 메모리 프로젝트 리스크(기존 memory 파일 기준 리스크 #3)와 직결된다.

### 2. Fact follow-up miss 사유가 관측 불가

`fact_followup_resolver.resolve_followup_from_facts`는 hit 시에만 `FOLLOWUP.FACT_RESOLVED` 이벤트를 낸다. miss 시에는 아무 신호가 없어, short-circuit이 왜 실패했는지 (active anchor 없음 / record 없음 / 패턴 매칭 실패 / strategy_meta 조건 미충족) 사후 진단이 불가능하다. 현행 패턴 매칭도 "총 몇 명(연구자)", "연구책임자", "참여기관 수/목록", "연구자 목록" 4개 케이스만 커버한다.

### 3. Context Router heuristic ↔ LLM 경로 분기 추적성

`run_context_router`는 heuristic 결과가 resolved가 아닐 때만 LLM fallback을 시도하며, LLM 결과의 confidence가 낮으면 다시 heuristic으로 회귀한다. 하지만 현재 `CONTEXT.ROUTER` 이벤트는 최종 source만 남기고, "heuristic → llm 시도 → heuristic 회귀(low_confidence_fallback)" 같은 중간 경로는 별도 이벤트로 분리되지 않아 LLM 호출 비용/품질 판단이 어렵다.

### 4. 계층 경계: retrieval이 conversation의 raw payload 저장소를 직접 호출

`apps/retrieval/retrieval_workflow.py`는 `apps.conversation.raw_payload_store`와 `apps.conversation.fact_followup_resolver`를 직접 import한다. 검색 직전 메모리 조회 자체는 합리적 hook이지만, "압축된 raw payload → answer text"로 변환하는 로직(`_answer_from_facts`)이 conversation 레이어에 있는 것은 역할 정의와 어긋난다. conversation은 "대화 상태 소유자", evidence는 "답변 근거 조립자"이므로 raw payload → answer 변환은 evidence 레이어에 위치하는 것이 자연스럽다.

### 5. request_facade 내부 context_router 분기의 가독성

`request_facade.build_intent_payload`는 ~2100줄에 가까운 단일 함수이며, 내부에 context_router 호출, scope_decision 패치, followup_resolution 3단 분기, hard_signal 처리, anchor 병합이 섞여 있다. `context_router_anchor` vs `scope_decision.resolved_anchor`의 우선순위 로직이 여러 조건문에 걸쳐 흩어져 있어, 향후 수정 시 리스크가 높다.

## proposed_target_state

### 1. Memory observability surface 신설

대화 메모리 계층에 "상태 스냅샷 + 이벤트" 표준화된 관측 지점을 추가한다. 단일 책임을 가진 작은 모듈을 conversation 레이어에 둔다.

- `apps/conversation/memory_observer.py` (신설)
  - `summarize_raw_payload_memory(memory)` → `{records_total, records_active, records_inactive, bytes_total, anchor_keys, oldest_updated_at}`
  - `log_memory_snapshot(stage, ...)` — `MEMORY.SNAPSHOT` 이벤트 emit
  - `log_fact_followup_miss(reason, ...)` — `FOLLOWUP.FACT_MISS` emit
  - `log_context_router_transition(from_source, to_source, reason, ...)` — `CONTEXT.ROUTER.FALLBACK` emit

이 모듈은 **관측 전용**이며 기능 경로에 부작용을 주지 않는다 (fire-and-forget).

### 2. Fact follow-up의 miss 신호 & 패턴 확장

`resolve_followup_from_facts`를 다음과 같이 확장한다.

- 반환을 `Optional[Dict]` 유지하면서, miss 시 `{"miss_reason": "..."}` 형태의 사유를 호출자(retrieval_workflow)에 전달할 수 있는 내부 API를 추가한다. 기존 호출 방식과 호환을 유지하기 위해, 별도 함수 `diagnose_followup_fact_miss(...)`를 제공해 호출자가 miss 시 reason을 명시적으로 조회하도록 한다.
- 패턴을 2개 추가한다 — "연구 기간 / 시작일 / 종료일" (period), "연구비 / 총 사업비" (budget). build_eager_facts에 이미 존재하는 필드를 재활용한다.
- miss 시 `log_fact_followup_miss(reason, anchor_key, has_anchor, has_record)`을 호출해 운영 가시성을 확보한다.

### 3. Context Router fallback 경로 세분화 로그

`request_facade.build_intent_payload`의 context_router 호출부에서 다음을 추가한다.

- heuristic resolved → no fallback attempted: `CONTEXT.ROUTER.FALLBACK` 생략
- heuristic ambiguous/unresolved → LLM attempted → LLM resolved: `CONTEXT.ROUTER.FALLBACK(from=heuristic, to=llm, reason=heuristic_ambiguous)`
- LLM attempted → low_confidence_fallback: `CONTEXT.ROUTER.FALLBACK(from=llm, to=heuristic, reason=low_confidence_fallback)`
- LLM error: `CONTEXT.ROUTER.FALLBACK(from=llm, to=heuristic, reason=llm_error_fallback:<ExceptionType>)`

이 추가는 decision.reason / decision.source 조합을 파생시켜 별도 이벤트를 내는 순수 파생 로직이며, 라우팅 행위 자체에는 영향을 주지 않는다.

### 4. Memory snapshot 이벤트

`node_load_memory` 끝에 `log_memory_snapshot(stage="load", ...)`, `node_save_history` 끝에 `log_memory_snapshot(stage="save", ...)`를 emit한다. prune이 실제로 발생했는지 판단은 전/후 count 차이로 간접 확인 가능하다. (엄격한 drop count가 필요하면 `_prune_records`를 래핑하는 후속 ADR에서 다룬다.)

### 5. 장기 과제 (후속 사이클에서 실행 완료)

> 상태: **executed in follow-up cycle (2026-04-21)** — 본 섹션의 두 항목 모두 본 문서 최초 작성 이후 후속 사이클에서 반영됐다. 아래 각 항목에 실제 구현 요약을 병기한다.

- **fact_followup_resolver → evidence 이전 (완료)**:
  - 구현: `apps/evidence/memory_facts_resolver.py`를 신규 모듈로 생성하고 `resolve_followup_from_facts`, `diagnose_followup_fact_miss`의 전 로직을 이전했다. 기존 `apps/conversation/fact_followup_resolver.py`는 얇은 re-export shim으로 축소했다 (외부 실험 worktree/사용자 스크립트의 import path 하위호환 유지).
  - Import 정리: `apps/retrieval/retrieval_workflow.py`가 `apps.evidence.memory_facts_resolver`를 직접 import하도록 변경. retrieval → conversation 경계 넘김은 `raw_payload_store`(state ownership) / `view_state`(focus) 조회로만 남는다.
  - 불변 계약: `AnswerArtifact` 합성·`answer_source="raw_payload_facts"` meta·pjt_id≠pjt_no 분리·raw payload의 prompt 비주입 모두 그대로 유지. 패턴은 연구책임자/참여기관/참여연구자/연구기간/연구비로 확장되었다 (기존 participant_count·participant_org_count도 유지).

- **request_facade의 context_router 분기 추출 (완료)**:
  - 구현: `apps/conversation/request_facade.py`에 `_apply_context_router_decision(...)` async 헬퍼를 추가하고, `build_intent_payload` 내부에 인라인으로 존재하던 `context_router_anchor = None … CONTEXT.ROUTER.FALLBACK` 블록 전체 (약 90 lines)를 단일 호출로 교체했다.
  - 책임 경계: 호출부는 `(scope_decision, context_router_anchor, context_router_meta)` 튜플만 수령. `run_context_router` 호출·`scope_decision.model_copy` 패치·`_context_router_anchor_allowed` 게이트·`CONTEXT.ROUTER` log_event·`log_context_router_transition` 방출 전부 헬퍼 내부로 국소화.
  - 라우팅 행위 불변: 헬퍼는 순수 이동 리팩터이며, 기존 `reference_followup` / `needs_clarification=False` / `context_router_anchor` 우선순위·detail 쿼리 시 pjt_id 필요 가드 로직이 모두 동치 보존된다. planner strategy output은 여전히 건드리지 않는다.

## non-negotiables (이 ADR도 유지)

- planner strategy는 요청마다 하나이며 immutable이다 (`mode`/`relation`/`target_cols`/`join_key_mode` answer stage에서 재결정 금지).
- `SEARCH` recall-first, `LOOKUP`/`JOIN` precision-first gating 유지.
- raw payload는 prompt에 직접 주입되지 않는다. fact short-circuit은 이미 `answer_artifact`를 합성하므로 이 불변식을 위반하지 않는다.
- `pjt_id` (instance) ≠ `pjt_no` (group). context_router는 ID 생성 금지, 로컬 매핑만 사용.
- context_router 출력에 `mode/relation/target_cols/join_key_mode` 불포함.
- fallback chat escape hatch 금지, BM25-only shortcut 금지.

## impact_and_tradeoffs

- **장점:** 운영 중 메모리 retention/follow-up hit rate/LLM fallback 비용을 정량적으로 관측 가능. fact short-circuit miss 분포가 드러나면 패턴 확장 우선순위 판단 가능.
- **비용:** MEMORY.SNAPSHOT 이벤트는 turn당 2회 추가(load/save). CONTEXT.ROUTER.FALLBACK은 라우터 호출 시에만. FOLLOWUP.FACT_MISS는 retrieval 단계에서 fact hook이 호출될 때만. 전체적으로 turn당 수 개의 구조화 로그 추가 — 로그 볼륨은 기존 `RAG.*`, `PLANNER.*`에 비해 낮다.
- **리스크:** miss reason 판단 로직 자체가 오판하면 잘못된 운영 신호를 줄 수 있다. 미치는 영향은 로깅 한정(분기 행위 미변경)이라 안전하게 롤백 가능.

## compatibility

- 기존 API 응답 명세(`05_API_응답명세.md`) 변경 없음.
- 기존 이벤트 이름(`FOLLOWUP.FACT_RESOLVED`, `CONTEXT.ROUTER`, `LOAD.MEMORY`, `REQ.SUMMARY`) 유지. 신규 이벤트만 추가.
- `RAW_PAYLOAD_RECENT_ANCHOR_LIMIT`, `REDIS_TTL` 기본값 변경 없음.
- `resolve_followup_from_facts`의 기존 signature/반환은 유지. 신규 `diagnose_followup_fact_miss` 함수만 추가.
- 기존 불변 계약(모든 non-negotiables) 보존.

## validation

- `py_compile` 로컬 실행: `apps.conversation.memory_observer`, `apps.conversation.fact_followup_resolver`, `apps.conversation.raw_payload_store`, `apps.api.workflow_nodes`, `apps.conversation.request_facade`, `apps.retrieval.retrieval_workflow`.
- import smoke: 모듈 import 성공.
- `create_app()` smoke: 라우트 wiring 성공.
- 본 ADR은 `tests/` 회귀 lane이 본 worktree에서 비활성인 점(이전 ADR들과 동일 조건)을 가정하며, 후속으로 import-light regression lane에 신규 이벤트 스모크를 포함할 것을 제안한다.
