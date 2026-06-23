# 03. 에이전트 런타임 (이 브랜치의 심장)

> "에이전트가 어떻게 도구를 부르고, 언제 멈추고, 답을 어떻게 검증하나"를 다룬다. 이 브랜치를 *진짜 에이전트*로 만드는 부분이다. §1이 개념, §2 이후가 정밀 참조.

## 1. 핵심: 플래너가 루프를 돈다

고정 RAG 체인과 가장 다른 점 — **LLM 플래너가 도구를 반복해서 부르며 스스로 끝낼 때를 정한다.**

```
planner_loop:  "도구 부를까 / 이제 답할까 / 되물을까?"  ← LLM이 매 스텝 결정
   ↓ call_tool
tool_executor: 도구 실행 → 관찰(Observation)
   ↓ (response.* 종결이면 발행, 아니면)
planner_loop:  관찰을 보고 다시 결정 ...  (반복)
```

- 플래너가 `action=answer`를 내면 → 근거 정리(answer_curator) → 답변 생성.
- **최대 8스텝**(`_DEFAULT_MAX_STEPS = 8`, `apps/pipeline/agents/planner_agent.py:31`)이 안전망. 종료 결정 자체는 LLM이 한다(시스템 프롬프트 규칙 #3).
- 추가 종료 가드: `duplicate_call`(같은 도구 반복 호출 방지), 라우터 안전 분기 `step_no >= 50`.

> **왜 이렇게?** 예전엔 별도 `AdequacyGate`(LLM 판정자)가 "근거 충분한가"를 따로 판정했는데, 성공한 검색도 "불충분"으로 과판정해 중복검색이 폭주했다. ADR-0023이 그걸 제거하고 **Planner를 유일한 충분성 판정자**로 만들었다.

## 2. 정밀 참조 — PlannerAgent (2-pass)

`PlannerAgent.decide_next`는 **두 번에 나눠** LLM을 부른다:

- **Pass 1 (행동·도구 선택):** `action`, 선택적 `tool`, `reason`, `confidence` 결정. **도구 이름 + 한 줄 설명만** 본다(전체 인자 스키마 안 봄). thinking: `RAG_PLANNER_THINKING_ENABLED`(기본 on) → `disable_thinking=False`, `reasoning_effort="medium"`, `max_tokens=4096`(thinking 시) / `384`.
- **Pass 2 (도구 인자):** `action="call_tool"`이고 선택된 도구가 카탈로그에 있을 때만. **선택된 도구의 스키마만** 본다. thinking 미적용.
- 폴백: Pass 1이 모르는 도구 → `answer` 폴백(Pass 2 생략) · Pass 2 실패 → 도구는 유지하고 `args={}`.
- max_steps 초과 → `PlannerStep(action="answer", reason="max_steps_exceeded(...)", confidence=0.3)`.

## 3. 정밀 참조 — tool_executor 라우팅 (ADR-0023)

`_route_after_tool_executor`가 **결정적으로** 라우팅한다(LLM 판정 없음):

- `plan_state`가 None 또는 `step_no >= 50` → `answer_curator`(안전판).
- 마지막 관찰이 `response.direct_answer`/`response.unsupported` + `status=ok` → `emit_tool_response`(직접답변 종결, ADR-0024).
- 그 외 전부 → `planner_loop`(다시 판단).

## 4. 정밀 참조 — answer_curator는 evidence-only (ADR-0024)

`node_answer_curator`는 **근거만** 만든다(`EvidenceBundle` 빌드) → 항상 `answer_agent`로. `response.*` 발행은 별도 `emit_tool_response` 노드 담당. `PlannerStep.answer_text`는 발행되지 않는다(필드는 남아 있으나 비활성).

근거 선택 우선순위: ① 마지막 성공 `search.detail` → ② 마지막 성공 `search.stats` → ③ 마지막 성공 `search`(비면 직전 비어있지 않은 search) → ④ 빈 근거. (구 이름 `search.exact_lookup`/`search.aggregate`/`search.hybrid`도 호환 인식 — ADR-0022)

## 5. 정밀 참조 — 듀얼 답변 (Solar A / Gemma B, ADR-0021)

`node_answer`가 **같은** `EvidenceBundle`에 대해 두 `AnswerAgent`를 `asyncio.gather`로 동시 실행:

- **메인 = Solar** (`solar_vllm_0`, `model_key="solar"`, 패널 A): `state.answer_draft`. **canonical** — CriticAgent가 검증하고, `repair_answer`·`reference.set`·세션 저장을 구동.
- **비교 = Gemma** (`gemma_triton_0`, `model_key="gemma"`, 패널 B): `state.secondary_answer_draft`. **raw로 노출** — groundedness/citation 게이트·repair 없음, 저장 안 함.
- 비교 답변은 비어있지 않은 근거 첫 패스에서만 생성. `repair_answer` 재패스는 메인(Solar)만 재생성. `reference.set` 하나가 두 패널에 적용.
- `RAG_DUAL_ANSWER_ENABLED=false` → 메인(Solar)만 생성해 두 패널에 동일 표시.

## 6. 정밀 참조 — CriticAgent 라우팅

`critic_agent`(+ `grounding/llm_judge.py`)가 메인(Solar) 답을 검증하고 반환: `publish`/`repair_answer`/`clarify`/`internal_error`.

- `publish` → `save_session`
- `repair_answer` → `answer_agent` (**1회만** — answer_agent 내부 `repair_attempted` 가드로 무한루프 차단)
- `clarify` → `emit_clarification`
- 그 외/없음 → `emit_internal_error`

grounding checker 기본 on(`RAG_GROUNDING_CHECKER_ENABLED=true`). critic thinking: `RAG_CRITIC_THINKING_ENABLED`(기본 on), max token 512(on)/256(off), 스레드 타임아웃 `RAG_CRITIC_THREAD_TIMEOUT_SECONDS=600`.

## 7. 정밀 참조 — 직접 응답 도구

`apps/pipeline/tools/response_tools.py`: `response.direct_answer`(인사·능력설명 등 NTIS 근거 불필요) · `response.unsupported`(NTIS R&D 범위 밖 정직한 거절). 플래너가 "검색하지 않겠다"를 *명시적으로* 정할 수 있게 하는 도구.
