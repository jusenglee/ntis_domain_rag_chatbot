# NTIS RAG 챗봇 온보딩

이 문서는 신규 기여자가 10분 안에 시스템의 진입점, 핵심 계약, 운영 점검 순서를 잡기 위한 입구 문서다.

## Source of Truth

- 문서 루트: `apps/docs`
- 코드 진입점: `apps/api/routes.py`
- 함께 읽을 문서: `README.md`, `01_ARCHITECTURE.md`, `02_CONTRACTS_AND_RULES.md`, `07_회귀기준과_점검.md`

## 시스템 한 줄 요약

`사용자 질문 -> Dialogue Agent 판단 (L1) -> guarded tool adapter -> planner/contract assembly (L2) -> retrieval 실행 -> canonical evidence/display snapshot -> answer publication guard`

핵심 원칙은 **Agent freedom with contract guard**다. Agent는 사용자의 전략적 의도(L1)를 결정하고, 하위 계층은 이를 기술적 계약(L2)으로 구체화하여 실행한다.

## 가장 먼저 알아야 할 용어

| 용어 | 의미 |
|---|---|
| `SEARCH / LOOKUP / JOIN` | 검색 모드. retrieval 전략의 최상위 계약 |
| `canonical evidence` | raw 검색 결과를 prompt-safe 근거로 정규화한 결과 |
| `output_type` | `summary/detail/list/stats/relation/comparison/series` 표현 계약 |
| `follow-up` | 이전 턴의 대상이나 맥락을 이어받는 후속 질의 |
| `active_scope` | 현재 보고 있는 목록/상세/하위 엔티티 상태 |
| `visible_answer_manifest` | 사용자가 실제로 본 목록 순서와 항목의 truth |
| `Shock Absorber` | LLM 비결정성과 엄격 계약 사이의 완충 원칙 |
| `Smart Coercion` | `limit/display_limit` 같은 부수 파라미터만 제한적으로 교정하는 규칙 |
| `single-candidate guard` | detail 실행 전에 대상이 정확히 하나인지 확인하는 guard |
| `contract-invalid` | 실행 계약이 깨져 LLM 답변 생성을 시작하면 안 되는 상태 |
| `pjt_id` | 과제 instance key |
| `pjt_no` | 과제 group key |

## 추천 읽기 순서

1. `README.md`
2. `01_ARCHITECTURE.md`
3. `02_CONTRACTS_AND_RULES.md`
4. `03_BEHAVIORAL_SAFETY.md`
5. `04_TOOLING_STANDARDS.md`
6. `06_운영과_환경.md`
7. `07_회귀기준과_점검.md`

코드 읽기 순서:

1. `apps/api/routes.py`
2. `apps/conversation/request_facade.py`
3. `apps/retrieval/retrieval_workflow.py`
4. `apps/conversation/view_state.py`
5. `apps/chat/answer_generation.py`

## 요청 1건 흐름

```text
사용자 질문
  -> Dialogue Agent가 사용자의 전략적 의도(L1)와 도구 선택을 확정
  -> agent tool adapter가 L1 의도에 기반한 guarded IntentPayloadV3 생성
  -> planner/contract assembly가 L1 의도를 기술적 계약(L2)으로 컴파일하고 Smart Coercion 적용
  -> retrieval이 L1/L2 계약을 준수하여 실행
  -> evidence가 raw payload를 canonical evidence와 display snapshot으로 정규화
  -> answer publication guard가 L1 의도와 최종 결과의 정합성 검증
  -> REQ.SUMMARY와 각 단계 운영 로그 기록
```

## 기준 파일 지도

| 확인하고 싶은 것 | 파일 |
|---|---|
| 라우트와 응답 surface | `apps/api/routes.py` |
| follow-up / scope orchestration | `apps/conversation/request_facade.py` |
| Agent decision / tool adapter | `apps/conversation/agent_dialogue_router.py`, `apps/conversation/agent_tool_executor.py` |
| scope 분류 | `apps/conversation/scope_resolver.py` |
| anchor/ordinal/source-reference 해석 | `apps/conversation/followup_anchor.py` |
| planner runtime | `apps/planner/planner_runtime.py` |
| retrieval 전체 흐름 | `apps/retrieval/retrieval_workflow.py` |
| canonical evidence 조립 | `apps/evidence/canonical_evidence.py` |
| prompt envelope / packing | `apps/evidence/prompt_evidence_envelope.py`, `apps/evidence/context_packer.py` |
| 대화 상태와 visible manifest | `apps/conversation/view_state.py` |
| answer generation / merge | `apps/chat/answer_generation.py`, `apps/chat/answer_merge.py` |
| 환경값 | `apps/platform/settings.py` |

## 문제 진단 순서

1. `REQ.START`에서 질문, override, conversation id를 확인한다.
2. `AGENT.STATE_CARD.BUILT`, `AGENT.DECISION`, `AGENT.TOOL_CALL`에서 Agent가 새 검색인지 current-context refinement인지 판단했는지 본다.
3. `PLANNER.COUNT_CONTRACT`, `PLANNER.*`에서 `action/output_type`, count normalization, strategy drift 여부를 본다.
4. `RAG.EXECUTION_MANAGER.RESULT`, `RAG.RETRIEVAL_QUERY.ACTUAL`, `RAG.RESULT`, `RAG.CONTEXT`에서 detail 요청이 broad `SEARCH_RECOVERY`로 새지 않았는지 확인한다.
5. `DISPLAY.SNAPSHOT.BUILT`에서 visible/canonical order와 count가 맞는지 본다.
6. `LLM.RESULT`, `ANSWER.STATE_DIAG`에서 groundedness, answer-state consistency, degraded 여부를 본다.
7. `REQ.SUMMARY`에서 최종 latency, fallback, output_type, count 관련 요약을 확인한다.

## 절대 잊지 말아야 할 규칙

| 규칙 | 이유 |
|---|---|
| Dialogue Agent가 정한 전략적 의도(Action, Subject)는 하위 계층에서 바꾸지 않는다 | L1 의도 진실 보호 |
| `pjt_id`와 `pjt_no`는 섞지 않는다 | instance/group 의미가 다르다 |
| `lead_org_name`, `participant_org_name`, `people_affiliation_org_name`는 다른 역할이다 | 조직 의미 보존 |
| raw payload를 LLM에 직접 넣지 않는다 | canonical evidence와 render profile을 반드시 거친다 |
| retrieval metadata 전체를 답변 프롬프트에 노출하지 않는다 | prompt-safe 사실 필드만 사용한다 |
| `action=detail` 또는 `output_type=detail`은 `limit/display_limit=1/1`로 normalize한다 | detail은 단일 대상 답변 계약이다 (Smart Coercion) |
| 단일 후보가 확인되지 않은 detail은 broad search로 확장하지 않는다 | 엉뚱한 후보 컨텍스트 투입 방지 (Detail Guard) |
| 시스템/tool 오류는 사용자 모호성 clarification으로 위장하지 않는다 | 내부 오류와 사용자 모호성 분리 (Internal Error Loop) |
