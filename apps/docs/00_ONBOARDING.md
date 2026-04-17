# NTIS RAG 챗봇 온보딩

이 문서는 신규 기여자가 10분 안에 시스템의 진입점, 핵심 계약, 운영 점검 순서를 잡기 위한 입구 문서다.

## Source of Truth

- 문서 루트: `apps/docs`
- 코드 진입점: `apps/api/routes.py`
- 함께 읽을 문서: `README.md`, `01_아키텍처와_흐름.md`, `02_실행계약과_전략규칙.md`, `04_회귀기준과_점검.md`

## 시스템 한 줄 요약

`사용자 질문 -> follow-up/scope 해석 -> planner 전략 결정 -> retrieval 실행 -> canonical evidence 정규화 -> answer/streaming`

핵심 원칙은 retrieval-first다. 무엇을 어떻게 찾을지 먼저 고정하고, raw 결과를 canonical evidence로 정리한 뒤, 마지막에만 답변을 표현한다.

## 가장 먼저 알아야 할 용어

| 용어 | 의미 |
|---|---|
| `SEARCH / LOOKUP / JOIN` | 검색 모드. retrieval 전략의 최상위 계약 |
| `canonical evidence` | raw 검색 결과를 prompt-safe 근거로 정규화한 결과 |
| `output_type` | `summary/detail/list/stats/relation/comparison/series` 표현 계약 |
| `follow-up` | 이전 턴의 대상이나 맥락을 이어받는 후속 질의 |
| `active_scope` | 현재 보고 있는 목록/상세/하위 엔티티 상태 |
| `visible_answer_manifest` | 사용자가 실제로 본 목록 순서와 항목의 truth |
| `pjt_id` | 과제 instance key |
| `pjt_no` | 과제 group key |

## 추천 읽기 순서

1. `README.md`
2. `01_아키텍처와_흐름.md`
3. `02_실행계약과_전략규칙.md`
4. `03_운영과_환경.md`
5. `04_회귀기준과_점검.md`

코드 읽기 순서:

1. `apps/api/routes.py`
2. `apps/conversation/request_facade.py`
3. `apps/retrieval/retrieval_workflow.py`
4. `apps/conversation/view_state.py`
5. `apps/chat/answer_generation.py`

## 요청 1건 흐름

```text
사용자 질문
  -> request_facade가 follow-up / scope 해석
  -> planner가 strategy 조립
  -> retrieval이 strategy 그대로 실행
  -> evidence가 raw payload를 canonical evidence로 정규화
  -> chat/streaming이 사용자 응답 생성
  -> REQ.SUMMARY와 각 단계 운영 로그 기록
```

## 기준 파일 지도

| 확인하고 싶은 것 | 파일 |
|---|---|
| 라우트와 응답 surface | `apps/api/routes.py` |
| follow-up / scope orchestration | `apps/conversation/request_facade.py` |
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
2. `PLANNER.*`에서 질의 해석과 strategy drift 여부를 본다.
3. `RAG.RETRIEVAL_QUERY.RESOLUTION`, `RAG.RESULT`, `RAG.CONTEXT`에서 검색과 evidence 흐름을 본다.
4. `LLM.RESULT`에서 groundedness, answer-state consistency, degraded 여부를 본다.
5. `REQ.SUMMARY`에서 최종 latency, fallback, output_type, count 관련 요약을 확인한다.

## 절대 잊지 말아야 할 규칙

| 규칙 | 이유 |
|---|---|
| planner가 정한 `mode`, `relation`, `target_cols`, `join_key_mode`는 이후 단계에서 바꾸지 않는다 | drift 방지 |
| `pjt_id`와 `pjt_no`는 섞지 않는다 | instance/group 의미가 다르다 |
| `lead_org_name`, `participant_org_name`, `people_affiliation_org_name`는 다른 역할이다 | 조직 의미 보존 |
| raw payload를 LLM에 직접 넣지 않는다 | canonical evidence와 render profile을 반드시 거친다 |
| retrieval metadata 전체를 답변 프롬프트에 노출하지 않는다 | prompt-safe 사실 필드만 사용한다 |
