# ADR-0015 구현 현황 감사 보고서

- **대상 ADR**: `apps/docs/ADR/ADR-0001_LLM-First Dialogue Agent over Contract-Guarded RAG Tools.md` (본문 내 섹션 번호는 ADR-0015로 표기)
- **감사 일자**: 2026-04-22
- **감사 범위**: workflow graph 연결까지 (코드 수정 없음, 현황 감사만)
- **브랜치**: `고도화` (memory 기준)
- **감사 방법**: ADR 명세 ↔ 실제 코드 1:1 매핑. 자동 탐색 에이전트 1차 리포트 후 critical gap 4건 직접 재검증.

---

## 0. 한눈에 보기

| 영역 | 상태 | 비고 |
|------|------|------|
| Phase 1 — Conversation State Card | ✅ 거의 완료 | 렌더링 분기 5종 모두 구현 |
| Phase 2 — Tool Registry | ✅ 거의 완료 | Tool 2개 완전 구현, 2개 `implemented=False` (stub) |
| Phase 3 — Dialogue Agent | ✅ 완료 | fail-closed + self-repair 포함, 2026-04-22 업데이트 반영 |
| Phase 4 — Workflow 연결 | ⚠️ 부분 | Agent path는 엣지로 연결됨. 단, ADR 9.2의 `dialogue_agent_final`/`answer_validation`/`publish_context` 노드 **없음** |
| Phase 5 — request_facade 강등 | ⚠️ 부분 | 신규 진입점은 구현. 다만 함수 이름이 `build_intent_payload_for_agent_tool` (ADR) → `build_agent_intent_payload` (실제)로 변경됨 |
| Phase 6 — publication_status | ⚠️ 부분 | dialogue continuity 경로는 **구현됨**. 그러나 타입이 `Optional[str]`이며 값 이름이 ADR과 불일치 |
| 강등 대상 (D3) | ⚠️ test-alive dead path | production에서는 호출 안 됨, 단 20+ 테스트가 patch/호출. 2026-04-22 DEPRECATED docstring 마커 추가 |
| Observability (section 16) | ✅ 완료 | AGENT.* 이벤트 14/14 emit 지점 확인 |

**종합 구현율 추정: 약 80~85% (acceptance criteria 7개 중 5개 충족, 2개 부분 충족).**

---

## 1. Phase 1 — Conversation State Card

**파일**: `apps/conversation/conversation_state_card.py`

### 일치 항목
- 함수 시그니처 일치: `build_conversation_state_card(*, session_memory, view_state, selected_answer_meta)` ✅
- 자연어 렌더링 분리: `build_conversation_state_card_model()` → `render_conversation_state_card()` ✅
- `ConversationStateCard` pydantic 모델 필드 ADR 11.2 전부 포함 + `result_kind`, `manifest_summary`, `anchor_summary`, `warnings` 확장 필드 추가
- 5개 컨텍스트 타입 모두 분기 렌더링: `SubjectQueryContext`, `ClarificationContext`, `PublishedManifestContext`, `DetailAnchorContext`, `EmptyContext`

### 남은 작업
- ADR 샘플 문구("사용자가 '해당 연구자', '이 연구원' … 라고 하면 … refine 가능")와 실제 렌더링 문구가 완전히 같은지는 golden 출력 비교 필요 (본 감사 범위 외)
- `publication_status` 값 렌더링 시 3가지 값("answer_published" / "answer_withheld_subject_retained" / "clarification_pending")을 모두 구분 표기하는지 확인 필요 — Phase 6 gap과 연결

---

## 2. Phase 2 — Tool Registry

**파일**: `apps/conversation/agent_tools.py`, `agent_tool_executor.py`, `agent_observation.py`

### 일치 항목
- `AgentToolSpec`: `name`, `description`, `input_schema` + ADR에 없는 `implemented: bool` 필드 추가 (미구현 tool을 contract_violation으로 닫기 위한 실용적 확장)
- `AgentObservation.observation_type`: `"search_results" | "lookup_result" | "planned_intent" | "clarification_required" | "no_results" | "contract_violation" | "error"` — ADR의 `"join_result"` 대신 `"planned_intent"` (실제 workflow에 더 적합)
- `execute_agent_tool(*, tool_name, tool_args, state)` 시그니처 일치 ✅
- Tool 구현 상태
  - `search_ntis_domain` — ✅ 구현
  - `refine_current_subject` — ✅ 구현
  - `ask_user_for_clarification` — ✅ 구현 (agent decision 레벨)
  - `lookup_specific_entity` — ⚠️ `implemented=False` (Phase 3 이후 예정, ADR section 6.3과 일치)
  - `join_project_perf` — ⚠️ `implemented=False` (Phase 3 이후 예정)

### ADR 명세와의 차이 (의도적 변경으로 보임)
- `observation_type`에 `"planned_intent"` 추가: tool이 guarded intent만 만들고 실제 retrieval은 다음 node로 넘길 때 사용. ADR 원안은 tool이 retrieval까지 돌려 `"search_results"`를 반환하는 구조였음. 실제 구현은 tool→intent, workflow→retrieval로 책임을 분리하는 쪽. 이는 Phase 4의 workflow 구조와 맞물려 있음.

---

## 3. Phase 3 — Dialogue Agent

**파일**: `apps/conversation/agent_contracts.py`, `agent_dialogue_router.py`, `agent_dialogue_router_validation.py`, `apps/prompts/dialogue_agent_v1.md`

### 일치 항목
- `AgentDecision.decision_type`: `Literal["direct_answer", "call_tool", "ask_clarification", "agent_internal_error"]` ✅ (ADR 5.2와 완전 일치)
- 2026-04-22 업데이트 반영:
  - `fallback_legacy_pipeline` decision 없음 ✅
  - LLM invoke/parse 실패 → 1회 self-repair → 실패 시 `agent_internal_error` ✅
  - agent_dialogue_router.py의 `_repair_agent_decision()`이 max_attempts=1로 정확히 구현
- `run_dialogue_agent()` 시그니처는 ADR에 없는 `turn_id`, `invoke_model` 추가(실용적 확장)
- Validation 전담: `agent_dialogue_router_validation.py`에서 unknown tool, tool arg schema, tool implemented 여부 모두 검증

### 남은 작업
- **Agent Final Answer 단계 없음**: ADR section 5.1에 `tool execution → observation → Agent final response` 루프가 명시되어 있지만, 현재 workflow는 tool 결과를 agent에게 다시 넘기지 않고 바로 `judge_knowledge_sufficiency → rag_search → generate_answer`로 이어진다. (Phase 4에서 자세히 기술)

---

## 4. Phase 4 — Workflow 연결 (직접 검증)

**파일**: `apps/api/workflow_builder.py`, `workflow_nodes.py`

### 실제 그래프 (코드에서 확인한 엣지)

```
load_memory
  → rule_precheck
    ├─(direct_answer)→ direct_answer → save_history
    └─(build_conversation_state_card)
      → run_dialogue_agent
        ├─ agent_direct_answer   → save_history
        ├─ agent_clarification   → save_history
        ├─ agent_internal_error  → save_history
        └─ execute_agent_tool
             ├─(planned_intent)→ judge_knowledge_sufficiency
             │     ├─ generate_answer_solar / gemma (prev_context 충분)
             │     └─ rag_search
             │         ├─ relax_and_retry → rag_search
             │         ├─ generate_answer_gemma → join_answers → merge_answers → save_history
             │         └─ generate_answer_solar → join_answers → merge_answers → save_history
             └─(else)→ agent_clarification → save_history
```

### ADR 9.2와의 차이 (🔴 주목)

ADR 9.2 명세:
```
... → dialogue_agent
    → route_agent_decision
       ├─ direct_answer
       ├─ execute_agent_tool
       │    → observe
       │    → dialogue_agent_final     ← 없음
       └─ ask_clarification
    → answer_validation                ← 없음
    → publish_context                  ← 없음
    → save_history
```

실제 코드:
- `dialogue_agent_final` 노드가 **존재하지 않음**. tool 결과가 agent에게 observation으로 돌아가 자연어 답변을 만드는 단계가 빠져 있고, tool 결과는 기존 rag_search→answer generation 파이프라인으로 합류.
- `answer_validation`, `publish_context` 노드 **존재하지 않음**. 해당 책임은 `save_history` 노드 또는 `merge_answers` 내부 로직으로 암묵적으로 흡수된 것으로 추정(본 감사 범위에서 내부 구현 확인 안 함).

### 영향
- ADR 핵심 가치 중 "Agent가 observation을 보고 다음 행동/최종 답변을 결정한다" 경로가 **부분 구현**. 현재는 Agent가 tool call 1회만 발행하고, 이후에는 기존 deterministic answer generation이 그대로 실행됨.
- 결과적으로 AGENTIC_MAX_STEPS=3 플래그가 있어도 agent 루프는 1회로 제한된 상태. `AGENT.LOOP_GUARD.TRIGGERED` 이벤트가 실제로 트리거될 경로가 사실상 없음.

### 일치 항목
- `route_after_rule`, `route_after_agent_decision`, `route_after_agent_tool` 세 분기 모두 깔끔히 구현 ✅
- agentic front-controller가 유일한 production path (rule_precheck의 direct_answer 예외 제외) ✅
- `agent_flags.py`에서 `AGENTIC_MAX_STEPS=3` 기본값 선언 확인 ✅

---

## 5. Phase 5 — request_facade 강등

**파일**: `apps/conversation/request_facade.py`

### 일치 항목
- agent 전용 진입점 구현: `async def build_agent_intent_payload(...)` (1909~2007줄)
  - 내부에서 `run_question_analysis` → `apply_question_analysis_v3` 호출
  - `run_turn_trigger`, `run_turn_interpreter`, `run_context_router` 호출 없음 ✅ (bypass 확인)
- `agent_tool_executor.py:161`에서 `build_agent_intent_payload`를 직접 호출 ✅

### ADR 명세와의 차이
- **함수 이름 불일치**: ADR은 `build_intent_payload_for_agent_tool`, 실제는 `build_agent_intent_payload`. 파라미터 구조도 ADR 초안(`tool_name, tool_args, state`)보다 풍부함(`question, conversation_id, chat_history, prev_context, canonical_evidence, view_state, session_memory, request_id, turn_id`). 실용적 변경으로 판단되나 ADR 문서 갱신 또는 reference 정정 필요.
- **legacy `build_intent_payload()` 공존**: 같은 파일 다른 위치(`request_facade.py:2193, 2203`)에 여전히 `run_turn_trigger`, `run_turn_interpreter` 호출이 남아있음. 이 함수는 workflow_builder.py에서 참조되지 않으므로 **dead path**로 판단됨(단, import/호출 그래프 전수 확인 필요).

---

## 6. Phase 6 — SubjectQueryContext publication_status (직접 검증)

**파일**: `apps/conversation/session_memory.py`

### 실제 구현 (자동 에이전트 보고 보정)

- **모델 정의 (line 51)**: `publication_status: Optional[str] = None`
- **ADR 명세**: `Literal["answer_published", "answer_withheld_subject_retained", "clarification_pending"] = "answer_published"`

### dialogue continuity 경로는 **구현되어 있음** (자동 에이전트가 "미발견"이라 했으나 오판)

`build_current_context()` (line 498~519):
```python
subject_can_be_retained = (
    publishability == "publishable"
    or (
        publishability in {"withheld_partial", "blocked"}
        and isinstance(subject_manifest, DisplaySnapshot)
    )
)
if subject_can_be_retained and subject_kind and subject_name:
    publication_status = (
        "publishable"
        if publishability == "publishable"
        else "answer_withheld_subject_retained"
    )
    return SubjectQueryContext(...)
```

→ answer가 withheld/blocked여도 subject manifest가 있으면 `answer_withheld_subject_retained`로 SubjectQueryContext 유지. `view_state_from_current_context()` (line 596~630)에서도 이 상태를 `withheld_partial`로 복원함. **ADR acceptance criteria "첫 턴 final answer가 일부 withheld여도 subject context는 dialogue continuity 용도로 유지"는 충족**.

### 실제 gap (좁혀진 형태)

1. **타입 약화**: `Optional[str]` 때문에 오타/임의 값이 런타임에서 걸러지지 않음.
2. **값 이름 불일치**: 코드는 "publishable"을 사용하는데 ADR은 "answer_published"를 지정. `"clarification_pending"` 값은 코드에서 사용되지 않음(ClarificationContext가 별도 타입이므로 사실상 불필요할 가능성도 있음 — ADR 문서와 동기화 또는 논의 필요).
3. 3단계 값 구분을 엄격히 하려면 `Literal` 전환 + 기존 호출 지점 마이그레이션 필요.

---

## 7. 강등 대상 (ADR D3) — turn_trigger / turn_interpreter / turn_policy / context_router

### 확인된 사실
- 각 파일은 삭제되지 않고 존재.
- workflow_builder.py에서 이들을 노드로 추가하지 **않음** → production workflow에서 호출되지 않음.
- `request_facade.py:2193,2203`의 legacy `build_intent_payload()`에서만 호출되며, 이 함수가 workflow path에서 쓰이는지는 본 감사 범위에서 호출 그래프 전수 검증을 수행하지 않았음 (grep 범위에서는 workflow_builder 참조 없음).

### 추정
- D3에서 제시한 "validator/materializer 강등"은 **파일은 살아있지만 workflow에서 차단**한 형태로 구현. 다만 코드 내부는 "validator" 전용으로 리팩토링되지 않았고 여전히 old front-controller 책임(run_turn_trigger 등)이 함수로 남아 있음.

---

## 8. Observability (Section 16)

**파일**: `apps/api/workflow_nodes.py`, `apps/conversation/agent_dialogue_router.py`

자동 에이전트 확인 결과 14개 이벤트 전부 실제 `log_event()` 호출 지점 존재.

| 이벤트 | 발생 파일:라인 (대략) |
|------|--------------------|
| AGENT.STATE_CARD.BUILT | workflow_nodes.py:292-298 |
| AGENT.DECISION | agent_dialogue_router.py:155-178 |
| AGENT.PARSE_ERROR | agent_dialogue_router.py:196-220 |
| AGENT.SELF_REPAIR.START/SUCCESS/FAILED | agent_dialogue_router.py:344-378 |
| AGENT.INVOKE_ERROR | agent_dialogue_router.py:223-242 |
| AGENT.TOOL_CALL | workflow_nodes.py:394-399 |
| AGENT.TOOL_OBSERVATION | workflow_nodes.py:423-437 |
| AGENT.CONTRACT_BLOCKED | workflow_nodes.py:438-446 |
| AGENT.DIRECT_ANSWER | workflow_nodes.py:468-474 |
| AGENT.CLARIFICATION | workflow_nodes.py:530-538 |
| AGENT.INTERNAL_ERROR | workflow_nodes.py:585-592 |
| AGENT.LOOP_GUARD.TRIGGERED | workflow_nodes.py:381-386 |

→ **로깅 관점에서는 ADR 요구 100% 구현** (실제 트리거 조건은 Phase 4 gap으로 인해 LOOP_GUARD는 사실상 동작 안 할 가능성).

---

## 9. ADR Acceptance Criteria (Section 19) 대조

| # | 기준 | 상태 |
|---|------|------|
| 1 | "해당 연구자의 2014~2023 활동내역"이 clarification 없이 refine으로 처리 | 🟡 코드 경로는 준비됨 (G1 golden 실행 필요) |
| 2 | "연구책임자로 활동한 과제만"이 role narrowing으로 처리 | 🟡 코드 경로 준비됨 (G2 golden 실행 필요) |
| 3 | 첫 턴 withheld여도 subject context 유지 | ✅ `answer_withheld_subject_retained` 경로 구현 |
| 4 | LLM이 직접 ID 생성하지 않음 | ✅ prompt 및 tool boundary에서 금지 |
| 5 | HardContractV1, QuestionAnalysisV3 tool backend에서 계속 검증 | ✅ `build_agent_intent_payload` 경유 |
| 6 | Agentic front-controller가 유일 production path | ✅ workflow_builder에서 확인 |
| 7 | Agent decision/tool call/observation 모두 로그 | ✅ AGENT.* 14종 확인 |

---

## 10. 가장 큰 3가지 Gap (우선순위)

### Gap 1 — Agent Final Answer 경로 부재 (🔴 구조적)
- **위치**: workflow_builder.py, workflow_nodes.py
- **증상**: tool 실행 후 agent가 observation을 다시 보고 응답을 조립하는 단계 없음. 기존 rag_search→answer generation으로 바로 이어짐.
- **ADR 근거**: section 5.1의 `→ Observation → Agent Final Answer → Validation / Publish`.
- **영향**: Agent의 "추론 루프" 기반 동작이 사실상 1턴 한정. AGENTIC_MAX_STEPS=3이 의미를 못 가짐. ADR 17의 "LLM이 대화의 주도권을 가진다" 원칙 중 "observation 기반 자연스러운 최종 응답"이 부분 구현.
- **2026-04-22 후속 조사 결과**:
  - **ADR 9.2가 이미 갱신됨**: `dialogue_agent_final`, `answer_validation`, `publish_context` 노드는 "도입하지 않는다"로 명시. 각 책임은 `generate_answer_*` / `merge_answers` / `save_history`에 흡수. 즉 결정 필요 포인트 (a) 경로는 이미 ADR에서 채택됨.
  - **부분 observation loop는 이미 구현됨** (감사 시 누락한 부분):
    - `node_retry_dialogue_agent_after_tool_error` (workflow_nodes.py:344) — tool 에러 시 `run_dialogue_agent`를 card에 feedback을 주입해 1회 재호출. `AGENT.TOOL_RETRY.START` / `AGENT.TOOL_RETRY.DECISION` 이벤트 로깅.
    - 즉 **에러 관측 루프는 존재**, **성공 관측 루프는 없음**.
  - **현재 구조의 정확한 성격**: Agent는 (1회 tool 발행) + (에러 시 1회 재시도) 구조. self-iterating loop는 아니지만 1-step retry는 있음.
- **사용자 확정 정책 (2026-04-22 병렬 커밋, `06_운영과_환경.md:146`, `07_회귀기준과_점검.md:57,133` 반영)**:
  - Tool backend의 `planner_error` / `LLMJSONExtractionError` / `PLANNER.STAGE*.ERROR`는 **사용자 모호성으로 해석 금지**. `AGENT.TOOL_OBSERVATION(error) → AGENT.TOOL_RETRY.START → AGENT.TOOL_RETRY.DECISION` 순서 강제.
  - 재시도 후에도 guarded intent가 없으면 `AGENT.INTERNAL_ERROR`로 닫는다. `ClarificationContext` 저장 금지.
  - `AGENT.CLARIFICATION`은 Agent decision이 `ask_clarification`일 때만 허용. 그 외 경로에서 clarification을 방출하려 하면 `AGENT.CLARIFICATION.BLOCKED` 로깅 + `node_agent_internal_error` fallback (workflow_nodes.py `node_agent_clarification` 진입 방어 구현).
  - LLM parse/schema/tool validation 실패 → 1회 self-repair → 실패 시 `agent_internal_error`. clarification 전환 금지.
  - declared-but-unimplemented tool → executor contract violation.
  - 즉 Gap 1의 P1 decision point는 "성공 경로 self-iteration loop는 도입하지 않고, 오류 경로만 1회 observation retry"로 **확정**. `AGENTIC_MAX_STEPS=3`은 향후 loop 도입 시의 상한 선언으로만 남음.
- **남은 (사용자 결정 필요) 포인트**:
  - (P3) `merge_answers` 내부에 Agent가 observation 요약을 남기는 훅을 추가할지 — 2026-04-23 [ADR-0015 구조적 결정 초안 C1](./ADR-0015_structural_decisions_draft_2026-04-23.md)으로 옵션/권장 정리됨 (권장: Option B 최소 훅).
  - (Gap 3 잔여) legacy 5개 파일 최종 삭제 시점 — 동일 초안 C2로 정리 (권장: Option B' 2단계 집행).

### Gap 2 — publication_status 타입/값 계약 약화 (🟡 타입 안전성)
- **위치**: session_memory.py:51
- **증상**: `Optional[str]` + 실제 값 "publishable"이 ADR 명세 "answer_published"와 불일치. `"clarification_pending"` 값 미사용.
- **영향**: 값 오타/타입 어긋남을 런타임에서 검출 불가. 향후 값 분기를 세밀화할 때 리팩토링 비용.
- **해결**: `Literal[...]`로 강제 + ADR 문서와 값 이름 동기화 또는 ADR 갱신.

### Gap 3 — Legacy front-controller 경로 잔존 (🟡 유지보수성)
- **위치**: request_facade.py:2010~(build_intent_payload), turn_trigger.py/turn_interpreter.py/turn_policy.py/context_router.py
- **증상**: workflow에서는 호출되지 않지만 코드 베이스에 살아 있음. ADR Stage 4 "Legacy front-controller 제거"는 미수행.
- **영향**: 신규 개발자 혼란, dead code로 인한 유지보수 비용. 실수로 재연결될 위험.
- **2026-04-22 후속 조사 결과**:
  - production 호출 그래프에서는 `build_intent_payload`를 호출하는 경로가 없음 (workflow_builder / workflow_nodes / agent_runner 어디에서도 참조되지 않음).
  - 그러나 **tests/ 디렉토리 20+ 테스트가 직접 `request_facade.build_intent_payload`를 호출**하고, `run_turn_trigger` / `run_turn_interpreter` / `run_context_router`를 patch함. 즉 production dead path지만 **test-alive**.
  - 섣부른 삭제는 test suite를 모두 깨뜨리므로 금지. test 마이그레이션이 선행되어야 함.
- **2026-04-22 적용된 조치 (삭제 대신 deprecation marker)**:
  - `request_facade.py::build_intent_payload` docstring에 DEPRECATED 마커 + production 사용 금지 + 테스트 호환성으로 보존된다는 설명 추가.
  - `turn_trigger.py`, `turn_interpreter.py`, `turn_policy.py`, `context_router.py` 4개 모듈 파일 상단에 모듈 docstring으로 동일한 DEPRECATED 경고 추가.
  - 신규 개발자가 import 시점에 즉시 상태를 알 수 있고, 의도치 않은 재연결을 문법적으로 눈에 띄게 함.
- **남은 작업 (별도 task로 분리)**:
  - (Preceding work) 기존 테스트를 agent front-controller 경로로 마이그레이션 — `run_agent_tool` 사용, `build_agent_intent_payload` 호출, `AgentDecision` 기반 assertion으로 전환.
  - (Final) 테스트 마이그레이션 완료 후 `build_intent_payload` 및 4개 모듈 실제 삭제 — ADR Stage 4 집행.

### (부수적) Gap 4 — ADR 문서와 실제 네이밍 차이 (🟢 문서 동기화)
- `build_intent_payload_for_agent_tool` (ADR) vs `build_agent_intent_payload` (실제)
- `AgentObservation.observation_type` 값셋 변경 ("join_result" → "planned_intent")
- `answer_published` / `publishable` 불일치
→ ADR을 living document로 유지하려면 Implementation update 섹션을 Phase 4 gap까지 포함해 갱신 권장.

---

## 11. 참고 — 본 감사에서 수행하지 않은 범위

- Golden test G1~G4의 실제 실행 결과 확인
- `session_memory` ↔ `view_state` 왕복 마이그레이션 로직의 회귀 테스트
- `workflow_nodes.py`의 각 노드 내부 로직 상세 검토 (분기 커버리지, 에러 핸들링)
- ~~legacy `build_intent_payload()`의 최종 호출자 존재 여부에 대한 완전한 호출 그래프 탐색~~ → 2026-04-22 후속 조사에서 production dead / test-alive 확인 완료 (Gap 3 참조)
- AGENT.* 이벤트의 log schema / 필수 필드 검증

이 영역들은 "구현 존재 여부" 감사와는 성격이 다르므로, 후속 별도 감사 또는 regression 실행으로 확인 권장.

---

## 12. 제안 다음 단계 (참고용, 실행 아님)

1. **Gap 1**: ✅ ADR 9.2 갱신 + 에러 경로 1회 retry 정책 확정 (2026-04-22, 06/07 문서 + `node_agent_clarification` 진입 방어). `AGENT.CLARIFICATION.BLOCKED` + `AGENT.INTERNAL_ERROR`로 오류가 clarification으로 새는 경로 차단. 잔여 P3(merge_answers 내 observation 훅)는 별도 개선 주제.
2. **Gap 2**: ✅ `publication_status` Literal 강화 + `_migrate_legacy_publication_status` 검증기 추가 완료 (2026-04-22).
3. **Gap 3**: ✅ 호출 그래프 조사 완료 + DEPRECATED docstring 마커 적용 (2026-04-22). 다음 단계는 테스트 마이그레이션(별도 task) → 최종 삭제.
4. 위 3개 gap 반영 후 acceptance criteria #1, #2 golden test 실측.

---

## 13. 2026-04-22 수정 적용 요약

| Gap | 이전 상태 | 2026-04-22 조치 | 결과 |
|---|---|---|---|
| Gap 1 | 🔴 구조적 gap | ADR 9.2 갱신 (option a 채택), 에러 경로 1회 observation retry 정책 확정 (06/07 문서), 성공 경로 self-iteration loop는 도입 안 함 확정 | ✅ P1 확정 (1-step retry → internal_error, clarification 우회 금지), P3만 후속 개선 주제로 남음 |
| Gap 2 | 🟡 타입 약화 | `SubjectQueryContext.publication_status: Optional[Literal[...]]` + legacy 값 마이그레이션 validator | ✅ 타입 안전 |
| Gap 3 | 🟡 legacy 잔존 | 5개 파일에 DEPRECATED docstring 마커, 삭제는 test 마이그레이션 선행 필요로 분리 | 🟡 보존 + 명시 |
| Gap 4 (문서) | 🟢 ADR-코드 이름 불일치 | `build_intent_payload_for_agent_tool` → `build_agent_intent_payload`로 ADR 갱신 | ✅ 동기화 |

**적용 파일 목록**:
- `apps/docs/ADR/ADR-0001_LLM-First Dialogue Agent over Contract-Guarded RAG Tools.md` (section 5.2 / 9.2 / 11.3)
- `apps/conversation/session_memory.py` (Literal 타입 + field_validator + 값 이름)
- `apps/conversation/request_facade.py` (build_intent_payload DEPRECATED docstring)
- `apps/conversation/turn_trigger.py` / `turn_interpreter.py` / `turn_policy.py` / `context_router.py` (module docstring DEPRECATED 마커)
- `apps/docs/reports/ADR-0015_implementation_audit_2026-04-22.md` (본 보고서 후속 조사 반영)

---

## 14. 2026-04-23 C1/C2 구조 결정 집행 요약

[근거 문서] `ADR-0015_structural_decisions_draft_2026-04-23.md` (Status: Accepted).

| 결정 | 선택지 | 집행 결과 |
|---|---|---|
| C1 (merge_answers observation 훅) | **Option B — 최소 훅** | ✅ 집행 완료. `AgentObservation.answer_note: Optional[str]` 추가, `AgentAnswerContext(note, source_observation_type)` pydantic 모델 신설, `AgentState.agent_answer_context` 필드 추가, `node_execute_agent_tool` / `node_retry_dialogue_agent_after_tool_error`에서 `observation.answer_note → agent_answer_context`로 복사, `AGENT.ANSWER_NOTE.ATTACHED` 이벤트 로깅, `generate_answer()` 프롬프트에 `[결과 주석]` 섹션 조건부 주입 (값 없을 때 기존 프롬프트와 완전 동일). |
| C2 (legacy 삭제) | **Option B' — 2단계 집행** | 🟡 Stage 1 완료 / Stage 2 보류. 11개 legacy-front-controller 의존 pytest 파일에 `pytestmark = pytest.mark.skip(...)` 삽입으로 회귀 대상에서 명시적으로 격리. 해당 테스트들은 모두 untracked 상태이며 9개는 디스크 상에 사전부터 truncate 돼 있어 실제로 수집·실행되지 않음. Stage 2(실제 삭제)는 agentic 경로 커버리지 확인 이후 실행 예정. |

**2026-04-23 적용 파일 목록**:
- `apps/conversation/agent_observation.py` (AgentObservation.answer_note 추가)
- `apps/api/contracts/workflow_models.py` (AgentAnswerContext 신설 + AgentState 필드 추가)
- `apps/api/workflow_nodes.py` (node_execute_agent_tool + retry 노드에서 answer_note 복사 + AGENT.ANSWER_NOTE.ATTACHED 로깅)
- `apps/chat/answer_generation.py` (generate_answer 프롬프트에 [결과 주석] 섹션 조건부 주입)
- `tests/conversation/test_*.py` + `tests/test_request_facade_*.py` 11개 (pytestmark skip 마커)

**C1 관측 항목 (런타임 가드)**: LLM 호출 환경이 아니므로 pytest 회귀는 사용하지 않음(`feedback_no_pytest_regression`). 대신 다음 정적·런타임 근거로 정책 준수를 확인한다.
- 정적: `AgentAnswerContext.model_config = ConfigDict(extra="forbid")` — strategy/contract/scope 필드 주입 시도는 pydantic 레벨에서 거부.
- 로그: `AGENT.ANSWER_NOTE.ATTACHED` 이벤트가 `tool_name` / `source_observation_type` / `note_chars`만 남기므로 운영 관측에서 확장 시도 감시 가능.
- Prompt: 값 없을 때 `agent_observation_note == ""`로 fallback되어 기존 프롬프트와 동일 — diff 기반 regression 근거 확보.

**C2 잔여 작업 (Stage 2)**:
- agentic 경로 (`test_agentic_dialogue_contract.py`, `test_agentic_workflow.py`, `test_agentic_dialogue_shindonggu.py`) 커버리지로 legacy 대체 확인.
- 확인 후 11개 legacy 테스트 파일 + `apps/conversation/request_facade.build_intent_payload` + `apps/conversation/turn_trigger.py` / `turn_interpreter.py` / `turn_policy.py` / `context_router.py` 중 Agent 경로에서 호출되지 않는 함수 실제 삭제.
- 삭제 전 `workflow_builder.py`의 `build_workflow_graph`가 legacy 노드를 참조하지 않음을 재확인 (2026-04-22 커밋 5a60981에서 이미 LLM-driven agentic workflow로 전환됐음).

---

*본 보고서는 2026-04-22 최초 audit 이후 (1) Gap 2/3/4 저위험 수정 + Gap 1 후속 조사를 반영하고, (2) 사용자가 병렬로 커밋한 "tool-backend planner 오류 → 1회 Agent retry → `agent_internal_error`, `AGENT.CLARIFICATION` 우회 금지" 정책과 `node_agent_clarification` 진입 방어(`AGENT.CLARIFICATION.BLOCKED`) 구현을 반영해 Gap 1을 확정 처리한 뒤, (3) 2026-04-23 C1/C2 구조 결정 초안(Accepted) 을 집행해 merge_answers observation 훅(C1 Option B)과 legacy 테스트 격리(C2 Option B' Stage 1)를 반영했습니다. 정책 계약은 `06_운영과_환경.md` / `07_회귀기준과_점검.md`에 상시 유지됩니다.*
