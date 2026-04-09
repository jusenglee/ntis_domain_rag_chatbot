# NTIS RAG 챗봇 — 온보딩 가이드

> 처음 합류한 사람이 "이 시스템이 무엇을 하고, 어디서부터 읽고, 문제가 생기면 어디를 봐야 하는지" 10분 안에 잡기 위한 입구 문서입니다.

---

## 이 문서를 읽을 사람

- **신규 개발자**: 코드베이스를 처음 접하는 백엔드 개발자
- **PM / 기획**: 시스템 동작 방식을 파악해야 하는 비개발자

---

## 시스템 한 줄 요약

**NTIS 사용자 질문 → 검색 전략 결정 → 검색 실행 → 근거 정규화 → 답변 생성**

핵심은 "어떻게 찾을지 먼저 고정하고, 근거를 만든 뒤, 마지막에야 답변을 표현한다"는 계층 분리입니다. 답변 품질은 retrieval 전략 정확도에 의존합니다.

---

## 핵심 용어 정리

| 용어 | 의미 |
|---|---|
| `planner contract` | 어떻게 검색할지에 대한 실행 전략 — 한번 결정되면 불변 |
| `SEARCH / LOOKUP / JOIN` | 검색 모드. SEARCH=recall 우선, LOOKUP=정확도 우선, JOIN=두 컬렉션 연결 |
| `canonical evidence` | raw 검색 결과를 LLM에 보여주기 전에 정규화한 근거 |
| `follow-up` | 이전 턴의 대상을 이어받는 후속 질문 |
| `anchor` | follow-up 해석 시 기준이 되는 과제/연구자/기관/성과 식별자 |
| `scope` | 현재 대화의 맥락 상태 (어떤 목록이나 상세를 보고 있는지) |
| `subject_index` | 대화 중 등장한 주체(사람/기관/성과)를 이름으로 재참조하기 위한 인덱스 |
| `drift` | 질문 의미가 검색 실행 중간에 바뀌는 현상 — 방지가 핵심 목표 |
| `groundedness` | 답변이 실제 근거 안에 있는지 판단 |
| `pjt_id / pjt_no` | 과제 식별자. `pjt_id`=개별 과제, `pjt_no`=묶음 과제. 혼용 금지 |

---

## 처음 합류하면 이 순서로 읽습니다

### 개발자 (전체 흐름 파악)
1. 이 문서 (온보딩)
2. `01_아키텍처와_흐름.md` — 4개 계층과 요청 흐름
3. `02_실행계약과_전략규칙.md` — 절대 깨지면 안 되는 계약
4. 코드 읽기 순서: `routes.py` → `request_facade.py` → `retrieval_workflow.py` → `view_state.py` → `answer_generation.py`

### PM / 기획 (시스템 이해)
1. 이 문서 (온보딩) — 용어 정리까지
2. `01_아키텍처와_흐름.md` — 계층 역할과 제한 사항 표 위주
3. `02_실행계약과_전략규칙.md` — 핵심 요약과 실행 약속 섹션만

---

## 요청 1건이 처리되는 순서

```
사용자 질문
  │
  ├─ [Conversation] scope_resolver
  │    follow-up 유형 분류: fresh / reference / child_entity / reset / ambiguous
  │
  ├─ [Planner] stagewise 전략 결정
  │    surface_signals → stage1 → stage1.5 → stage2 → validation → assemble
  │    결과: strategy { mode, relation, ids_map, filters, retrieval_query }
  │
  ├─ [Retrieval] 전략대로 검색 수행
  │    SEARCH / LOOKUP / JOIN → 결과 hydration → reranking
  │
  ├─ [Evidence] 검색 결과 정규화
  │    canonical evidence → derived facts → prompt envelope → packing
  │
  ├─ [Chat] LLM 답변 생성 + streaming
  │
  └─ REQ.SUMMARY 로그
```

---

## 기준 파일 지도

| 확인하고 싶은 것 | 파일 |
|---|---|
| 진입점, 라우팅 | `apps/api/routes.py` |
| 질문 해석, follow-up, anchor | `apps/conversation/request_facade.py` |
| Scope 분류 (fresh/reference/...) | `apps/conversation/scope_resolver.py` |
| Follow-up anchor 이름 매칭 | `apps/conversation/followup_anchor.py` |
| Planner 진입점 | `apps/planner/planner_runtime.py` |
| Planner 기본값 | `apps/planner/planner_defaults.py` |
| Retrieval 흐름 전체 | `apps/retrieval/retrieval_workflow.py` |
| Canonical evidence 조립 | `apps/evidence/canonical_evidence.py` |
| Prompt envelope + packing | `apps/evidence/prompt_evidence_envelope.py` |
| 대화 상태 (scope, manifest) | `apps/conversation/view_state.py` |
| Raw payload 메모리 | `apps/conversation/raw_payload_store.py` |
| 설정값 | `apps/platform/settings.py` |

---

## 문제가 생기면 이 순서로 봅니다

```
1. REQ.START          → raw 질문, request override 입력 확인
2. PLANNER.SIGNALS    → surface_signals 추출 결과
3. PLANNER.STAGE*     → planner drift 여부
4. RAG.RETRIEVAL_QUERY.RESOLUTION → 전략이 계약대로 컴파일됐는지
5. RAG.RESULT         → retrieval hit 수, canonical evidence 구성
6. LLM.RESULT         → groundedness, answer-state consistency
7. REQ.SUMMARY        → 전체 지연, 토큰, fallback 여부
```

---

## 이것만 기억하세요

- planner가 결정한 전략(`mode`, `relation`, `target_cols`)은 **이후 단계에서 절대 변경하지 않습니다.**
- `pjt_id`와 `pjt_no`는 다른 개념입니다. 혼용하면 잘못된 과제를 찾습니다.
- raw payload를 LLM에 직접 넣지 않습니다. 반드시 canonical evidence를 경유합니다.
- 현재 검증은 smoke-only입니다. pytest 회귀 레인이 아직 없습니다.
