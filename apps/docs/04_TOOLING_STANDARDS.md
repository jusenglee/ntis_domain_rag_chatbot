# 04 도구화 규격과 상태 격리

이 문서는 L2 오케스트레이션에서 사용하는 Atomic Tool 규격과 state ownership 경계를 정의한다.

---

## Tool 기본 원칙

- tool은 `AgentState`를 직접 읽거나 쓰지 않는다.
- tool은 필요한 입력만 받고, 결과는 `ToolResult`로만 반환한다.
- state merge, retry 여부 판단, trace 기록은 오직 orchestrator가 담당한다.
- raw -> canonical -> prompt 흐름에서 tool은 raw 결과를 정리해 `canonical_evidence`와 `render_profile`까지만 올린다.
- Agent-facing tool은 의미 수준 인자만 받는다. DB filter, 식별자 축, JOIN 축은 planner/contract backend가 검증 가능한 형태로 만든다.
- parser가 인식하지 못하는 자연어 count token을 검색어에 섞지 않는다.

---

## 공통 반환형: `ToolResult`

orchestrator-facing tool은 모두 `ToolResult`를 반환한다.

- `tool_name`
- `status`
- `rows`
- `canonical_evidence`
- `render_profile`
- `diagnostics`
- `latency_ms`

내부 leaf helper는 raw `list[dict]`를 잠깐 사용할 수 있지만,
`ExecutionManager`가 직접 호출하는 경계에서는 반드시 `ToolResult`로 정규화한다.

---

## 현재 tool 구성

### Agent-facing tool adapter

ADR-0001 전환을 위해 `apps.conversation`에 agent-facing tool 계약을 추가했다.
이 계층은 LLM Dialogue Agent가 호출할 의미 수준 tool을 정의하고, 기존 planner / contract 검증을 통과시키는 얇은 adapter 역할을 한다.

- `agent_tools.py`: `search_subject_activity`, `search_ntis_domain`, `refine_current_subject`, `ask_user_for_clarification`의 tool spec과 아직 미구현인 `lookup_specific_entity`, `join_project_perf` 선언
- `agent_contracts.py`: workflow state와 router가 공유하는 `AgentDecision` 계약. 허용 decision은 `direct_answer`, `call_tool`, `ask_clarification`, `agent_internal_error`로 고정한다.
- `agent_observation.py`: `AgentObservation` 표준 관측 결과와 guarded planner 산출물을 함께 싣는 `AgentToolExecutionResult`. subject activity/refinement 결과는 answer publication 전 staging 후보인 `next_current_context`를 함께 실을 수 있다.
- `agent_tool_executor.py`: `search_subject_activity`와 `refine_current_subject`는 direct compile로, generic `search_ntis_domain`은 `build_agent_intent_payload()`로 연결해 guarded `IntentPayloadV3` / `QuestionAnalysisV3`를 생성한다. direct subject tool은 `SubjectQueryContext(publication_status="clarification_pending")`를 stage하고 answer publication 이후 `answer_published` 또는 `answer_withheld_subject_retained`로 커밋한다. stage/commit payload는 `identity_status`와 복수 `person_no` 후보를 함께 유지할 수 있으며, ambiguity는 commit 이후에도 explicit disambiguation signal 전까지 보존한다.
- `conversation_state_card.py`: `SessionMemory.current_context`를 LLM이 읽을 수 있는 state card로 투영

신규 agent 계약에는 이전 front-controller fallback decision이 없다.
LLM 출력 parse/schema/tool validation 실패는 router가 1회 self-repair한다. repair 실패 또는 invoke 실패는 `agent_internal_error`로 닫으며 clarification으로 변환하지 않는다.
unknown tool name은 repairable validation error다. Registry에 선언되어 있으나 미구현인 tool 선택은 router가 막지 않고 executor가 `contract_violation` observation으로 닫는다.

### Agent-facing tool 책임 경계

| Tool | 사용해야 하는 경우 | 금지 |
|---|---|---|
| `search_subject_activity` | 명시 연구자/기관의 활동기록, 활동내역, 참여이력, 관련 과제/성과 이력을 조회할 때 | generic topic search, subject가 구조화되지 않은 broad search |
| `search_ntis_domain` | 명시적으로 새 generic 대상/새 도메인을 검색할 때 | 사람/기관 활동기록, 현재 화면 항목 상세, current subject refinement |
| `refine_current_subject` | `SubjectQueryContext`의 현재 주체에 기간, 역할, 성과유형, target, 개수 조건만 더할 때 | current context 없이 임의 대상 생성 |
| `lookup_specific_entity` | explicit ID 또는 single-candidate guard를 통과한 단일 대상 상세 | 후보 복수 상태에서 임의 첫 항목 조회 |
| `join_project_perf` | project anchor와 JOIN relation이 contract로 확정된 경우 | `pjt_id`/`pjt_no` 축 추측 |
| `ask_user_for_clarification` | 사용자 대상이 실제로 복수/모호한 경우 | schema/tool/planner 내부 오류 대체 |

### Query materialization 규칙

Agent tool adapter는 구조화 인자를 우선한다.

- `limit`은 tool args와 planner count contract에 구조화 필드로 전달한다.
- `domain_head`는 자연어 검색어에 붙이지 않고 `NormalizedIntent.base_route`, `QuestionAnalysis.head`, `qdrant_query_plan.target_collections`로 전달한다.
- 검색어 보강이 꼭 필요하더라도 `domain_head`와 count는 검색어 보강 재료가 아니다.
- `1 items`, `10 items`, `people 10개` 같은 parser-incompatible 또는 구조 필드 token을 검색어에 붙이면 안 된다.
- `action=detail` 또는 `output_type=detail`은 materialized query의 count token보다 system contract가 우선하며 `1/1`로 normalize한다.
- count token은 대상 식별 근거가 아니다. 단일 후보 검증은 current context, active scope, visible manifest, explicit ID에서 별도로 수행한다.
- `search_subject_activity` / `refine_current_subject` direct compile은 `QuestionAnalysisV3.planner_source`에 새 enum을 쓰지 않는다. 관측용 출처는 `strategy_meta.tool_execution_source`와 로그 필드에만 둔다.

### `project_tools.py`

- `fetch_project_detail(pjt_id, target_cols, collaborators)`
- `search_projects_by_text(query, filters, limit, collaborators)`

### `relation_tools.py`

- `fetch_project_performance(pjt_ids, join_key_mode, collaborators)`

### `route_search_tools.py`

- `search_route_by_text(base_route, query, filters, limit, target_cols, collaborators)`
- `render_profile` 기본값은 `{"context_kind": base_route, "name": "list"}`

---

## Route별 기본 target cols

planner / query_intent가 이미 `target_cols`를 준 경우 그 값을 우선 사용한다.
없을 때만 route 기본값을 사용한다.

- `project -> [COL_PROJECT]`
- `perf -> [COL_PERF]`
- `people -> [COL_PROJECT, COL_PERF]`
- `org -> [COL_PROJECT, COL_PERF]`
- `support -> [COL_SUPPORT]`

주의:

- `support` route 기본값은 정의만 유지한다.
- 이번 tranche에서는 `support SEARCH`를 `ExecutionManager`로 이관하지 않고 현재 agent-facing adapter 범위 밖에 둔다.

---

## State ownership

### orchestrator가 읽는 것

- `QuestionAnalysisV3`
- `retrieval_query`
- `target_cols`
- `request_meta`
- collaborator bundle

### orchestrator가 state에 쓰는 것

- `context`
- `canonical_evidence`
- `render_profile`
- `execution_trace`
- `retrieval_runtime_meta`
- `no_result_message`

### tool이 몰라도 되는 것

- `AgentState` 전체
- `ConversationViewState` 전체
- `selected_answer_meta`
- prompt 조립 로직

---

## JOIN fallback authority

JOIN의 group fallback authority는 free-text가 아니라 active anchor state다.
따라서 orchestrator는 필요할 때만 아래 값을 `request_meta`로 주입한다.

- `anchor_pjt_id`
- `anchor_pjt_no`
- `anchor_source`

tool은 이 값이 어디서 왔는지 추론하지 않고, 전달된 값만 사용한다.
