# ADR-0015 구조적 결정 초안 (C1, C2) — 2026-04-23

**Status:** Accepted (2026-04-23, 승주 승인 "C 진행")
**Date:** 2026-04-23
**Deciders:** 승주 (프로젝트 오너)
**Context source:** `apps/docs/reports/ADR-0015_implementation_audit_2026-04-22.md` — Gap 1 / Gap 3 잔여 결정 포인트

본 문서는 ADR-0015 감사에서 "사용자 확인 필요"로 분리된 두 가지 구조적 결정(C1, C2)에 대한 옵션·트레이드오프·권장안 초안이다. 확정 시 이 문서의 Status를 Accepted로 올리고, 결정 사항은 ADR-0001 또는 별도 ADR로 승격한다.

---

## C1. `merge_answers` 내부에 Agent observation 훅을 도입할지

### Context

- 현재 워크플로우: `run_dialogue_agent → execute_agent_tool → (성공) judge_knowledge_sufficiency → rag_search → generate_answer_gemma/solar → join_answers → merge_answers → save_history`.
- Agent는 **tool decision 단계에만** 참여한다. 이후 답변 생성(`generate_answer_*` + `merge_answers`)은 기존 deterministic pipeline이 `AgentObservation`을 직접 참조하지 않고 `context` / `question_analysis` 기반으로 답변을 만든다.
- ADR-0015 section 5.1 원칙: "LLM이 대화 주도권을 가진다" / section 17: "observation 기반 자연스러운 최종 응답". 현 구현은 전자만 충족.
- 2026-04-22 ADR 9.2 갱신으로 `dialogue_agent_final` 노드는 "도입 안 함"이 이미 확정됨 (사용자 결정).
- 잔여 미결 질문: "Agent의 observation이 답변 프롬프트에 **부분적으로라도** 흘러가게 할지".

### Decision

**권장: Option B (최소 훅) — observation 요약 필드만 answer prompt context에 주입, 노드 추가 없음.**

### Options Considered

#### Option A — 변경 없음 (status quo)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low (0) |
| Cost | 0 |
| Scalability | 영향 없음 |
| Team familiarity | 기존 코드 그대로 |

**Pros**
- 구현 비용 0, 회귀 리스크 0.
- 현재 prompt/context 계약을 일절 건드리지 않음.

**Cons**
- ADR-0015의 "observation 기반 자연스러운 답변" 원칙을 충족 못 함 (여전히 retrieval이 blind로 답변 생성).
- Agent가 판별한 `planned_intent`의 의도·제약(role, time window 등)이 답변 문구에 직접 반영되지 않음 → 답변 tone/정확성 개선 여지 미확보.

#### Option B — 최소 훅 (observation 요약 필드만 prompt context로 주입)

| Dimension | Assessment |
|-----------|------------|
| Complexity | Low-Med |
| Cost | 작음 (prompt + state 필드 1~2개) |
| Scalability | 영향 없음 |
| Team familiarity | 기존 merge/generate 구조 유지 |

**구체안**
- `AgentObservation`에서 답변 prompt에 흘릴 수 있는 요약 필드를 정의 (예: `observation.summary`, `observation.planned_intent_notes`, `observation.confidence`).
- `AgentState`에 `agent_answer_context: Optional[AgentAnswerContext]` 추가 (hardcoded 계약).
- `node_execute_agent_tool` / `node_retry_dialogue_agent_after_tool_error` 출력 시 `agent_answer_context` 채움.
- `node_generate_answer_*`의 prompt 템플릿에 `{agent_observation_note}` placeholder 추가, 값이 없으면 빈 문자열.
- `merge_answers`에서는 아무 것도 추가로 하지 않음 (prompt 주입은 generate 단계에서 이미 일어남).

**Pros**
- 노드/엣지 변경 없음 → 회귀 표면 작음.
- Agent 참여가 "프롬프트 맥락" 수준에서 끝나 기존 answer contract(`visible_answer_manifest`, `publication_gate`)를 건드리지 않음.
- ADR 원칙("LLM-first + contract-guarded RAG")과 정합: LLM이 tool 이후에도 답변 prompt 생성 맥락에 등장.

**Cons**
- prompt에 들어가는 observation 요약이 정확/간결해야 함. 과장되면 Planner truth와 충돌 가능.
- 새 prompt 필드가 비어 있을 때 기본 답변이 이전과 동일해야 하므로 fallback 테스트(정적) 필요.

#### Option C — Agent Final 노드 도입 (완전 observation loop)

| Dimension | Assessment |
|-----------|------------|
| Complexity | High |
| Cost | 큼 (새 노드, 새 contract, 새 prompt 2종) |
| Scalability | latency/토큰 비용 증가 |
| Team familiarity | 기존 pipeline 대폭 재편 |

**Pros**
- ADR 원안의 "Agent → Observation → Agent Final Answer" 루프 완전 구현.
- Agent가 답변 문구까지 주도 → 대화 일관성 최고.

**Cons**
- 현재 ADR 9.2가 "dialogue_agent_final 도입 안 함"으로 이미 결정.
- `generate_answer_gemma`/`generate_answer_solar` 병렬 구조, HardContractV1 검증, publication gate 로직과 충돌 가능 → 재설계 비용 큼.
- ADR-0015 section 10의 Runtime Guardrails(토큰/latency 상한)와 tension.

### Trade-off Analysis

| 기준 | A (현상유지) | B (최소 훅) | C (Agent Final) |
|---|---|---|---|
| ADR 원칙 적합도 | 부분 | 높음 | 가장 높음 |
| 구현 비용 | 0 | 소 | 대 |
| 회귀 표면 | 0 | 작음 | 큼 |
| latency 비용 | 0 | 0 | +LLM 1회 |
| 이미 결정된 방향과의 정합 | 0 | ○ | × (9.2 결정과 충돌) |

### Consequences (Option B 채택 시)

**쉬워지는 것**
- 답변 문구에 Agent decision이 참조한 맥락(역할, 기간, 주제 지속 여부 등)을 자연스럽게 녹일 수 있음.
- 향후 C 방향으로 확장할 때 이 훅을 기반으로 단계적 확장 가능.

**어려워지는 것**
- `AgentObservation` contract에 "prompt용 요약 필드"가 추가되어 contract 계약이 소폭 확장.
- prompt template 변경으로 기존 output 품질 변동 가능 (LLM 호출 불가 환경이라 정적 검증만 가능).

**재방문 필요 시점**
- 실제 서비스에서 답변 품질 회귀가 관측되면 B → A 롤백 또는 B → C 확장 결정.

### Action Items (Option B 채택 전제)

1. [x] `AgentObservation` 모델에 `answer_note: Optional[str]` 필드 추가 — `apps/conversation/agent_observation.py` (2026-04-23).
2. [x] `apps/api/contracts/workflow_models.py`에 `AgentAnswerContext` 모델 신설 + `AgentState.agent_answer_context` 필드 추가 (2026-04-23).
3. [x] `generate_answer()` 함수에서 `{agent_observation_note}` 역할을 하는 `[결과 주석]` 섹션을 조건부 주입 — `apps/chat/answer_generation.py` (2026-04-23). 값 없을 때 기존 프롬프트와 완전 동일.
4. [x] `node_execute_agent_tool` / `node_retry_dialogue_agent_after_tool_error`에서 `observation.answer_note → agent_answer_context` 복사 + `AGENT.ANSWER_NOTE.ATTACHED` 이벤트 기록 — `apps/api/workflow_nodes.py` (2026-04-23).
5. [x] `generate_answer`가 `agent_answer_context`가 없을 때 기존 동작과 동일하게 fallback — `agent_observation_note == ""`로 초기화해 f-string 합성 시 빈 문자열이 들어가므로 diff 기반 regression 가능 (2026-04-23).
6. [x] ADR-0001 section 9.2에 "Option B 훅" 반영 + 본 문서 Status를 Accepted로 전환 (2026-04-23).

---

## C2. Legacy front-controller(Gap 3) 최종 삭제 시점

### Context

- 대상: `apps/conversation/request_facade.py::build_intent_payload`, 그리고 `turn_trigger.py` / `turn_interpreter.py` / `turn_policy.py` / `context_router.py` 4개 모듈.
- 2026-04-22 상태: production workflow에서 미사용. 5개 파일에 DEPRECATED docstring 마커 추가됨. tests/ 디렉토리의 20+ 테스트가 `build_intent_payload`를 직접 호출하고 `run_turn_trigger` / `run_turn_interpreter` / `run_context_router`를 patch.
- 2026-04-23 신규 제약: 사용자 환경에서 **LLM 호출이 실제로 발생하지 않음**. LLM 경유 경로를 검증하는 pytest는 "의미 없다"고 명시적으로 기각됨 (feedback memory 등록).
- 즉 "테스트가 legacy 경로에 의존한다"는 이유가 완화된다. 테스트 자체가 현 환경에서 의미있는 regression 신호를 못 낸다면, 그 테스트를 이유로 legacy 코드를 보존할 근거도 약해진다.

### Decision

**권장: Option B' (2단계 집행)**
- 1단계 (저위험, 즉시 가능): legacy 의존 테스트를 **식별 + pytest skip 마커**로 일괄 비활성화. 테스트 자체는 당장 삭제하지 않음(참고 문서 가치).
- 2단계 (별도 세션, 집행 확정 후): legacy 5개 파일 실제 삭제 + skip 마커 단 테스트 파일 정리.

### Options Considered

#### Option A — 현상 유지 (DEPRECATED 마커만)

| Dimension | Assessment |
|-----------|------------|
| Complexity | 0 |
| Cost | 0 |
| Risk | Low |

**Pros**
- 지금 당장 건드릴 게 없음.

**Cons**
- 신규 개발자가 여전히 4개 모듈을 실수로 import할 수 있음.
- ADR-0015 Stage 4 "Legacy front-controller 제거"가 계속 미수행.
- "DEPRECATED 마커만 있고 실제로는 살아있다"는 상태가 장기화되면 마커가 마모됨.

#### Option B — 테스트 skip + 코드 즉시 삭제 (한 세션 집행)

**Pros**
- Stage 4 즉시 수행.
- 코드 베이스 정리 효과 최대.

**Cons**
- 테스트 skip 처리와 코드 삭제가 한 commit에 묶이면 rollback 단위가 커짐.
- 예상 외 import 의존이 드러나면 복구 비용.

#### Option B' — 2단계 집행 (권장)

- **1단계**: legacy 테스트 인벤토리 + skip 마커 + reason 주석 ("tests/ legacy 경로, LLM 호출 가정으로 현 환경에선 의미 제한"). 코드 삭제 없음. commit 단위 작음.
- **2단계**: 별도 세션에서 실제 코드 삭제. 1단계에서 drop되는 테스트가 명확해진 뒤이므로 rollback 단위 제어 가능.

**Pros**
- 각 단계가 독립적이고 rollback 단위 작음.
- 1단계는 오늘 당장 할 수 있는 저위험 작업.
- 2단계 집행 시점을 사용자 일정에 맞춰 분리 가능.

**Cons**
- 완전 정리까지 두 번의 작업 필요.

#### Option C — 테스트를 agent 경로로 재작성 후 코드 삭제

**Pros**
- 회귀 안전망 최대.

**Cons**
- LLM 호출 불가 환경에서 재작성 테스트의 의미 제한 (feedback memory 근거).
- 재작성 비용 20+ 테스트 × 각 평균 1~2시간 = 큰 작업.

### Trade-off Analysis

| 기준 | A | B | B' (권장) | C |
|---|---|---|---|---|
| ADR Stage 4 집행도 | 0 | 100% | 50→100% (단계) | 100% |
| 즉시 변경량 | 0 | 큼 | 작음 | 매우 큼 |
| 회귀 리스크 | 0 | 중 | 저 | 저 |
| 환경 제약 정합 | 낮음 | 중 | 높음 | 낮음 |

### Consequences (Option B' 채택 시)

**쉬워지는 것**
- 신규 개발자가 legacy 경로를 실수로 재사용할 문법적 마찰이 더 커짐 (DEPRECATED docstring + 테스트 skip 이유서).
- 2단계 집행 시 "어떤 테스트가 legacy 때문에 깨질지"가 1단계에서 이미 전수 확인됨.

**어려워지는 것**
- 1단계 skip 마커만 쌓이고 2단계가 무기한 지연되는 "zombie skip" 리스크. mitigation: 1단계 커밋에 2단계 예정 ADR 링크 달기.

**재방문 필요 시점**
- 1단계 이후에도 legacy 경로를 참조하는 신규 코드가 등장하면 skip이 아닌 delete로 즉시 전환.

### Action Items (Option B' 1단계)

1. [x] `tests/conversation/` 및 `tests/` 하위에서 legacy 의존 테스트 인벤토리 완료 — 총 11개 파일 식별 (2026-04-23).
2. [x] 각 대상 파일 최상단에 `pytestmark = pytest.mark.skip(reason="Legacy front-controller path (ADR-0015 Stage 4). Targeted for removal per ADR-0015_structural_decisions_draft_2026-04-23 C2 Option B' stage 2. 승주 환경에서 LLM 호출 불가로 회귀 검증 의미 제한 (feedback_no_pytest_regression).")` 추가 완료 (2026-04-23). 11개 파일 모두 untracked 상태이며 9개는 사전부터 디스크 truncate로 수집되지 않음 — skip 마커는 문서적 의도 표시 역할.
3. [x] 본 문서 Status를 Accepted로 전환 및 1단계 완료 표시 (2026-04-23).
4. [ ] 2단계 Action은 별도 세션에서 제출 — legacy 5개 파일 (`request_facade.py::build_intent_payload`, `turn_trigger.py`, `turn_interpreter.py`, `turn_policy.py`, `context_router.py`에서 Agent 경로 미사용 함수) 실제 삭제, skip-only 테스트 파일 정리, 감사 보고서 dashboard 갱신.

**1단계 skip 마커 삽입 파일 목록 (11개)**:
- `tests/conversation/test_clarification_labeler.py`
- `tests/conversation/test_clarification_prose.py`
- `tests/conversation/test_context_router.py`
- `tests/conversation/test_followup_candidate_resolution.py`
- `tests/conversation/test_followup_unification.py`
- `tests/conversation/test_turn_interpreter.py`
- `tests/conversation/test_turn_policy_llm_guard.py`
- `tests/conversation/test_turn_trigger_current_context.py`
- `tests/test_request_facade_child_detail_followup.py`
- `tests/test_request_facade_context_router.py`
- `tests/test_request_facade_turn_policy.py`

---

## 공통 Consequences & 승격 경로

- 본 초안의 결정이 Accepted되면 **ADR-0001** 문서에 implementation update 섹션을 추가하거나 **ADR-0016 (후속)**로 승격한다. 선택은 승주 결정.
- C1 Option B와 C2 Option B'는 독립적이라 한쪽만 채택해도 정합성 문제 없음.
- 두 결정 모두 "LLM 호출 불가 → pytest 회귀 금지" 선호를 반영해 **정적 guard + ADR/계약 문서 중심**으로 안전망을 설계.

---

*2026-04-23 승주 승인으로 Accepted. ADR-0001 section 9.2에 implementation update로 반영하며, C2 1단계(테스트 skip 마커)는 동일 세션에서 집행한다. C1 코드 변경 및 C2 2단계(코드 삭제)는 ADR 반영 후 순차 집행.*
