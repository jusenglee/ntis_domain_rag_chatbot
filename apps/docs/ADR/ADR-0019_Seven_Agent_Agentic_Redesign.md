# ADR-0019: 7-Agent Agentic Redesign

- **Status**: Accepted (2026-05-19)
- **Supersedes**: [ADR-0018 Three Layer Authority Separation](./ADR-0018_Three_Layer_Authority_Separation.md)
- **Builds on**: [ADR-0016 Shock Absorber](./ADR-0016_Agent_Contract_Shock_Absorber.md), [ADR-0017 Remapped Reference Manifest](./ADR-0017_Answer_Rank_Remapped_Reference_Manifest.md)

## Context

2026-05-18 로그 분석에서 ADR-0018의 3계층(JudgmentAgent / SearchAgent / FinalGuard) 설계가 다음
패턴의 회귀를 막지 못하는 것이 드러났다:

1. **`state_consistency` 만성 차단** — list 응답이 정책 자기모순(`allow_prefix_subset=True` ↔
   `allow_manifest_publish_on_subset=False`)으로 일괄 비공개 처리.
2. **`people_terms_lost` 드리프트** — 사람 anchor가 retrieval 단계에서 구조화 필터로 전달되지 않아
   주체와 무관한 NTIS 사업이 top hit.
3. **Agent 루프** — `resolve_project_title` 같은 도구가 코드형 식별자(`K-20-L01-C07`)를 처리하지
   못하고 동일 인자 재호출 → loop guard로 차단.
4. **session payload 손상** — 한 turn의 `current_context`가 다음 turn에서 다른 컨텍스트로
   덮어쓰여 multi-turn subject 연속성이 끊김.
5. **manifest_rank=10 > visible_count=7** — manifest가 cited_ranks만 발행해 follow-up 매핑 깨짐.

근본 원인은 **JudgmentAgent가 의도 분류 + 식별자 해소 + 검색 전략 + 도구 선택을 한꺼번에
결정**하면서 LLM의 책임이 너무 넓어졌고, **단일 `current_context` discriminated union**이
세 종류의 follow-up(subject refine, manifest ordinal, focused detail)을 동시에 다룰 수 없었기
때문이다.

## Decision

7개 단일책임 에이전트 + 6개 frozen Pydantic 계약 + 3개 평행 세션 슬롯으로 재설계한다.

### Agents (책임 단일화)

| # | Agent | 입력 → 출력 | LLM | 책임 |
|---|---|---|---|---|
| 1 | `DialogueAgent` | 질문 + `SessionState` → `DialogueIntent` | Solar (1회) | 의도 분류만 (kind / target_hint / action_hint / subject / manifest_rank / identifier_hints) |
| 2 | `EntityResolverAgent` | Intent + Session → `EntityResolution` | — | 결정적 식별자 해소 (manifest_rank → ids, rst_id → target=perf 강제, ASCII 식별자 형식 검증, 동명이인 표시) |
| 3 | `SearchPlannerAgent` | Intent + Resolution → `SearchPlan` | — | 1+ `SearchTask` 빌드 + merge_strategy 결정 |
| 4 | `RetrievalAgent` | `SearchPlan` → `SearchResult` | — | `asyncio.gather` 병렬 + identity dedup + max_results cap |
| 5 | `EvidenceCuratorAgent` | `SearchResult + Intent` → `EvidenceBundle` | — | view 결정 + display_rank 최종 고정 + 그룹화 |
| 6 | `AnswerAgent` | `EvidenceBundle + Intent` → `AnswerDraft` | Gemma/Triton (1회, 또는 repair시 2회) | view별 prompt + LLM stream + [N] 파싱 |
| 7 | `CriticAgent` | `AnswerDraft + Bundle + Intent` → `GuardDecision` | — | citation 정합 + ReferenceManifest 발행 (ADR-0017) + publish/repair/clarify/error |

### Contracts (모두 frozen Pydantic, `extra="forbid"`)

신규 6개: `DialogueIntent`, `EntityResolution`, `SearchPlan`, `EvidenceBundle`, `AnswerDraft`, `GuardDecision`.
재사용: `SearchTask`, `CanonicalEvidence`, `SearchResult`, `IdentifierBundle`, `SubjectAnchor`, `FilterBundle`, `ReferenceItem`, `ReferenceManifest`.
폐기: `JudgmentDecision`, `DirectAnswer`, `Clarification`(contracts.py 모델), `GeneratedAnswer`, `FinalAnswer`.

### Session — 3 Parallel Slots

`apps.pipeline.agents.session_state.SessionState`는 다음 슬롯을 **평행**으로 들고 다닌다.
한 슬롯의 갱신은 다른 슬롯을 절대 지우지 않는다 (이것이 ADR-0018 대비 핵심 변경).

- `current_subject` (`SubjectSlot`) — 사람/기관 anchor. refine_previous 시 복원.
- `published_manifest` (`ManifestSlot`) — 직전 turn N건. ordinal/title follow-up 해소.
- `focused_detail` (`FocusedDetailSlot`) — 가장 최근 본 단일 대상.

`SessionState`는 **frozen**(`extra="forbid", frozen=True`). 갱신은 `with_subject` / `with_manifest` / `with_focused_detail` 메서드(내부 `model_copy`)로 함수적으로 수행.

`SessionStateAdapter.from_session_memory(memory, *, conversation_id)` / `to_session_memory(state)`로 기존 `SessionMemory`와 양방향 변환. **`SessionMemory` 모델 자체에는 `conversation_id` 필드가 없으므로(Pydantic v2 `extra="ignore"` silent drop) 외부에서 명시 인자로 들고 다닌다.**

### Workflow (LangGraph 12 nodes, 5 routings)

```
load_session
  → dialogue_agent
        ├─ direct_answer    → emit_direct_answer    → save_session
        ├─ clarification    → emit_clarification    → save_session
        └─ search/refine/detail
            → entity_resolver
                ├─ clarification_needed → emit_clarification → save_session
                └─ otherwise
                    → search_planner
                          ├─ plan == None       → emit_clarification
                          └─ plan
                              → retrieval_agent
                                    ├─ status=error → emit_internal_error
                                    └─ otherwise
                                        → evidence_curator
                                            → answer_agent
                                                  → critic_agent
                                                        ├─ publish        → save_session
                                                        ├─ repair_answer  → answer_agent (state.repair_attempted=True)
                                                        ├─ clarify        → emit_clarification
                                                        └─ internal_error → emit_internal_error
```

`load_session`은 명시적으로 주입된 `session_state`(테스트·재실행)가 비어있지 않으면 KV 로드를 건너뛴다. 운영 라우트(`routes.py`)는 빈 session_state를 전달하므로 KV 로드가 그대로 작동.

빈 질문은 라우트 레이어에서 400으로 거부하지 않고 그대로 워크플로우에 위임 → `DialogueAgent`가 `kind="clarification"`으로 안전 닫기.

`AnswerAgent.generate(repair_hint=...)`는 `GuardDecision.repair_hint`를 system prompt에 첨부해 2차 시도 시 동일 위반을 반복하지 않도록 유도.

### Reference Manifest (ADR-0017 강화)

`CriticAgent`는 publish 시 `EvidenceBundle.items` **전체**를 `published_rank = snapshot_rank`로 발행한다. cited_ranks만 발행하면 follow-up "[10]번 항목" 매핑이 깨지므로 모든 evidence를 발행. `EvidenceCuratorAgent`가 1..N으로 재부여한 `snapshot_rank`가 진실원이다.

## Consequences

### Positive
- LLM 책임 축소 — DialogueAgent(분류)와 AnswerAgent(답변) 두 군데만 LLM, 나머지 5단계는 결정적.
- 정책 자기모순 제거 — 부분집합 수용/차단 정책 폐기. CriticAgent는 단일 규칙 집합.
- multi-turn follow-up 강건성 — 3 평행 슬롯이 subject/manifest/focused_detail을 동시에 보존.
- 자가복구 — CriticAgent `repair_answer` 1회 재시도, 실패 시 사용자에게 clarify.
- 관측성 일관성 — 모든 에이전트가 동일한 `diagnostics` 키셋을 다음 단계에 전달.

### Negative / Tradeoffs
- 노드 수가 늘어 LangGraph 컴파일 단계가 약간 복잡 (12 노드, 5 라우팅).
- DialogueAgent와 AnswerAgent의 LLM 어댑터가 정확히 합의된 ainvoke/astream 시그니처여야 한다.
- repair_answer 1회 한도는 외부 state(`repair_attempted`)로 관리 — 단순 노드 호출만으로는 무한 루프 방지가 안 된다.

### Migration (Phase 11 시점에 완료)

- 삭제됨: `apps/pipeline/judgment_agent.py`, `apps/pipeline/final_guard.py`, `apps/pipeline/llm_generator.py`, `apps/pipeline/workflow.py`, `apps/pipeline/state.py`.
- 변경됨: `apps/api/runtime.py` (`AgentPipelineDeps` 빌드), `apps/api/routes.py` (`AgentPipelineState` 사용, payload에 `dialogue_intent`/`entity_resolution`/`search_plan`/`evidence_bundle_view`/`guard_decision`).
- 유지됨: `apps/pipeline/search_agent.py` (RetrievalAgent의 `task_executor`로 재사용), `apps/pipeline/contracts.py`의 SearchTask/CanonicalEvidence/ReferenceItem/ReferenceManifest 등.

## Validation

- `pytest tests/pipeline -q` → 210+ passed
- Golden 시나리오 (8 e2e tests) — 신동구 활동내역, manifest_rank follow-up, pjt_no/rst_id literal, 빈 답변, repair 2종.
- Incident 회귀 (R1~R15) — manifest 전체 발행, citation 정합, rst_id forced perf, snapshot_rank 1..N, 평행 슬롯 격리, 빈 본문 internal_error, 빈 질문 clarification, conversation_id 추적, repair_hint 전달, 비ASCII identifier 드롭, retrieval single-task diagnostics keyset, SSE status kind 제거, 라우트 400 폐기.
- 관측성 (9 tests) — Curator/Critic/Answer/Retrieval diagnostics 키셋.

## Reference Implementation

- 패키지 진입점: `apps.pipeline.build_agent_pipeline_graph`, `apps.pipeline.AgentPipelineDeps`, `apps.pipeline.AgentPipelineState`.
- 7 agents: `apps/pipeline/agents/{dialogue_agent,entity_resolver,search_planner,retrieval_agent,evidence_curator,answer_agent,critic_agent}.py`.
- Contracts: `apps/pipeline/agents/contracts.py`, `apps/pipeline/contracts.py`.
- Session: `apps/pipeline/agents/session_state.py`.
- Workflow: `apps/pipeline/agent_workflow.py`, `apps/pipeline/agent_state.py`.
