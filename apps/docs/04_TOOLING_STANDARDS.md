# 04 도구화 규격과 상태 격리

이 문서는 L2 오케스트레이션에서 사용하는 Atomic Tool 규격과 state ownership 경계를 정의한다.

---

## Tool 기본 원칙

- tool은 `AgentState`를 직접 읽거나 쓰지 않는다.
- tool은 필요한 입력만 받고, 결과는 `ToolResult`로만 반환한다.
- state merge, retry 여부 판단, trace 기록은 오직 orchestrator가 담당한다.
- raw -> canonical -> prompt 흐름에서 tool은 raw 결과를 정리해 `canonical_evidence`와 `render_profile`까지만 올린다.

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

- `agent_tools.py`: `search_ntis_domain`, `refine_current_subject`, `ask_user_for_clarification`의 tool spec과 아직 미구현인 `lookup_specific_entity`, `join_project_perf` 선언
- `agent_contracts.py`: workflow state와 router가 공유하는 `AgentDecision` 계약. 허용 decision은 `direct_answer`, `call_tool`, `ask_clarification`, `agent_internal_error`로 고정한다.
- `agent_observation.py`: `AgentObservation` 표준 관측 결과와 guarded planner 산출물을 함께 싣는 `AgentToolExecutionResult`
- `agent_tool_executor.py`: `search_ntis_domain`, `refine_current_subject`를 `build_agent_intent_payload()`로 연결해 guarded `IntentPayloadV3` / `QuestionAnalysisV3`를 생성
- `conversation_state_card.py`: `SessionMemory.current_context`를 LLM이 읽을 수 있는 state card로 투영

신규 agent 계약에는 이전 front-controller fallback decision이 없다.
LLM 출력 parse/schema/tool validation 실패는 router가 1회 self-repair한다. repair 실패 또는 invoke 실패는 `agent_internal_error`로 닫으며 clarification으로 변환하지 않는다.
unknown tool name은 repairable validation error다. Registry에 선언되어 있으나 미구현인 tool 선택은 router가 막지 않고 executor가 `contract_violation` observation으로 닫는다.

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
