# NTIS Domain RAG Chatbot — Cleanup Plan (정리 작업 계획)

> **목적**: 현재 운영 모드(Agentic, `RAG_AGENTIC_MODE=true` default)에서 dead code, 중복 분기, 과세분화 enum, 중복 prompt cards를 단계적으로 정리한다.
>
> **상태**: 계획 수립 단계 — 아직 코드 변경 없음. 각 task는 사용자 승인 후 direct edit으로 진행.
>
> **작성 근거**: `apps/api/runtime.py`, `apps/pipeline/agents/contracts.py`, `apps/pipeline/agents/adequacy_gate.py`, `apps/pipeline/agents/critic_agent.py`, `apps/pipeline/agentic_workflow.py` 직접 확인 (2026-05-28).

---

## 0. Pre-flight (사전 점검 사항)

### 0-1. 불변 조건 (Invariants — 절대 깨면 안 되는 것)

1. **Agentic 흐름 무중단**: `POST /query`와 `POST /query/stream`이 정상 응답을 반환할 것 (200 OK + 답변 텍스트 + citations).
2. **세션 영속화**: `load_session` ↔ `save_session`이 동일 `conversation_id`에 대해 round-trip 가능할 것.
3. **Repair loop 한도**: CriticAgent → AnswerAgent 재시도가 정확히 1회만 허용될 것 (`repair_attempted` 플래그).
4. **Rollback 가능성**: `RAG_AGENTIC_MODE=false`로 7-agent fallback이 *적어도* 기존과 동일 수준으로 동작할 것 (단, Task 5에서 명시적으로 폐기 결정 시 예외).

### 0-2. 검증 게이트 (각 task 종료 시 통과해야 함)

| 게이트 | 방법 | 통과 기준 |
|--------|------|-----------|
| G1. import 정합성 | `python -c "from apps.api.app_factory import create_app; create_app()"` | NoneType / ImportError 없음 |
| G2. 부팅 로그 | `uvicorn apps.api.main:app` 실행 후 `pipeline compiled successfully` 로그 확인 | "Agentic pipeline (cyclic LangGraph) compiled successfully" 노출 |
| G3. Smoke query | `POST /query {"question":"NTIS가 뭐야"}` (direct_answer 경로) | 200 OK + `answer_kind` 필드 존재 |
| G4. Smoke search | `POST /query {"question":"인공지능 관련 과제 알려줘"}` (search 경로) | 200 OK + references 길이 ≥ 1 |
| G5. 세션 round-trip | 같은 `conversation_id`로 2회 호출, 2회차에 "그 사람" 류 follow-up | 두 응답이 같은 세션 컨텍스트 공유 (manifest 또는 focused_detail 갱신 확인) |
| G6. 회귀 fallback | `RAG_AGENTIC_MODE=false` 환경에서 G3 재실행 | (Task 5 이전에는 통과 필요 / Task 5 이후는 명시적 폐기) |

### 0-3. Rollback 전략

- 각 task는 **단일 커밋**으로 묶고, 직전 커밋으로 `git revert` 가능하도록 분리.
- Task 5(legacy 폐기)는 별도 PR로 분리하고, `RAG_AGENTIC_MODE=false` 회귀 옵션을 명시적으로 폐기한다고 commit message에 기록.

### 0-4. 사전 발견된 사실 (계획 수정 사유)

- **`apps/conversation/` 5개 파일이 이미 없음**: 이전 분석에서 dead로 지목했던 `anchor_constraint_compiler.py`, `scope_resolver.py`, `followup_anchor.py`, `followup_resolution.py`, `entity_reference.py`는 **이미 삭제되어 있음** (현재 디렉터리는 `entity_registry.py`, `session_memory.py`, `view_state.py` 3개만). `__pycache__`의 .pyc 파일은 과거 잔여물.
  → **Task 5에서 conversation 모듈 정리 항목은 .pyc 청소만 남음.**
- **`answer_agent`/`critic_agent`는 두 모드 모두 사용**: runtime.py L208-209에서 인스턴스화되며 agentic/legacy 양쪽에서 호출됨. Task 1에서 *legacy 전용* 5개만 조건화해야 함.
- **Prompt card 로더 위치 불명확**: 메인 `apps/`에는 카드를 concat하는 코드가 발견되지 않음. `.claude/worktrees/...planner_context_cards.py`만 발견됨. Task 3 진행 전 **로더 위치 확정 조사**가 선행되어야 함.

---

## 1. Task 1 — runtime.py에서 legacy 7-agent 인스턴스화 조건화

### 1-1. 목적
Agentic 모드에서 호출되지 않는 5개 legacy 에이전트 인스턴스화를 skip하여 부팅 시간·메모리·Qdrant connection footprint를 줄인다.

### 1-2. 변경 범위 (Scope)
- 파일: `apps/api/runtime.py`
- 라인: L196-213 (deps = AgentPipelineDeps(...) 블록)

### 1-3. 현재 코드 (verbatim, L196-213)
```python
deps = AgentPipelineDeps(
    dialogue_agent=DialogueAgent(llm=dialogue_llm),
    # 2026-05-27: qdrant_client 주입으로 subject 도메인 검증 단 활성화 (Phase B).
    entity_resolver=EntityResolverAgent(qdrant_client=rag_resources.qdrant_client),
    search_planner=SearchPlannerAgent(),
    retrieval_agent=RetrievalAgent(
        qdrant_client=rag_resources.qdrant_client,
        embed_e5i=rag_resources.embed_e5i,
        embed_e5=rag_resources.embed_e5,
        task_executor=search_agent,
    ),
    evidence_curator=EvidenceCuratorAgent(),
    answer_agent=AnswerAgent(llm=answer_llm),      # 공유 (agentic도 사용)
    critic_agent=CriticAgent(grounding_checker=grounding_checker),  # 공유
    planner_agent=planner_agent,                   # agentic 전용
    tool_executor=tool_executor,                   # agentic 전용
    adequacy_gate=adequacy_gate,                   # agentic 전용
)
```

### 1-4. 제안 변경 (Proposed diff — 미적용)
```python
# 공유 에이전트는 항상 인스턴스화
_answer_agent = AnswerAgent(llm=answer_llm)
_critic_agent = CriticAgent(grounding_checker=grounding_checker)

# Legacy 5개는 RAG_AGENTIC_MODE=false 일 때만 인스턴스화
if agentic_mode_enabled:
    _dialogue_agent = None
    _entity_resolver = None
    _search_planner = None
    _retrieval_agent = None
    _evidence_curator = None
    logger.info("Legacy 7-agent constructors skipped (agentic mode active)")
else:
    _dialogue_agent = DialogueAgent(llm=dialogue_llm)
    _entity_resolver = EntityResolverAgent(qdrant_client=rag_resources.qdrant_client)
    _search_planner = SearchPlannerAgent()
    _retrieval_agent = RetrievalAgent(
        qdrant_client=rag_resources.qdrant_client,
        embed_e5i=rag_resources.embed_e5i,
        embed_e5=rag_resources.embed_e5,
        task_executor=search_agent,
    )
    _evidence_curator = EvidenceCuratorAgent()

deps = AgentPipelineDeps(
    dialogue_agent=_dialogue_agent,
    entity_resolver=_entity_resolver,
    search_planner=_search_planner,
    retrieval_agent=_retrieval_agent,
    evidence_curator=_evidence_curator,
    answer_agent=_answer_agent,
    critic_agent=_critic_agent,
    planner_agent=planner_agent,
    tool_executor=tool_executor,
    adequacy_gate=adequacy_gate,
)
```

### 1-5. 선행 조건 (Prerequisite)
`AgentPipelineDeps`의 5개 legacy 필드가 `Optional[…]`을 허용해야 함. 만약 현재 `dataclass`/`Pydantic`에서 non-Optional이면 먼저 타입을 `Optional[X] = None`으로 완화. → **별도 micro-task로 처리** (1-5a).

### 1-6. 검증
- G1, G2 통과 필수
- agentic 모드 부팅 시 위 `logger.info` 출력 확인
- `RAG_AGENTIC_MODE=false`로 부팅 시 5개 legacy agent 정상 생성 확인 (G6)

### 1-7. 위험도 / 영향
- **위험**: 낮음 (조건 분기만 추가, 호출되지 않는 객체를 None으로 대체)
- **이득**: 부팅 시 Solar LLM 추가 클라이언트 1개 + Qdrant subject 검증 워밍업 1개 + dataclass 5개 인스턴스 제거. 부팅 시간 약 200~800ms 단축 예상 (실측 필요).
- **롤백**: 단일 커밋 revert로 즉시 복구.

### 1-8. 추정 LOC delta: +18 / -16 (실질 +2줄)

---

## 2. Task 2 — DialogueKind enum에서 agentic 미사용 5개 분리

### 2-1. 목적
DialogueAgent가 매 요청마다 분류하는 10개 kind 중 5개(`ask_meta`, `ask_children`, `ask_similar`, `refine_previous`, `compare`, `stats`)는 agentic 흐름에서 분기 조건으로 사용되지 않는다. 분류 부담을 줄이고 LLM 응답 일관성을 높인다.

### 2-2. 변경 범위
- 파일 1: `apps/pipeline/agents/contracts.py` (L41-52)
- 파일 2: `apps/pipeline/agents/dialogue_agent.py` (L228-307, classification prompt)
- 파일 3: `apps/pipeline/agent_workflow.py` (legacy 그래프; 5개 kind를 emit_* 노드로 분기 — Task 5에서 함께 제거 예정이라면 변경 불필요)

### 2-3. 두 가지 옵션 비교

| 옵션 | 설명 | 장점 | 단점 |
|------|------|------|------|
| **A. Legacy enum 유지 + Dialogue 프롬프트만 축소** | contracts.py는 그대로, Dialogue prompt에서 5개 분류 안내만 제거하고 4개만 출력하도록 변경 | 7-agent 그래프 호환 유지, 롤백 안전 | enum 정의는 여전히 존재 (cosmetic dead code) |
| **B. enum 자체를 `AgenticDialogueKind`로 분리** | `DialogueKind` (10개, legacy) + `AgenticDialogueKind = Literal["ask_search","ask_detail","direct_answer","clarification"]` 신설 | 타입 안전성 ↑, dead enum 명시적 격리 | 두 enum 동시 유지 비용, downstream type 변경 cascading |

→ **권장: 옵션 A** (Task 5에서 7-agent 그래프 전체 폐기 시 자연 해소). 단, Task 5를 영구 폐기 결정한 직후라면 옵션 B로 깔끔히 마무리.

### 2-4. 옵션 A 변경 (Proposed)

**dialogue_agent.py** classification prompt에서:
- L241의 JSON schema 라인에서 5개 kind 제거: `"kind": "ask_search"|"ask_detail"|"direct_answer"|"clarification"`
- L253-271의 kind 설명 블록에서 5개 항목 삭제

**contracts.py** L41-52는 그대로 유지하되, 주석 추가:
```python
DialogueKind = Literal[
    "ask_search",         # ✓ agentic
    "ask_detail",         # ✓ agentic
    "ask_meta",           # ⚠ legacy 7-agent only (DEPRECATED in agentic mode)
    "ask_children",       # ⚠ legacy 7-agent only
    "ask_similar",        # ⚠ legacy 7-agent only
    "refine_previous",    # ⚠ legacy 7-agent only
    "compare",            # ⚠ legacy 7-agent only
    "stats",              # ⚠ legacy 7-agent only
    "direct_answer",      # ✓ agentic
    "clarification",      # ✓ agentic
]
```

### 2-5. 검증
- G1, G2, G3, G4 필수
- **추가 회귀 테스트**: "올해 통계 보여줘" (예전엔 `stats`로 분류) → agentic 모드에서 `ask_search`로 분류되어 PlannerAgent가 `aggregate` 도구를 호출하는지 수동 확인
- "이 과제랑 비슷한 거" (예전 `ask_similar`) → `ask_search`로 분류되어도 검색 의도가 유지되는지 확인

### 2-6. 위험도 / 영향
- **위험**: 중간 (LLM 분류 분포 변화 → downstream PlannerAgent의 행동 변화 가능)
- **이득**: Dialogue 프롬프트 토큰 약 30~40% 감소 → 매 요청 100~200 토큰 절감
- **롤백**: 단일 커밋 revert

### 2-7. 추정 LOC delta: -25 / +5

---

## 3. Task 3 — Prompt cards 9 → 3 통합

### 3-1. 목적
PlannerAgent가 매 루프 step마다 시스템 프롬프트에 카드를 주입한다면, 카드 중복은 직접적인 토큰·지연 비용이다. 도메인 진실 정확도를 유지하면서 카드를 압축한다.

### 3-2. 선행 조사 (BLOCKER — Task 3 시작 전 필수)
`apps/` 메인 트리에서 prompt cards를 LLM에 주입하는 로더가 발견되지 않았다. `.claude/worktrees/.../planner_context_cards.py`만 발견됨. 다음을 먼저 확정해야 함:

1. **카드가 실제로 어디서 LLM 프롬프트에 들어가는가?**
   - 후보 A: `apps/pipeline/agents/planner_agent.py` 내부에서 직접 로드
   - 후보 B: `apps/api/runtime.py`에서 PlannerAgent 생성 시 주입
   - 후보 C: 빌드 시점에 정적으로 임베딩 (런타임 로딩 없음)
2. **9장 모두 사용 중인가, 아니면 일부만?** — 카드 로더가 발견되면 어떤 카드를 select하는지 확인.

### 3-3. 변경 범위 (조사 후 확정 예정)
- 파일 1: 카드 로더 (위치 TBD)
- 파일 2-10: `apps/prompts/cards/*.md` 9장

### 3-4. 통합 안 (잠정)

| 신규 카드 | 흡수 대상 | 책임 |
|-----------|-----------|------|
| `ntis_domain_core.md` | `ntis_domain_card_full.md` + `collections_card.md` + `domain_card_integration_notes.md` | 컬렉션 구조 + 전체 도메인 개요 + 통합 메모 |
| `ntis_id_filter_semantics.md` | `id_semantics_card.md` + `filter_semantics_card.md` + `semantic_disambiguation_card.md` | 식별자 + 필터 + 동음이의어 규칙 |
| `ntis_relations_quality.md` | `relationship_semantics_card.md` + `perf_tag_card.md` + `field_reliability_card.md` | 엔티티 관계 + 성과 태그 + 필드 신뢰도 |

### 3-5. 검증
- G3, G4 필수
- **추가 검증**: 답변 품질 회귀 비교 — 통합 전후 동일 질문 10건에 대해 references / answer_text 비교 (수동 또는 평가 셋 사용)
- 토큰 사용량 측정: PlannerAgent 1 step의 system prompt 토큰 수 (before/after)

### 3-6. 위험도 / 영향
- **위험**: 높음 (LLM 행동 직접 변경, 도메인 정확도 영향)
- **이득**: 토큰 절감 30~50% (카드 중복 부분 제거), 매 step LLM 비용 직접 감소
- **롤백**: 카드 파일을 단순 revert. 로더 변경분도 함께 revert.

### 3-7. 단계화 권장
1. 먼저 로더 위치 확정 (Task 3-pre)
2. 9장 중복 영역을 표로 정리 (manual diff)
3. 신규 3장 초안 작성 → 도메인 검토
4. 로더 변경 + 신규 카드 배치
5. 회귀 평가

### 3-8. 추정 LOC delta: 카드 9장(~600줄?) → 3장(~300줄?), 실측 후 확정

---

## 4. Task 4 — AdequacyGate ↔ CriticAgent 책임 재정의 (설계 결정 필요)

### 4-1. 문제
- **AdequacyGate** (`apps/pipeline/agents/adequacy_gate.py` L95): LLM judge로 *수집된 observation이 답할 수 있는지* 판정 → `{adequate, insufficient, error}`
- **CriticAgent** (`apps/pipeline/agents/critic_agent.py` L126-293): LLM judge로 *생성된 답변이 근거에 부합하는지* 판정 → `{publish, repair_answer, clarify, internal_error}`
- 두 judge 모두 LLM을 호출하고 go/no-go 결정을 내림 → AdequacyGate가 "충분"이라고 통과시킨 evidence를 CriticAgent가 다시 "not_grounded"라며 repair 요청할 수 있음. 책임 경계가 모호함.

### 4-2. 세 가지 옵션

| 옵션 | 설계 | 장점 | 단점 |
|------|------|------|------|
| **A. 명시적 분리 (현 상태 유지 + 문서화)** | AdequacyGate=retrieval 차원, CriticAgent=generation 차원. 책임을 ADR로 명시. | 변경 0, 양쪽 모두 유지 | LLM 호출 2번 비용 지속 |
| **B. AdequacyGate를 결정론으로 다운그레이드** | LLM judge 제거. response.* 자동 adequate / error 자동 insufficient / 그 외 deterministic heuristic (예: tool 호출 횟수, observation count 임계치) | LLM 비용 1회 절감, latency ↓ | adequacy 판정 정확도 잠재 하락 |
| **C. AdequacyGate를 CriticAgent로 흡수** | Critic이 evidence bundle 단계에서도 호출되어 unified judge. 두 단계 통합. | 단일 책임 원칙, 일관성 | Critic의 LLM 부담 ↑ 가능, 큰 리팩토링 |

### 4-3. 권장 절차
1. **데이터 수집**: 현재 운영 로그에서 (a) AdequacyGate=adequate ∧ Critic=repair_answer/clarify 케이스 빈도, (b) AdequacyGate=insufficient → loop 횟수 분포 측정
2. (a) 비율이 5% 미만이면 옵션 A 유지 (충분히 직교적), 10%+ 이면 옵션 B 또는 C 적극 검토
3. 옵션 B는 작은 PR로 실험 가능 (LLM judge 분기만 끄기) → A/B 비교

### 4-4. 변경 범위 (옵션 B 기준 잠정)
- 파일: `apps/pipeline/agents/adequacy_gate.py` L95-145
- 파일: `apps/pipeline/agentic_workflow.py` L520-532 (라우터)

### 4-5. 검증 / 위험
- **위험**: 높음 (운영 동작 변경)
- **선행**: ADR 작성 (옵션 결정 근거)
- 별도 PR 권장

### 4-6. 추정 LOC delta: 옵션에 따라 -10 ~ -150

---

## 5. Task 5 — 7-agent 그래프 + apps/conversation .pyc 잔여 청소

### 5-1. 목적
Agentic 모드를 항구 채택하기로 결정한 경우, `RAG_AGENTIC_MODE=false` fallback을 제거하고 약 1,000줄의 dead code를 main에서 분리한다.

### 5-2. 선행 결정 (사용자 의사결정 필수)
- ☐ **5a. 영구 폐기**: legacy 7-agent를 영구 제거. `RAG_AGENTIC_MODE` 환경변수 자체 제거.
- ☐ **5b. legacy 브랜치 분리**: main에서 제거하되 `legacy/static-7agent` 브랜치에 보존.
- ☐ **5c. 현 상태 유지**: Task 1만 적용하고 legacy는 그대로 둠 (보수적).

### 5-3. 변경 범위 (5a 기준)
| 항목 | 파일 / 경로 | 동작 |
|------|-------------|------|
| 7-agent 그래프 | `apps/pipeline/agent_workflow.py` | 삭제 |
| Legacy agents | `apps/pipeline/agents/dialogue_agent.py`, `entity_resolver.py`, `search_planner.py`, `retrieval_agent.py`, `evidence_curator.py` | 삭제 |
| runtime.py | L158-159, L196-213, L230-236 | agentic-only로 단순화 |
| .pyc 잔여물 | `apps/conversation/__pycache__/anchor_constraint_compiler.cpython-31*.pyc` 등 | git clean / .gitignore 점검 |
| AgentPipelineDeps | `apps/pipeline/...` | legacy 필드 5개 제거 |
| DialogueKind | `contracts.py` | Task 2 옵션 B와 함께 4개로 축소 |

### 5-4. 검증
- G1~G5 통과
- **G6 회귀 게이트는 명시적으로 폐기** — commit message에 "RAG_AGENTIC_MODE fallback 제거" 명시
- 모든 import가 깨끗하게 정리되는지 `ruff check` / `pyright` 통과

### 5-5. 위험도 / 영향
- **위험**: 중간 (영구 변경)
- **이득**: 약 1,000~1,500 LOC 제거, 인지 부하 감소, CI 시간 단축
- **롤백**: git revert로 가능하나 환경변수 자체가 사라지므로 운영 측 영향 검토 필요

### 5-6. 추정 LOC delta: -1,200

---

## 6. Execution Order (실행 순서 권장)

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 1 — Safe immediate (안전 즉시 실행)                  │
│ ─────────────────────────────────────────────────           │
│ Task 1: runtime.py legacy guard           [위험: 낮음]     │
│ Task 5 .pyc 청소 항목만                   [위험: 0]        │
└─────────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 2 — Short-term (Task 5 결정 후)                      │
│ ─────────────────────────────────────────────────           │
│ 사용자 결정: Task 5a/5b/5c 선택                            │
│ 5c → Phase 3로 (5a/5b면 Phase 2-a 진행)                    │
│                                                              │
│ Phase 2-a (5a 또는 5b 선택 시):                            │
│   - 7-agent 그래프 + legacy agents 제거                    │
│   - DialogueKind 옵션 B 적용                                │
└─────────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 3 — Quality / cost optimization                       │
│ ─────────────────────────────────────────────────           │
│ Task 3-pre: 카드 로더 위치 확정 (조사)                     │
│ Task 3: prompt cards 9 → 3                [위험: 높음]     │
│   ↳ 평가 셋 회귀 검증 필수                                  │
│ Task 2 옵션 A (5c 선택 시 fallback)       [위험: 중간]     │
└─────────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────────┐
│ Phase 4 — Design decision (별도 PR)                         │
│ ─────────────────────────────────────────────────           │
│ Task 4: AdequacyGate ↔ CriticAgent 책임 재정의             │
│   ↳ 운영 로그 통계 수집 선행                                │
│   ↳ ADR 작성 후 옵션 선택                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## 7. Validation Matrix (작업별 게이트 매핑)

| Task | G1 | G2 | G3 | G4 | G5 | G6 | 추가 검증 |
|------|----|----|----|----|----|----|-----------|
| 1 | ✓ | ✓ | ✓ | ✓ | – | ✓ | 부팅 시간 측정 |
| 2 (옵션 A) | ✓ | ✓ | ✓ | ✓ | – | ✓ | 분류 분포 비교 (5건 수동) |
| 3 | ✓ | ✓ | ✓ | ✓ | ✓ | – | 답변 품질 회귀 (10건) + 토큰 측정 |
| 4 | ✓ | ✓ | ✓ | ✓ | – | – | 운영 로그 분석 + ADR |
| 5 | ✓ | ✓ | ✓ | ✓ | ✓ | ✗(폐기) | ruff / pyright 통과 |

---

## 8. Open Questions (사용자 확인 필요)

1. **Q1**: `RAG_AGENTIC_MODE=false` 회귀 옵션을 영구 폐기할 의향이 있는가? (Task 5a vs 5b vs 5c)
2. **Q2**: Prompt cards 로더가 어디 있는지 아는가? (Task 3-pre 단축 가능)
3. **Q3**: AdequacyGate와 CriticAgent의 운영 통계(adequate→repair_answer 비율 등)를 측정할 수 있는 로그/대시보드가 있는가?
4. **Q4**: 답변 품질 회귀 평가 셋(질문-정답 페어)이 별도로 존재하는가? Task 3 검증에 필요.
5. **Q5**: 도메인 검토(prompt cards 통합)를 누가 할 것인가?

---

## 9. Summary Table

| Task | 우선순위 | 위험도 | 예상 작업량 | LOC delta | 즉시 실행 가능? |
|------|----------|--------|-------------|-----------|-----------------|
| 1. runtime.py guard | High | 낮음 | 1 PR (~30분) | +2 | ✅ |
| 2. DialogueKind 정리 | Mid | 중간 | 1 PR (~1시간) | -20 | ✅ (옵션 A) |
| 3. Prompt cards 통합 | Mid | 높음 | 2-3 PR (~1일+) | ~-300 | ⚠ 로더 조사 선행 |
| 4. AdequacyGate/Critic | Low | 높음 | 1+ PR (~수일) | -10~-150 | ❌ ADR 선행 |
| 5. Legacy 폐기 | Mid | 중간 | 1 PR (~반나절) | -1,200 | ❌ 사용자 결정 선행 |

---

*이 계획은 사용자 승인을 기다리는 작성 단계 문서입니다. 각 task를 시작할 때 본 문서의 해당 섹션을 참조하여 단일 PR 단위로 진행합니다.*
