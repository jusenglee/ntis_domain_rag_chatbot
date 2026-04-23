# 02 실행 계약과 전략 규칙 (Contracts & Rules)

이 문서는 Dialogue Agent, Planner, Orchestrator, Retrieval 사이에서 지켜야 하는 **실행 약속(Contract)**과 **세부 전략 규칙**을 명시한다.

---

## 1. 의도 진실 (L1 Intent Truth)

**Dialogue Agent**가 확정한 사용자의 전략적 의도와 도구 선택이 시스템 실행의 최상위 "진실(L1)"이 된다. Planner가 생성하는 `IntentContract`는 이 L1 의도를 실행 가능한 기술적 언어로 번역한 **기술적 실행 계약(L2)**이다.

### L1 의도 요소
*   **실행 액션 (`action`):** `list`, `detail`, `stats` 등 대화 목적.
*   **조회 대상 (`subject`):** 어떤 엔티티(과제, 인물 등)를 다루고자 하는가.
*   **도메인 축 (`axes`):** `pjt_id` vs `pjt_no` 등 식별자 축에 대한 전략적 선택.

### L2 기술적 컴파일 (Planner의 역할)
Planner는 Agent의 L1 의도에 종속(Subordinate)되며, 이를 달성하기 위한 구체적인 기술 명세를 생성한다.
*   **조회 모드 (`mode`):** `SEARCH` (벡터 검색), `LOOKUP` (ID 기반), `JOIN` (관계형 검색).
*   **식별자 맵 (`ids_map`):** `pjt_id`, `pjt_no`, `perf_id` 등 도메인 고유 식별자.
*   **필터 (`filters`):** 사용자 질문에서 명시된 검색 조건 (`lead_org_name` 등).
*   **원시 검색어 (`retrieval_query`):** 검색 엔진에 전달할 핵심 키워드.
*   **표시 계약 (`limit`, `display_limit`, `output_type`):** 답변 형태와 표시 개수. Smart Coercion이 적용되는 주 영역이다.

### Smart Coercion: 계층 간 정렬 (Alignment)

Smart Coercion은 하위 계층(L2 Planner)의 기술적 파라미터 환각이 상위 계층(L1 Agent)의 의도와 충돌할 때, 이를 L1에 맞추어 강제로 정렬하는 매커니즘이다. 상세 사항은 [ADR-0016](./ADR/ADR-0016_Agent_Contract_Shock_Absorber.md)을 참조한다.

허용 (L1 의도에 맞춘 L2 교정):

*   `limit`, `display_limit` 같은 표시/개수 파라미터.
*   Agent가 상세 조회를 결정(`action=detail`)했으나 Planner가 다수 결과(`limit > 1`)를 요청한 경우, 이를 `limit=1`, `display_limit=1`로 normalize.
*   Parser 호환성을 위한 query materialization.

금지 (L1 의도 자체의 변조 금지):

*   에이전트가 확정한 대상 식별 및 도메인 head.
*   에이전트가 결정한 조회 축 및 필터의 핵심 의미.
*   `SEARCH` / `LOOKUP` / `JOIN` 모드 간의 임의 변경 (단, Planner 내부의 기술적 최적화는 허용).

금지 영역에서 충돌이나 모호성이 발견되면 하위 계층이 임의로 교정하지 않는다. 대신 상위 계층인 Agent에게 Observation으로 되돌려 의도를 재확인(Self-correction)하거나 사용자에게 Clarification을 요청한다.

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
*   Entity kind의 키워드, 직시 패턴, 대표 ID 키는 `apps.conversation.entity_registry`의 Pydantic registry entry를 기준으로 관리한다. `ConversationViewState` 저장 모델은 문자열 kind를 보존하므로 새 도메인 kind가 추가되어도 상태 저장 단계에서 폐기하지 않는다.
*   Registry 로드 시 kind 중복, 빈 keyword, invalid regex, 승인되지 않은 keyword collision은 실패해야 한다. 승인된 shared keyword는 kind별 `priority`가 달라야 하며, priority가 낮은 kind가 우선한다.
*   도메인별 시간 축은 registry의 `temporal_keys`로 정의한다. Router는 `candidate.year`에 직접 묶이지 않고 `temporal_keys` 순서에 따라 후보의 시간 값을 비교한다.
*   LLM에 주입하는 후보 수 제한은 프롬프트 예산 제한일 뿐 전체 후보군 탐색 제한이 아니다. 후보 선택은 전체 후보군에서 같은 kind, 명시 이름, 연도, 활성 anchor 우선순위로 1차 회수한 뒤 제한된 수만 prompt payload로 보낸다.
*   `route_context()`는 deterministic-only API다. LLM fallback은 `run_context_router()`에서만 수행하며, 후보 payload는 `index/title/year/lead_org/entity_kind/source`로 제한하고 planner 전략 필드나 내부 ID를 생성하지 않는다. LLM이 `candidates`에 없는 index를 반환하면 런타임에서 거부한다.
*   단일 same-kind 후보처럼 확신 가능한 경우에만 `aggressive_auto` 정책 소스를 남기며 자동 재사용할 수 있다. 이 경우에도 publishability, followup rights, `pjt_id`/`pjt_no` 계약은 우회하지 않는다.
*   다음 턴 참조 truth는 `conversation:v3:{conversation_id}:session_memory.current_context` 하나다. 기존 `conversation:v2:*:history`, `last_canonical_evidence`, `last_render_profile`, `view_state` 키는 load fallback으로 사용하지 않으며, save 시 best-effort delete 대상이다.
*   `view_state`는 렌더링/materialization 상태로 유지할 수 있지만 follow-up 공식 truth가 아니다. Runtime은 `current_context`에서 복원한 view-state projection만 follow-up 해석에 사용한다.

*   Runtime front-control belongs to the Dialogue Agent. `turn_trigger`, `turn_interpreter`, and `turn_policy` are not on the production front-controller path; `SessionMemory.current_context` remains the official next-turn truth.
*   Dialogue Agent 출력 parse/schema/tool validation 실패는 사용자 모호성이 아니다. Router는 1회 schema-only self-repair를 시도하고, 실패하거나 primary LLM invoke가 실패하면 `agent_internal_error`로 종료한다. Tool backend planner 오류(`planner_error`, `LLMJSONExtractionError`, `PLANNER.STAGE*.ERROR`)도 사용자 모호성이 아니므로 `AGENT.CLARIFICATION`으로 변환하지 않는다. 이 경우 compact observation을 Agent에 1회 되돌려 corrected `call_tool`을 유도하고, 재시도 후에도 guarded intent가 없으면 `agent_internal_error`로 닫는다. 이 경로에서는 `ClarificationContext`를 새로 저장하지 않는다.
*   `view_state_from_current_context()` is compatibility materialization for candidate construction and rendering only. It is not the official next-turn truth.
*   Answer/runtime nodes must publish an explicit `next_current_context` for persistence. If this value is missing, session memory persistence fails closed to `EmptyContext`; `view_state` inference is not used as a save-time fallback.

*   `followup_resolution_status="agent_tool"`은 Agent 경유를 나타내는 관측용 상태다. 이 값만으로 follow-up clarification을 생성하거나 retrieval을 차단하면 안 된다.

### Detail / Current-context 규칙

*   `action=detail` 또는 `output_type=detail`은 단일 대상 답변 계약이다. Planner count validation은 `limit=1`, `display_limit=1`로 normalize해야 한다.
*   count가 `1/1`이어도 단일 후보가 확정되지 않으면 detail을 실행하지 않는다. 단일 후보 검증은 `SessionMemory.current_context`, active result scope, visible answer manifest, explicit ID를 기준으로 수행한다.
*   project detail에서 단일 `pjt_id`가 확정되면 runtime은 broad search가 아니라 lookup 정책(`LOOKUP_MISSING_RECOVERY`의 primary detail lookup)으로 실행한다.
*   화면 항목, 대괄호 제목, "상세정보", "그 과제", "2020년에 진행한 프로젝트" 같은 후속 표현은 먼저 current context에서 해소한다.
*   current context에서 후보가 여러 개이면 broad search로 확장하지 않고 Agent clarification observation 또는 사용자 clarification으로 닫는다.
*   detail-like query는 `SEARCH_RECOVERY`로 17건/20건 리스트를 만드는 경로로 내려가면 안 된다.
*   신규 대상명이 명시된 fresh search와 기존 화면/주체 refinement는 분리한다. Agent tool은 신규 검색에 `search_ntis_domain`, 현재 주체/화면 정제에 `refine_current_subject`, 단일 대상 상세에 `lookup_specific_entity` 또는 동등한 detail lookup을 사용해야 한다.

---

## 4. 핵심 실행 불변 약속 (Invariants)

*   **검색 모드 불변:** 실행 도중 `SEARCH`, `LOOKUP`, `JOIN` 의미를 서로 바꾸지 않는다.
*   **L1 진실 보호:** Orchestrator가 L2 정책에 따라 보정을 수행하더라도, L1이 정한 `ids_map`과 `filters`는 최종 답변의 '근거 출처'로서의 자격을 유지해야 한다.
*   **상태 격리:** 도구(Tools)는 에이전트의 전체 상태를 알 필요가 없으며, 오직 명시적으로 전달된 인자만으로 동작한다.
*   **부수 교정 제한:** Smart Coercion은 표시/개수 파라미터에만 적용한다. 대상, 축, 필터, 모드는 교정 대상이 아니다.
*   **Detail fail-closed:** detail 단일 후보가 없으면 retrieval 확장이나 임의 첫 항목 조회로 진행하지 않는다.

---

## 5. 결과 부족 및 실패 처리 (L2 Safety)

*   **No-result vs Error:** 데이터가 없는 것(No-result)은 시스템 오류(Error)가 아니다. 이를 명확히 구분하여 로그에 기록한다.
*   **Fuzzy Fallback:** 검색 결과가 부족할 경우, L1 계약을 훼손하지 않는 범위 내에서 검색 가중치를 조절하거나 보조 도구를 호출하는 L2 정책을 실행할 수 있다.
*   **Internal Error Loop:** schema/tool/planner/backend 오류는 사용자 모호성으로 위장하지 않는다. Compact observation으로 Agent에게 최대 1회 self-correction 기회를 주고, 재시도 실패 시 `agent_internal_error`로 닫는다.
*   **Clarification 경계:** clarification은 사용자 대상이 실제로 모호할 때만 사용한다. 내부 오류나 parser/schema 실패를 `ClarificationContext`로 저장하면 안 된다.
