# 01 아키텍처와 흐름 (Architecture & Flow)

이 문서는 NTIS RAG 시스템의 **2층 계약(2-Layer Contract)** 아키텍처 철학과 이를 구현하는 **4계층 구조**, 그리고 실제 **데이터 흐름**을 정의한다.

---

## 1. 아키텍처 철학: "2층 계약 (2-Layer Contract)"

본 시스템은 **"의도의 진실(L1)"**이라는 견고한 선로 위에, **"행동의 안전(L2)"**이라는 유연한 환승 규칙을 얹은 구조다. 단순히 답을 내는 것을 넘어, 왜 이 답이 나왔는지(L1)와 어떤 보정을 거쳤는지(L2)를 명확히 분리하여 관리한다.

### 1층: 의미적 불변 계약 (L1 Semantic Invariants)
*   **정의:** Planner가 추출한 사용자 질문의 본질적 의도와 도메인 식별자 진실.
*   **불변성:** 실행 도중 절대로 수정되거나 타협될 수 없다.
*   **내용:** 식별자 축(`pjt_id` vs `pjt_no`), 필터(`lead_org_name` 등), 조회 대상(`action`).
*   **목적:** "사용자가 원래 무엇을 원했는가?"에 대한 법적/도메인적 기준점(Source of Truth) 제공.

### 2층: 행동적 안전 계약 (L2 Behavioral Safety)
*   **정의:** L1 의도를 달성하기 위해 Orchestrator가 수행하는 시스템적 반응 정책.
*   **유연성:** L1의 제약(축, 필터)을 훼손하지 않는 범위 내에서 실행 경로를 동적으로 선택.
*   **내용:** 실패 시 보조 도구 호출(Fuzzy Fallback), 타임아웃 처리, 결과 부족 시 가이드라인.
*   **목적:** 실행 불확실성(데이터 없음 등)에 대응하면서도 시스템의 신뢰 가드레일을 유지.

---

## 2. 시스템 4계층 구조

시스템은 2층 계약 철학을 실현하기 위해 다음과 같은 4개 물리 계층으로 구성된다.

| 계층 | 역할 | 주요 산출물 | 비고 |
|---|---|---|---|
| **Planner** | 의도 컴파일러 (L1) | `IntentContract` | 사용자 질문 -> 시스템 언어 번역 |
| **Orchestrator** | 행동 실행기 (L2) | `ExecutionTrace` | 정책 기반 도구 체이닝 및 보정 |
| **Atomic Tools** | 순수 기능 단위 | `ToolResult` | 상태 없는(Stateless) 데이터 조회 |
| **Answer Stage** | 답변 및 검증 | `FinalAnswer` | L1/L2 결합 및 Groundedness 검증 |

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
   └─ API route가 workflow seed 생성.

2. 질의 이해 (L1 Compile)
   └─ question_analysis, IntentContract(L1) 생성

3. 정책 로드 및 전략 수립 (L2 Policy Load)
   └─ Orchestrator가 L1 계약 수신 및 RuntimeStrategyPolicy(L2) 로드

4. Retrieval 실행 (Action)
   └─ L1 의도에 부합하는 도구 호출 및 실패 시 L2 정책에 따른 보정 실행

5. 근거 조립 (Evidence Assembly)
   └─ raw retrieval payload -> canonical_evidence + render_profile 변환

6. 답변 생성 및 검증 (Answer Stage)
   └─ L1 의도 진실과 L2 실행 이력을 결합하여 최종 답변 및 검증 수행

7. Response / Ops Summary
   └─ 최종 응답 전송 및 REQ.SUMMARY 기록
```

---

## 4. 운영 및 디버깅 신호 (Observability)

문제가 발생했을 때 로그에서 확인해야 할 주요 신호들이다.

*   **요청 시작:** `REQ.START`
*   **질의 해석:** `PLANNER.PIPELINE`, `PLANNER.ASSEMBLE`
*   **실행 전략:** `RAG.PLAN`, `RAG.RETRIEVE`, `RAG.JOIN.POLICY`
*   **데이터 근거:** `RAG.RESULT.TOP`, `RAG.CONTEXT`, `RAG.CTX`
*   **답변 및 종료:** `LLM.RESULT`, `REQ.SUMMARY`, `REQ.END`

---

## 5. 계층별 제약 및 금지 사항

| 계층 | 할 수 있는 일 | 하면 안 되는 일 |
|---|---|---|
| **Planner** | 질문 의도 분석, 식별자 추출 | 실행 도중의 보정 정책 결정 |
| **Orchestrator** | 도구 체이닝, 정책 기반 회복 | L1이 정한 식별자나 필터의 임의 변경 |
| **Tools** | 명시적 인자에 따른 데이터 조회 | 전체 에이전트 상태(State) 참조 |
| **Answer** | 근거 기반 답변, 검증 | 검색 계약(Contract)의 사후 변경 |
