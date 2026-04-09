# NTIS Domain RAG Chatbot

> NTIS 질문을 검색 전략으로 바꾸고, 검색 근거를 정규화해 답변으로 전달하는 **retrieval-first RAG 시스템**

- **브랜치**: `고도화` (변경 금지)
- **검증 방식**: smoke-only (`py_compile` + import smoke + `create_app()`)

---

## 시스템이 하는 일

사용자 질문 → planner가 검색 전략(`SEARCH / LOOKUP / JOIN`) 결정 → 전략대로 검색 실행 → 결과를 canonical evidence로 정규화 → LLM이 근거 기반 답변 생성

핵심 원칙은 **"어떻게 찾을지 먼저 고정하고, 근거를 만든 뒤, 마지막에야 답변을 표현한다"** 는 계층 분리다.

---

## 4개 계층 구조

| 계층 | 역할 | 금지 사항 |
|---|---|---|
| **질의 이해** | intent 추출, ids_map 구성 | runtime 전략 확정 금지 |
| **Retrieval 실행** | SEARCH / LOOKUP / JOIN 수행 | 새 mode / relation / join_key_mode 발명 금지 |
| **근거 조립** | raw 결과 → canonical evidence | raw payload 직접 prompt 노출 금지 |
| **채팅 UX** | 답변 생성, streaming, 대화 흐름 | retrieval contract 변경 금지 |

---

## 패키지 구조

```
apps/
├── api/            # 진입점, 라우팅, streaming transport, workflow 조립
├── conversation/   # follow-up, scope, anchor, view-state, raw payload memory
├── planner/        # stagewise planner (surface_signals→stage1→1.5→stage2→assemble)
├── retrieval/      # SEARCH/LOOKUP/JOIN orchestration, filter compile, batch hydration
├── evidence/       # canonical evidence, derived facts, prompt envelope, packing/compression
├── chat/           # answer generation, LLM runtime, streaming runner
├── platform/       # settings, Solar tokenizer, shared DTOs, storage
└── prompts/        # planner stage prompt templates + domain cards
    └── cards/
```

---

## 요청 1건 흐름

```
POST /query/stream
    │
    ├─ [API] workflow seed 생성
    ├─ [Conversation] scope_resolver → follow-up 분류 (fresh / reference / child_entity / reset / ambiguous)
    ├─ [Planner] surface_signals → stage1 → stage1.5 → stage2 → validation/repair → assemble → strategy
    ├─ [Retrieval] execution constraint compile → SEARCH / LOOKUP / JOIN → hydration
    ├─ [Evidence] canonical evidence 조립 → derived facts → prompt envelope → packing
    ├─ [Chat] answer generation → streaming
    └─ REQ.SUMMARY
```

---

## 핵심 계약 (절대 깨지면 안 됨)

**전략 불변성**
- planner가 결정한 `mode / relation / target_cols / join_key_mode`는 단일·불변
- answer 단계에서 재결정 금지

**검색 모드 경계**
- `SEARCH` — recall 우선, server-side must filter 추가 금지
- `LOOKUP` — precision 우선, server-side gate 유지 필수
- `JOIN` — exact seed 없는 relation join 금지
- BM25-only 우회 금지

**식별자 분리**
- `pjt_id`(개별 과제) ≠ `pjt_no`(묶음 과제) — 절대 혼용 금지

**근거 경계**
- raw payload를 prompt에 직접 넣지 않음 — canonical evidence 경유 필수
- `prtcp_mp` / `prtcp_org` 거대 배열은 facts + preview로만 변환

**Fallback 금지**
- fallback chat, mode 변경 재시도 금지
- 근거가 약하면 보수적으로 답변

---

## 대화 흐름 핵심 규칙

| 주제 | 규칙 |
|---|---|
| Follow-up 해석 우선순위 | active child anchor → subject_index → visible_answer_manifest → prev_context |
| Scope 분류값 | `fresh_search`, `reference_followup`, `child_entity_followup`, `refinement_followup`, `scope_reset`, `ambiguous_followup` |
| 실행 진실원 | `normalized_intent.ids_map` + `QuestionAnalysisV3` |
| Memory 보존 | raw payload는 gzip+base64 압축, `conversation_id + turn_id` 키로 저장 |
| Canonical evidence | prompt 진실원은 `prompt_units`, `context`는 직렬화 산출물 |

---

## 검증 방법

### Smoke 검증 (현재 active gate)

```powershell
# 1. 문법 검증
python -m py_compile apps/api/app_factory.py apps/api/runtime.py `
    apps/planner/query_analysis.py apps/planner/planner_runtime.py `
    apps/conversation/scope_resolver.py apps/conversation/followup_anchor.py

# 2. Import smoke
python -c "import apps.planner.query_analysis, apps.planner.planner_runtime"
python -c "import apps.conversation.scope_resolver, apps.conversation.followup_anchor"

# 3. App factory smoke
python -c "from apps.api.app_factory import create_app; create_app()"

# 4. Workflow graph smoke (langgraph 있는 환경)
python -c "from apps.api.workflow_builder import build_request_workflow; build_request_workflow().compile()"
```

> `scripts/run_baseline_checks.ps1`는 retired 상태이며 `tests/`는 현재 worktree에서 비활성입니다.

---

## 운영 로그 트리아지

문제가 생기면 이 순서로 로그를 확인한다.

```
REQ.START
  └─ PLANNER.SIGNALS → PLANNER.STAGE1 → PLANNER.STAGE15 → PLANNER.STAGE2 → PLANNER.ASSEMBLE
       └─ RAG.RETRIEVAL_QUERY.RESOLUTION → RAG.RESULT → RAG.CONTEXT
            └─ LLM.RESULT → REQ.SUMMARY
```

| 확인 포인트 | 보는 이유 |
|---|---|
| `PLANNER.ASSEMBLE` | strategy drift 여부 |
| `RAG.RETRIEVAL_QUERY.RESOLUTION` | 실행 전략이 계약대로 컴파일됐는지 |
| `RAG.RESULT` / `DISPLAY.SNAPSHOT.*` | retrieval hit 수, canonical evidence 구성 |
| `LLM.RESULT` | groundedness verdict, answer-state consistency |
| `REQ.SUMMARY` | 전체 지연, 토큰, fallback 여부 |

---

## 기준 파일 지도

| 주제 | 파일 |
|---|---|
| Request 해석, follow-up anchor | `apps/conversation/request_facade.py` |
| Scope 분류 | `apps/conversation/scope_resolver.py` |
| Follow-up anchor 해석 | `apps/conversation/followup_anchor.py` |
| Raw payload memory | `apps/conversation/raw_payload_store.py` |
| Planner 진입점 | `apps/planner/planner_runtime.py` |
| Planner 계약 | `apps/planner/planner_contract.py` |
| Planner 기본값 | `apps/planner/planner_defaults.py` |
| Retrieval workflow | `apps/retrieval/retrieval_workflow.py` |
| Filter compile | `apps/retrieval/rag_compile_runtime.py` |
| Canonical evidence | `apps/evidence/canonical_evidence.py` |
| Prompt envelope & packing | `apps/evidence/prompt_evidence_envelope.py`, `apps/evidence/context_packer.py` |
| View/session state | `apps/conversation/view_state.py` |
| App factory | `apps/api/app_factory.py` |
| Settings | `apps/platform/settings.py` |
| Solar tokenizer | `apps/platform/solar_tokenizer_adapter.py` |

---

## 문서 지도

| 문서 | 용도 |
|---|---|
| `apps/docs/00_ONBOARDING.md` | 처음 합류한 사람을 위한 입구 |
| `apps/docs/01_아키텍처와_흐름.md` | 4개 계층과 요청 흐름 상세 |
| `apps/docs/02_실행계약과_전략규칙.md` | SEARCH/LOOKUP/JOIN 경계, follow-up 해석 기준 |
| `apps/docs/03_운영과_환경.md` | 운영 triage, 검증 명령 |
| `apps/docs/04_회귀기준과_점검.md` | 반드시 지킬 계약, 회귀 점검 방법 |
| `apps/docs/05_유지보수와_확장.md` | 유지보수 가이드 |
| `apps/docs/CODEX_CONTEXT.md` | 자동화 에이전트용 bootstrap truth |
| `apps/docs/SESSION_HANDOFF.md` | 세션별 변경 이력 |
| `apps/docs/GOLDEN_TESTS.md` | 핵심 계약 테스트 초안 |
| `apps/docs/EVIDENCE_PROMPT_PACKING.md` | evidence packing 상세 |
| `apps/docs/ADR/` | 아키텍처 결정 기록 (ADR-0005 ~ ADR-0008) |
| `AGENTS.md` | Watcher / Improver / Architect 자동화 루프 정의 |

---

## 알려진 제약사항

- **검증**: 현재 active gate는 smoke-only. pytest 회귀 레인 미존재.
- **Solar tokenizer**: production 환경에서 `transformers` 미설치 시 exact count fallback 발생 가능.
- **Observability**: raw payload memory hit/miss, hydrate 후보 이유, follow-up short-circuit count 로그 미비.
- **테스트 레인**: `GOLDEN_TESTS.md`의 계약 테스트가 실행 가능한 코드로 아직 이전되지 않음.
