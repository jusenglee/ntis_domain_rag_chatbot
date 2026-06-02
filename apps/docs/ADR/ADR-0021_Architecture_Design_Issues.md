# ADR-0021: Architecture Design Issues — Unified Agentic Pipeline

Status: active, 2026-06-02

이 문서는 ADR-0020 통합 Agentic 파이프라인 구현 이후 발견된 설계 오류와
기술 부채를 기록한다. 각 이슈는 심각도(Critical / Important / Minor)로 분류하고
근본 원인과 권장 조치를 명시한다.

---

## Issue 1 — DialogueAgent와 Planner의 이중 의도 판단

**심각도: Critical**

### 현상

```
DialogueAgent → kind=ask_search   ("이건 검색 쿼리다")
Planner       → response.unsupported ("이건 NTIS 범위 밖이다")
```

같은 질문에 대해 두 에이전트가 독립적으로 상반된 판단을 내린다.

### 근본 원인

`_summarize_session()`이 `state.dialogue_intent`를 Planner에게 전달하지 않는다.
Planner는 DialogueAgent의 분류 결과를 모르는 채로 처음부터 의도 판단을 다시 한다.

```python
# agentic_workflow.py — _summarize_session()
# dialogue_intent 누락
summary = {
    "has_subject": ...,
    "has_manifest": ...,
    "entity_resolution": ...,
    # "dialogue_kind": 없음  ← 문제
}
```

### 영향

- `ask_search`로 분류된 쿼리가 검색 한 번도 없이 `response.unsupported`로 종료
- 운영 로그 예시: "최근 LLM 관련 연구동향은?" → Planner가 즉시 unsupported 선택

### 권장 조치

`_summarize_session()`에 `dialogue_kind` 추가:

```python
if state.dialogue_intent is not None:
    summary["dialogue_kind"] = state.dialogue_intent.kind
```

Planner 프롬프트에 kind 기반 하드 규칙 추가:

```
session.dialogue_kind ∈ {ask_search, ask_detail, stats, compare, refine_previous,
                         ask_similar, ask_meta, ask_children} 이면:
→ search.* 도구를 최소 1회 호출하기 전에 response.unsupported 금지.
```

---

## Issue 2 — Planner max_tokens 부족으로 인한 빈 응답

**심각도: Critical (운영 장애 발생)**

### 현상

```
Planner Pass1 thinking=on, max_tokens=512
→ content="" (빈 응답)
→ parse_failure → fallback=answer
→ no_result 메시지 발행 (검색 0회)
```

### 근본 원인

Solar thinking 모드에서 내부 추론 토큰이 max_tokens 예산을 공유한다.
추론에 ~510 토큰을 소비하면 실제 JSON 출력 공간이 없어진다.

### 조치 완료 (2026-06-02)

```python
# agents/planner_agent.py
max_tokens=4096 if self._thinking_enabled else 384,  # 512 → 4096
```

---

## Issue 3 — answer_curator의 Layer 위반

**심각도: Important**

### 현상

`node_answer_curator`(Layer 2 — Evidence Authority)가 스트리밍 발행(Layer 3 행위)을
직접 수행한다.

```python
# agentic_workflow.py — node_answer_curator
if direct_text:
    await state.stream_emitter.publish(StreamEvent(...))   # ← Layer 3 행위
    return {
        "final_answer_text": direct_text,   # ← CriticAgent 없이 발행
        "answer_artifact": artifact,
    }
```

### 근본 원인

Planner가 `response.*` 도구를 호출하는 경로(ask_search → scope 판단 → unsupported)의
응답 발행 로직이 answer_curator에 남아있다. ADR-0020에서 제거하기로 했으나 미완료.

### 영향

- Layer 1이 분리해준 `direct_answer` 경로(emit_direct_answer 노드)와
  Layer 2 answer_curator의 `direct_response` 경로가 공존
- CriticAgent 없이 발행되는 응답이 두 가지 경로에서 나올 수 있음

### 권장 조치

answer_curator에서 스트리밍 발행 코드 제거.
`response.*` 도구 결과 처리를 위한 `emit_planner_response` 노드를 신설하고,
`_route_after_answer_curator`에서 이 노드로 분기한다.

```
현재: answer_curator → (direct_text 있으면) → save_session
변경: answer_curator → (direct_text 있으면) → emit_planner_response → save_session
     emit_planner_response: 스트리밍 발행 + artifact 생성 (Layer 3)
```

---

## Issue 4 — CriticAgent 우회 경로 2개

**심각도: Important**

### 현상

다음 두 경로에서 CriticAgent(검증자)를 완전히 건너뛴다.

**경로 A — response.* 도구 결과:**
```
Planner → response.direct_answer/unsupported → ToolExecutor
→ AdequacyGate(adequate, deterministic)
→ answer_curator(direct_text 감지)
→ final_answer_text 직접 설정 → save_session
```

**경로 B — PlannerStep.answer_text:**
```
Planner → action=answer + answer_text 직접 채움
→ answer_curator(_select_direct_response_text 감지)
→ final_answer_text 직접 설정 → save_session
```

### 근본 원인

`response.*` 도구와 `PlannerStep.answer_text`는 의도적으로 AnswerAgent/Critic을
우회하도록 설계됐다(ADR-0019 "능동 직접 응답"). 그러나 ADR-0019 Required Future Work에
"이 경로가 Critic을 우회해도 되는가"가 미결로 남아있다.

### 영향

- `response.unsupported`의 표준 문구는 Critic 검증 없이 발행됨
- PlannerStep.answer_text는 어떤 내용이든 그대로 사용자에게 노출될 수 있음

### 권장 조치

**단기:** PlannerStep.answer_text 경로 제거 (가장 위험한 경로)

```python
# _select_direct_response_text에서 answer_text 경로 제거
# Planner가 action=answer를 선택하면 항상 evidence 기반 흐름으로
```

**중기:** `response.*` 도구 결과도 경량 CriticAgent 검증 통과 후 발행

---

## Issue 5 — AdequacyGate와 answer_curator의 중복 판단 ✅ 해결 (ADR-0023)

**심각도: Important — 2026-06-02 AdequacyGate 제거로 해소**

### 현상 (해결 전)

`response.*` 도구 결과에 대해 두 곳에서 동일한 판단을 했다.

```
AdequacyGate:   last obs = response.* → verdict=adequate (deterministic)
answer_curator: direct_text 있음     → final_answer_text 직접 설정
```

또한 AdequacyGate의 LLM judge가 검색 성공 후에도 "insufficient" 과판정 →
Planner 동일 query 재시도 → duplicate_call 강제종료 churn을 유발했다.

### 해결 (ADR-0023)

AdequacyGate(LLM judge) 전체 제거. 충분성 판단을 Planner 단일 권한으로 일원화.
`_route_after_tool_executor`를 결정적 라우터로 교체:
`response.* + ok → answer_curator`, 그 외 → `planner_loop`.
`response.*` final_text 발행은 원래부터 answer_curator 소유였으므로 영향 없음.

---

## Issue 6 — 두 종류의 clarification emit 노드 혼용

**심각도: Important**

### 현상

```
emit_clarification    — DialogueAgent, EntityResolver, CriticAgent 결정 시 사용
emit_agentic_clarify  — Planner action=clarify 결정 시 사용
```

두 노드가 각각 다른 방식으로 clarification을 발행한다.

### 근본 원인

`emit_agentic_clarify`는 `plan_state.last_decision().clarification_question`에서
텍스트를 가져오고, `emit_clarification`은 `state.guard_decision.clarification_question`
또는 `state.dialogue_intent.clarification_question`에서 가져온다.

### 영향

- Planner가 되묻기를 선택했을 때와 나머지 에이전트가 되묻기를 선택했을 때
  사용자에게 다른 포맷으로 응답이 나올 수 있음
- 동일한 세션에서 두 경로가 번갈아 실행되면 일관성 결여

### 권장 조치

두 노드를 단일 `emit_clarification` 노드로 통합.
clarification 텍스트 소스를 단일 state 필드로 정규화:

```python
# 모든 clarification 텍스트를 state.pending_clarification_question 로 통합
# emit_clarification 노드가 이 필드만 참조
```

---

## Issue 7 — EntityResolver 무음 실패

**심각도: Minor**

### 현상

```python
# agent_workflow.py — _make_node_entity_resolver
async def node_entity_resolver(state):
    if state.dialogue_intent is None:
        return {}   # 로그 없이 빈 dict 반환
```

`entity_resolution`이 None인 채로 라우터에 도달 →
`_route_after_entity_resolver`가 `emit_internal_error`로 분기하지만
왜 internal_error가 발생했는지 로그가 없어 추적 불가.

### 권장 조치

```python
if state.dialogue_intent is None:
    logger.error(
        f"[entity_resolver] dialogue_intent_missing "
        f"req={short_id(state.request_id)} — 그래프 배선 오류"
    )
    return {}
```

---

## Issue 8 — evidence 선택 우선순위의 암묵적 커플링

**심각도: Minor**

### 현상

`_select_evidences_for_answer`의 우선순위:
```
exact_lookup > aggregate > hybrid
```

Planner가 search.aggregate 호출 후 search.exact_lookup을 호출하면
aggregate 결과는 silently 폐기된다.

### 근본 원인

우선순위가 Planner의 도구 호출 순서와 독립적으로 결정된다.
Planner가 "최신 도구 결과 우선"을 기대하지만, answer_curator는
도구 종류 기반 우선순위를 적용한다.

### 영향

- Planner가 집계 후 상세 조회를 하는 복합 전략에서
  집계 결과가 버려질 수 있음

### 권장 조치 (장기)

다중 observation을 통합하는 실질적인 evidence selector 구현.
현재는 단일 최우선 도구 결과만 사용하는 단순 구조.

---

## 이슈 요약표

| # | 이슈 | 심각도 | 조치 상태 |
|---|------|--------|-----------|
| 1 | DialogueAgent-Planner 이중 의도 판단 | **Critical** | ✅ 완료 (dialogue_kind 전달 + 하드 규칙) |
| 2 | Planner max_tokens 부족 (빈 응답) | **Critical** | ✅ 완료 (512→4096) |
| 2b | search.hybrid target="research" 무한 루프 | **Critical** | ✅ 완료 (ADR-0022 SearchRouter) |
| 2c | AnswerAgent 인용 범위 위반 (snapshot_rank 비연속) | **Critical** | ✅ 완료 (_renumber_evidences) |
| 2d | 정적/Agentic 이중 파이프라인 | Important | ✅ 완료 (ADR-0020 Phase 5, 단일 통합) |
| 3 | answer_curator Layer 위반 | Important | 미완료 |
| 4 | CriticAgent 우회 경로 2개 | Important | 미완료 |
| 5 | AdequacyGate-answer_curator 중복 판단 | Important | ✅ 완료 (ADR-0023 AdequacyGate 제거) |
| 6 | clarification emit 노드 2개 혼용 | Important | 미완료 |
| 7 | EntityResolver 무음 실패 | Minor | 미완료 |
| 8 | evidence 선택 우선순위 암묵적 커플링 | Minor | 미완료 (장기) |

---

## 조치 순서 권장

### 즉시 (Critical)

1. **Issue 1** — `_summarize_session`에 `dialogue_kind` 추가 + Planner 프롬프트 하드 규칙

### 단기 (Important, 구현 영향 작음)

2. **Issue 7** — EntityResolver 무음 실패 로그 추가 (1줄)
3. **Issue 4** — PlannerStep.answer_text 경로 제거

### 중기 (Important, 구조 변경 필요)

4. **Issue 3 + 5** — answer_curator 역할 축소 + emit_planner_response 신설
5. **Issue 6** — clarification emit 노드 통합

### 장기 (Minor, 안정화 후)

6. **Issue 8** — 다중 observation evidence selector
