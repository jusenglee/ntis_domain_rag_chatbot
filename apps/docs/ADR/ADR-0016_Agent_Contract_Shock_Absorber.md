# ADR-0016: Agent Contract Shock Absorber

## 상태 (Status)
채택됨 (Accepted)

## 맥락 (Context)
현재 NTIS RAG 시스템은 LLM Dialogue Agent가 대화를 주도하고 백엔드의 도메인 도구를 호출하는 구조입니다. 이 과정에서 LLM의 비결정성(Non-determinism)으로 인해 `limit`과 같은 부수적인 파라미터에서 사소한 환각(Hallucination)이 발생할 수 있습니다. 예를 들어, 단일 대상을 조회하는 `action=detail` 상황에서 LLM이 습관적으로 `limit=20`을 요청하는 경우입니다.

기존의 엄격한 계약 검증(`Count Contract`)은 이러한 사소한 오류도 즉각적인 실행 차단(Fail-closed)으로 처리하여, 사용자 경험이 저하되고 시스템이 지나치게 경직되는(Brittle) 문제가 발생하고 있습니다.

## 결정 (Decision)
LLM의 비결정성과 시스템의 결정성 사이의 충돌을 완화하기 위해 **"Shock Absorber (완충 원칙)"**를 도입합니다. 이는 **Dialogue Agent를 시스템의 전략적 의도 진실(L1)**로 격상시키고, **Planner를 기술적 컴파일러(L2)**로 재정의하여 상위 계층의 의도가 하위 계층의 기술적 파라미터를 강제하도록 하는 설계 원칙입니다.

### 1. Smart Coercion (지능형 교정 / 계층 간 정렬)
하위 계층(L2 Planner)의 기술적 파라미터 환각이 상위 계층(L1 Agent)의 전략적 의도와 충돌할 때, 시스템이 이를 L1에 맞추어 강제로 정렬(Alignment)합니다.
*   **교정 허용 (L2 -> L1 정렬):** `limit`, `display_limit`.
*   **상세 조회 정규화:** Agent가 상세 조회를 결정(`action=detail`)한 경우, Planner의 기술적 출력값과 무관하게 시스템이 `limit=1`, `display_limit=1`로 강제 교정합니다.
*   **교정 금지 (L1 불변):** 에이전트가 확정한 대상 식별(Target ID), 조회 축(Axes), 도메인 필터(Filters), 조회 모드(`SEARCH/LOOKUP/JOIN`). 이 영역의 오류는 상위 의도의 훼손으로 간주하여 하위 계층이 임의로 교정하지 않습니다.
*   **관측:** 정규화가 발생하면 `PLANNER.COUNT_CONTRACT`에 `source=planner_coerced`, `count_coerced=1`, 원본 count 값을 함께 기록합니다.

### 2. Detail Guard (상세 조회 보호)
`limit=1`로의 기술적 교정은 상위 의도(L1)를 준수하기 위한 수단일 뿐, 임의의 1건 조회를 정당화하지 않습니다.
*   상세 조회를 실행하기 전, 시스템은 L1이 지목한 대상이 정확히 하나(Single-candidate)인지 별도의 Guard를 통해 엄격히 검증합니다.
*   Project detail에서 단일 `pjt_id` 후보가 확정된 경우, runtime은 broad search가 아니라 `LOOKUP_MISSING_RECOVERY`의 primary detail lookup으로 실행합니다.
*   단일 대상이 확정되지 않은 상태에서의 상세 조회 요청은 broad search(`SEARCH_RECOVERY`)로 확장하지 않고 즉시 차단합니다.
*   차단 시 `RAG.DETAIL.SINGLE_CANDIDATE_GUARD`를 기록하고, `retrieval_runtime_meta.detail_guard_blocked=true`를 반환합니다.

### 3. Internal Error Loop (내부 자기 교정 루프)
L2(Planner/Backend) 수준에서 해결할 수 없는 L1 의도와의 충돌이나 스키마 오류는 사용자에게 즉시 노출하지 않고, 상위 계층인 Agent에게 자기 교정 기회를 부여합니다.
*   오류 발생 시 **"Compact Observation"**을 생성하여 Agent에게 반환합니다.
*   Agent는 이 관측 결과를 바탕으로 최대 1회 재시도(Self-correction)를 수행할 수 있습니다.
*   재시도 실패가 도구/계약/Planner 내부 오류라면 `agent_internal_error`로 종료합니다.
*   사용자가 실제로 대상을 특정하지 못한 모호성이라면 명시적인 `clarification`으로 종료합니다. (ADR-0015 준수)

### 4. People Activity Direct Compile
Agent가 `search_ntis_domain`을 선택했고 tool 인자에서 사람 축, 명시 연구자 anchor, 활동/참여이력 의도가 모두 확인되면 Tool Backend Guard는 stagewise planner LLM 재진입을 생략하고 실행 계약을 직접 컴파일합니다.
*   직접 컴파일 계약은 `QuestionAnalysisV3(mode=LOOKUP, head=people, action=list, output_type=list)`를 사용합니다.
*   기본 필터는 `filters.participant_researcher_name=[subject_name]`이며, 기간과 소속기관이 명시된 경우에만 구조화 필터로 추가합니다.
*   기본 조회 대상은 `ntis_project_v1`, `ntis_perf_v1`입니다.
*   이 경로에서는 `run_question_analysis`, Stage1, Stage1.5, Stage2 planner LLM을 호출하지 않습니다.
*   `followup_resolution_status`는 follow-up 의미 전용 필드로 유지하고, Agent/tool 관측 정보는 `tool_execution_source`와 `planner_llm_skipped` 같은 별도 메타에 기록합니다.
*   fast path 조건이 충족되지 않는 people/generic topic search는 기존 planner path를 유지합니다.

## 결과 (Consequences)
*   **장점:** 계층 간 위계(Agent > Planner)가 명확해짐에 따라, LLM의 사소한 파라미터 실수로 인한 불필요한 답변 보류를 방지하고 시스템의 가용성이 향상됩니다.
*   **단점:** "조용한 교정"이 발생하므로, 실행 추적(`execution_trace`) 시 실제 Planner의 원시 출력값과 교정 후의 최종 계약값을 명확히 구분하여 기록해야 합니다.
*   **주의:** L1 의도(전략적 대상, 축, 필터)에 대한 보호는 여전히 엄격하므로, 시스템의 도메인 안전성은 훼손되지 않습니다.
