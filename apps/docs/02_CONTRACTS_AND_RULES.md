# 02 실행 계약과 전략 규칙 (Contracts & Rules)

이 문서는 Planner, Orchestrator, Retrieval 사이에서 지켜야 하는 **실행 약속(Contract)**과 **세부 전략 규칙**을 명시한다.

---

## 1. 의도 진실 (L1 Intent Truth)

Planner가 생성하는 `IntentContract`는 시스템 실행의 유일한 법적/도메인적 근거가 된다.

### 핵심 명세 필드
*   **조회 모드 (`mode`):** `SEARCH` (벡터 검색), `LOOKUP` (ID 기반), `JOIN` (관계형 검색).
*   **식별자 맵 (`ids_map`):** `pjt_id`, `pjt_no`, `perf_id` 등 도메인 고유 식별자.
*   **필터 (`filters`):** 사용자 질문에서 명시된 검색 조건 (`lead_org_name` 등).
*   **원시 검색어 (`retrieval_query`):** 검색 엔진에 전달할 핵심 키워드.

---

## 2. 식별자 계약 (Identifier Semantics)

식별자 혼용은 시스템 신뢰도를 파괴하는 가장 큰 원인이다. 아래 규칙을 엄격히 준수한다.

### `pjt_id` vs `pjt_no`
*   **`pjt_id` (Instance):** 개별 과제를 가리키는 고유 ID. `detail` 조회 시 반드시 이 축을 사용한다.
*   **`pjt_no` (Group):** 같은 번호를 가진 여러 연차의 과제를 묶는 식별자. `stats`나 `perf` 조인 시 기본 축이 된다.
*   **규칙:** 한 질문 내에서 둘의 역할을 혼용하지 않으며, 지원하지 않는 alias(예: `RJT_ID`)를 임의로 매핑하지 않는다.

---

## 3. Follow-up 해석 기준

후속 질문(Follow-up)은 이전 대화 맥락을 기반으로 대상을 특정하는 과정이다.

### 해석 우선순위
1.  **명시적 ID (Explicit ID):** 질문에 포함된 직접적인 ID 값.
2.  **활성 하위 앵커 (Active Child Anchor):** 현재 보고 있는 상세 정보의 하위 엔티티.
3.  **목록 순번 (Ordinal / Source Reference):** "1번 과제", "첫 번째 항목" 등.
4.  **최근 언급 (Recent Mentions):** 최근 답변에서 언급된 대상.

### 실행 규칙
*   `selected_candidate_ids`는 반드시 현재 대화 상태(`ConversationViewState`)에 포함된 후보군 내에서 선택되어야 한다. 후보군 밖의 ID를 임의로 발명하지 않는다.
*   대상 선택(무엇을 가리키는가)은 pre-planner 단계에서 확정하며, 이후 planner는 해당 대상을 바탕으로 실행 전략(어떻게 찾을 것인가)을 수립한다.
*   질문 안에 `신동구 연구자`, `한국과학기술정보연구원 기관`처럼 명시 주체명과 주체 축 cue가 함께 있으면 `ExplicitNamedSubjectSeed`로 본다. 이 경우 이전 ambiguous context나 stale manifest를 재사용하지 않고 fresh search로 진입한다.
*   `2010~2015년도의 활동내역은?`, `논문만`, `다른 연도 활동`처럼 주체명 없이 필터만 바뀐 질문은 직전 current subject가 people/org로 확정되어 있을 때만 `SubjectRefinement`로 처리한다. 이는 `1번`, `출처 2`, `그 항목` 같은 `ReferenceFollowup`과 분리한다.
*   질문의 대상 축이 명시된 경우 후보 해석은 같은 entity kind로 fail-closed 한다. 예를 들어 people 질문에 people 후보가 없으면 project 후보로 fallback하지 않고 clarification 또는 fresh path로 빠진다.

---

## 4. 핵심 실행 불변 약속 (Invariants)

*   **검색 모드 불변:** 실행 도중 `SEARCH`, `LOOKUP`, `JOIN` 의미를 서로 바꾸지 않는다.
*   **L1 진실 보호:** Orchestrator가 L2 정책에 따라 보정을 수행하더라도, L1이 정한 `ids_map`과 `filters`는 최종 답변의 '근거 출처'로서의 자격을 유지해야 한다.
*   **상태 격리:** 도구(Tools)는 에이전트의 전체 상태를 알 필요가 없으며, 오직 명시적으로 전달된 인자만으로 동작한다.

---

## 5. 결과 부족 및 실패 처리 (L2 Safety)

*   **No-result vs Error:** 데이터가 없는 것(No-result)은 시스템 오류(Error)가 아니다. 이를 명확히 구분하여 로그에 기록한다.
*   **Fuzzy Fallback:** 검색 결과가 부족할 경우, L1 계약을 훼손하지 않는 범위 내에서 검색 가중치를 조절하거나 보조 도구를 호출하는 L2 정책을 실행할 수 있다.
