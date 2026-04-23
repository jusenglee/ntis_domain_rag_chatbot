# 01 아키텍처와 흐름 (Architecture & Flow)

이 문서는 NTIS RAG 시스템의 **2층 계약(2-Layer Contract)** 아키텍처 철학과 이를 구현하는 **Agentic RAG 실행 구조**, 그리고 실제 **데이터 흐름**을 정의한다.

---

## 1. 아키텍처 철학: "2층 계약 (2-Layer Contract)"

본 시스템은 **"의도의 진실(L1)"**이라는 견고한 선로 위에, **"행동의 안전(L2)"**이라는 유연한 환승 규칙을 얹은 구조다. 단순히 답을 내는 것을 넘어, 왜 이 답이 나왔는지(L1)와 어떤 보정을 거쳤는지(L2)를 명확히 분리하여 관리한다.

### 1층: 의미적 의도 진실 (L1 Semantic Intent Truth)
*   **정의:** Dialogue Agent가 확정한 사용자의 전략적 대화 의도 및 도구 선택.
*   **불변성:** 시스템 실행의 최상위 기준점으로서, 하위 계층에 의해 기각되거나 수정될 수 없다.
*   **내용:** 조회 대상(Subject), 실행 액션(`action`), 도메인 축(Axes).
*   **목적:** "사용자가 무엇을 원하는가?"에 대한 실질적인 판단 기준(Source of Truth) 제공.

### 2층: 기술적/행동적 실행 계약 (L2 Technical/Behavioral Contract)
*   **정의:** L1 의도를 달성하기 위해 Planner가 생성한 기술적 파라미터와 Orchestrator의 반응 정책.
*   **유연성:** L1의 전략적 방향을 유지하는 범위 내에서 기술적 수단(필터, 검색어)을 최적화하거나 동적으로 경로를 선택.
*   **내용:** Planner의 `IntentContract`(필터, 검색어), Orchestrator의 보정 정책(Fuzzy Fallback 등).
*   **목적:** L1 의도를 실제 데이터 쿼리로 번역하고 실행 불확실성에 대응하는 신뢰 가드레일 유지.

### 완충 원칙: Shock Absorber (ADR-0016)

LLM Dialogue Agent는 자연어 대화 흐름을 유연하게 판단하지만, LLM 출력에는 사소한 부수 파라미터 오류가 섞일 수 있다. 시스템은 이를 새로운 물리 계층으로 처리하지 않고, `Tool Backend Guard`, `Planner Assembly`, `Retrieval Guard`, `Answer Publication Guard` 전반에 적용되는 **완충 원칙(Shock Absorber)**으로 흡수한다. 세부 결정 사항은 [ADR-0016](./ADR/ADR-0016_Agent_Contract_Shock_Absorber.md)을 따른다.

Shock Absorber는 두 가지 핵심 규칙을 갖는다.

*   **Smart Coercion:** `limit`, `display_limit`처럼 표시 형태를 정하는 부수 파라미터만 도메인 규칙에 맞게 좁게 교정한다. 예를 들어 `action=detail` 또는 `output_type=detail`은 `limit=1`, `display_limit=1`로 normalize한다.
*   **L1 불변 보호:** 대상 식별, `pjt_id`/`pjt_no` 축, 도메인 필터, 기관 역할, `SEARCH/LOOKUP/JOIN` 의미는 교정하지 않는다. 이 값이 모호하거나 충돌하면 실행하지 않고 bounded clarification 또는 internal error path로 닫는다.

Smart Coercion은 오류를 숨기는 장치가 아니다. 단일 후보가 확인되지 않은 detail 요청은 `1/1`로 count를 normalize해도 실행하지 않으며, broad `SEARCH_RECOVERY`로 확장하지 않는다.

---

## 2. 시스템 4계층 구조

시스템은 2층 계약 철학을 실현하기 위해 다음과 같은 논리 계층으로 구성된다.

### Agentic migration note

ADR-0001에 따라 대화 제어권은 `Dialogue Agent`가 점진적으로 가져간다.
신규 agent 경로에서는 이전 front-controller 경로 decision을 두지 않는다. 사용자 대상이 실제로 모호할 때만 clarification으로 fail-closed 하고, LLM 출력 parse/schema 오류나 invoke 오류는 사용자 모호성으로 위장하지 않고 `agent_internal_error`로 닫는다.
Planner / Contract / Retrieval은 삭제 대상이 아니라 Agent tool backend의 contract guard로 유지한다.
현재 workflow graph는 `agentic front-controller`일 때 `rule_precheck` 이후 `Dialogue Agent` 경로로 진입한다. Agent의 `direct_answer` / `ask_clarification`은 바로 저장 가능한 답변 artifact를 만들고, `agent_internal_error`는 error artifact를 만들며, `call_tool`은 `agent_tool_executor`가 만든 guarded `IntentPayloadV3` / `QuestionAnalysisV3`를 기존 retrieval pipeline에 넘긴다. Tool backend의 planner/contract 오류는 사용자 모호성으로 노출하지 않고 compact observation으로 Agent에 1회 되돌린다. 재시도 후에도 guarded intent가 없으면 `agent_internal_error`로 닫으며 `ClarificationContext`를 저장하지 않는다.

Agent의 자유도는 대화 판단에 있다. 실행 자유도는 계약으로 제한된다. Agent는 DB filter, 식별자, JOIN 축을 직접 만들지 않고 의미 수준 tool call만 만든다. Tool backend는 이를 검증 가능한 planner/retrieval 계약으로 변환한다.

| 계층 | 역할 | 주요 산출물 | 비고 |
|---|---|---|---|
| **Dialogue Agent** | 전략적 의도 결정 (**L1**) | `AgentDecision` | 사용자의 실질적 의도 진실 확정 |
| **Tool Backend Guard** | L1/L2 완충 및 정렬 | `IntentPayloadV3` | Smart Coercion을 통해 L2를 L1에 정렬 |
| **Planner / Contract** | 기술적 컴파일러 (**L2**) | `IntentContract` | L1 의도를 기술적 파라미터(필터, 축)로 번역 |
| **Orchestrator / Retrieval** | 행동 실행 및 보정 (**L2**) | `ExecutionTrace`, `ToolResult` | 정책 기반 도구 실행과 bounded recovery |
| **Evidence / Display** | 근거/화면 상태 조립 | `canonical_evidence`, `DisplaySnapshot` | raw payload를 prompt-safe evidence와 visible truth로 변환 |
| **Answer Publication Guard** | 최종 검증 및 발행 | `FinalAnswer` | L1 의도와 실행 결과의 최종 정합성 확인 |

### 패키지 구조 (Package Structure)

| 패키지 | 역할 |
|---|---|
| `apps.api` | route, runtime wiring, transport, manifest |
| `apps.planner` | query intent, planner stage/runtime/service |
| `apps.conversation` | follow-up, scope, anchor, session/view-state |
| `apps.retrieval` | retrieval/filter/compile/orchestration/runtime |
| `apps.evidence` | canonical evidence, detail/context/render, result assembly |
| `apps.chat` | answer generation, llm runtime, streaming runner |
| `apps.platform` | shared settings, schemas, storage, provider clients |

---

## 3. 요청 실행 흐름 (Execution Flow)

```text
1. Request Ingress
   └─ API route가 workflow seed와 SessionMemory view를 생성.

2. Dialogue Agent Decision
   └─ Agent가 direct_answer / call_tool / ask_clarification / agent_internal_error를 선택.

3. Guarded Tool Adapter
   └─ Agent tool call을 `IntentPayloadV3` / `QuestionAnalysisV3`로 변환하고 parser-incompatible query token, count contract, tool schema를 검증.

4. Planner / Contract Assembly
   └─ L1 truth 생성. detail count는 `1/1`로 normalize하되 대상 단일성은 별도 guard로 검증.

5. Retrieval Execution
   └─ `SEARCH/LOOKUP/JOIN` 의미를 유지하며 실행. detail-like 요청은 단일 `pjt_id` 후보가 있으면 lookup 정책으로 실행하고, 단일 후보가 없으면 broad `SEARCH_RECOVERY`로 확장하지 않음.

6. Evidence / Display Snapshot
   └─ raw retrieval payload -> canonical_evidence + render_profile + display snapshot 변환. visible order와 canonical identifier 축 정합성 확인.

7. Answer Publication Guard
   └─ groundedness, answer-state consistency, contract-invalid 상태를 검증. contract-invalid이면 LLM 답변 스트리밍 대신 deterministic terminal message.

8. Response / Ops Summary
   └─ 최종 응답 전송 및 REQ.SUMMARY 기록.
```

---

## 4. 운영 및 디버깅 신호 (Observability)

문제가 발생했을 때 로그에서 확인해야 할 주요 신호들이다.

*   **요청 시작:** `REQ.START`
*   **Agent 판단:** `AGENT.STATE_CARD.BUILT`, `AGENT.DECISION`, `AGENT.TOOL_CALL`, `AGENT.TOOL_OBSERVATION`
*   **질의 해석:** `PLANNER.COUNT_CONTRACT`, `PLANNER.PIPELINE`, `PLANNER.ASSEMBLE`
*   **실행 전략:** `RAG.EXECUTION_MANAGER.RESULT`, `RAG.PLAN`, `RAG.RETRIEVE`, `RAG.JOIN.POLICY`
*   **데이터 근거:** `RAG.RESULT.TOP`, `RAG.CONTEXT`, `RAG.CTX`, `DISPLAY.SNAPSHOT.BUILT`
*   **답변 및 종료:** `LLM.RESULT`, `ANSWER.STATE_DIAG`, `REQ.SUMMARY`, `REQ.END`

---

## 5. 계층별 제약 및 금지 사항

| 계층 | 할 수 있는 일 | 하면 안 되는 일 |
|---|---|---|
| **Dialogue Agent** | 대화 의도 판단, tool 선택, 자연스러운 clarification 문구 생성 | DB filter, 식별자, JOIN 축 생성 |
| **Tool Backend Guard** | tool schema 검증, parser-safe query materialization, Smart Coercion | L1 대상/축/필터 교정 |
| **Planner** | 질문 의도 분석, 식별자 추출, detail count normalization | 실행 도중의 보정 정책 결정 |
| **Orchestrator** | 도구 체이닝, 정책 기반 회복 | L1이 정한 식별자나 필터의 임의 변경, detail broad search fallback |
| **Tools** | 명시적 인자에 따른 데이터 조회 | 전체 에이전트 상태(State) 참조 |
| **Answer** | 근거 기반 답변, 검증, publication guard | 검색 계약(Contract)의 사후 변경, contract-invalid 컨텍스트의 LLM 답변 노출 |
