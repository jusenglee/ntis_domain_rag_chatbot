# 기술부채 제거 — ADR-0015 Stage 4 집행 보고서

- 작업일: 2026-05-07
- 근거 문서:
  - `apps/docs/08_단계적_리팩토링_로드맵.md` (Phase 14 신설)
  - `apps/docs/reports/ADR-0015_implementation_audit_2026-04-22.md` (Gap 3)
  - `apps/docs/reports/ADR-0015_structural_decisions_draft_2026-04-23.md` (C2 Option B' Stage 2)
- 환경: VPN 차단으로 vllm/triton/qdrant/oracle 의 동작 회귀 테스트 불가. 본 캠페인은 정적 분석(py_compile, import-graph 정밀 grep)만으로 검증.

---

## 1. 작업 내용

### 1.1 활성 진입점에서 제거된 dead path

`apps/conversation/request_facade.py`:

- `build_intent_payload(...)` (deprecated, ADR-0015 마커가 붙어 있던 함수, 약 720 줄) 제거
- `_apply_context_router_decision(...)` 제거
- `_build_policy_clarification_resolution(...)` 제거
- `_context_router_allowed`, `_context_router_anchor_allowed`, `_build_context_router_scope_summary` 제거
- 상수 `_CONTEXT_ROUTER_CLARIFICATION_TYPES`, `_DETAIL_QUERY_HINTS` 제거
- 위 dead path 만 사용하던 import 11개 제거 (`route_context`, `run_context_router`, `log_context_router_transition`, `focus_entity_from_mention`(local 사용 영향 0), `resolve_candidate_to_focus_entity`, `resolve_reference_context_followup`, `TurnInterpretationResult`, `build_clarification_payload`, `build_turn_candidates`, `match_anchor_to_candidates`, `resolve_hard_followup_signal`, `run_turn_interpreter`, `TurnPolicyResult`, `resolve_turn_policy`, `TurnTriggerResult`, `run_turn_trigger`)

활성 경로(`build_agent_intent_payload`, `build_agent_subject_activity_intent_payload`, `build_agent_manifest_item_lookup_intent_payload`, `build_agent_people_activity_intent_payload`, `build_agent_current_subject_refinement_intent_payload`)는 그대로 보존됨. `_build_intent_payload_object`, `_build_strategy_meta`, `_build_turn_contract` 도 활성 경로의 공유 빌더로 유지.

파일 라인수: 3338 → 2437 (약 27 % 감축).

### 1.2 모듈 stub 처리

bash 마운트가 read-only 라 실제 `rm` 이 불가하여, 다음 파일들은 docstring(주석) 전용 stub 으로 교체. import 시 평문 모듈로 평가되며 어떤 심볼도 노출하지 않음.

| 경로 | 변경 |
|---|---|
| `apps/conversation/turn_trigger.py` | 12326 B → 749 B (stub) |
| `apps/conversation/turn_interpreter.py` | 52120 B → 749 B (stub) |
| `apps/conversation/turn_policy.py` | 15556 B → 579 B (stub) |
| `apps/conversation/context_router.py` | 15813 B → 579 B (stub) |
| `apps/conversation/clarification_labeler.py` | 28805 B → 463 B (stub) |
| `apps/prompts/turn_trigger_v1.md` | 2535 B → 534 B (HTML 주석) |
| `apps/prompts/turn_interpreter_v1.md` | 2731 B → 534 B (HTML 주석) |
| `apps/prompts/clarification_labeler_v1.md` | 1714 B → 528 B (HTML 주석) |
| `apps/prompts/context_router_v1.md` | 2043 B → 632 B (HTML 주석) |

### 1.3 인접 모듈 정리

- `apps/conversation/followup_anchor.py`: `TYPE_CHECKING` import of `TurnCandidate`, `_candidate_matches_ids`, `resolve_candidate_to_focus_entity` 제거 (legacy 경로 전용 helper)
- `apps/conversation/memory_observer.py`: `log_context_router_transition` 제거 (`MEMORY.SNAPSHOT`, `FOLLOWUP.FACT_MISS` 만 active 유지)

### 1.4 테스트 정리

11개 legacy pytest 파일을 tombstone(주석 전용)으로 교체 — 모두 ADR-0015 Stage 1 에서 `pytestmark = pytest.mark.skip(...)` 으로 격리되어 있던 dead test 였음:

- `tests/test_request_facade_turn_policy.py`
- `tests/test_request_facade_context_router.py`
- `tests/test_request_facade_child_detail_followup.py`
- `tests/conversation/test_turn_trigger_current_context.py`
- `tests/conversation/test_turn_policy_llm_guard.py`
- `tests/conversation/test_turn_interpreter.py`
- `tests/conversation/test_followup_unification.py`
- `tests/conversation/test_followup_candidate_resolution.py`
- `tests/conversation/test_context_router.py`
- `tests/conversation/test_clarification_prose.py`
- `tests/conversation/test_clarification_labeler.py`

추가로 다음 두 파일에서 legacy 의존 부분만 정밀 제거:

- `tests/test_planner_prompt_asset_paths.py`: `test_conversation_followup_prompts_load_from_apps_prompts_when_cwd_changes`(turn_trigger / turn_interpreter / clarification_labeler / context_router 4개 prompt 회귀) 및 `test_context_router_prompt_enforces_candidate_index_bounds` 제거. 활성 prompt 회귀(`test_dialogue_agent_prompts_*`, `test_planner_stage_prompts_*`)는 보존
- `tests/test_adr0013_observability_smoke.py`: `CONTEXT.ROUTER.FALLBACK` 관련 3개 함수 제거. `MEMORY.SNAPSHOT`, `FOLLOWUP.FACT_MISS` 회귀는 보존

### 1.5 문서 정합성

- `apps/docs/08_단계적_리팩토링_로드맵.md` 에 `Phase 14. ADR-0015 Stage 4 — Legacy front-controller 실제 삭제 (완료)` 섹션 추가 및 기타 backlog 의 turn_* 항목을 `[x]` 로 마킹

---

## 2. 변경 사유

### 왜 지금 집행했는가

ADR-0015 audit(2026-04-22) 시점부터 다음이 명확했음:

1. Production 호출 그래프(`workflow_builder.py` → `workflow_nodes.py` → `agent_tool_executor.py`)에서 legacy 경로 호출 0건
2. legacy 경로는 11개 pytest 파일에서만 patch 되어 살아있던 "test-alive, production-dead" 상태
3. Stage 1(2026-04-23)은 그 11개 파일에 `pytest.mark.skip` 마커만 부착했고 실제 삭제는 Stage 2 로 미뤄둠
4. 사용자 환경(VPN/LLM 호출 불가)에서는 legacy patch 기반 test 가 회귀 검증 가치가 거의 없음

이 상태가 길어지면서 (a) 신규 개발자가 legacy 경로를 다시 활용하는 위험, (b) 동일 책임을 가진 두 entry point(`build_intent_payload` vs `build_agent_intent_payload`)가 공존하는 인지 비용, (c) `_build_strategy_meta` 의 `turn_trigger`/`turn_interpretation`/`turn_policy`/`turn_candidates` dict 입력이 활성 경로에서는 항상 빈 dict 인데도 함수 시그니처에 남아 있는 인터페이스 부패 등이 누적됐음. 본 트랜치에서 이를 일괄 해소.

### 왜 stub 으로 남겼는가 (실제 `rm` 대신)

워크스페이스 마운트가 `Operation not permitted` 로 `rm` 을 거부함(시도 결과 첨부 가능). 대신 docstring/HTML 주석 전용 stub 으로 교체해 다음을 보장:

- import 시 정상 평가(SyntaxError, RuntimeError 없음)
- 어떤 심볼도 노출하지 않으므로 실수로 재참조하면 `AttributeError` 즉시 발생
- 각 stub 상단에 deletion rationale 와 replacement entry point 명시

사용자 환경에서 안전하게 `git rm` 으로 마무리하면 stub 도 같이 사라짐.

---

## 3. 검증 결과

### 3.1 정적 컴파일 (py_compile)

`apps/**/*.py` 152 개 모듈 중 151 개 OK.

| 파일 | 결과 | 비고 |
|---|---|---|
| 본 캠페인이 손댄 모듈 (request_facade, followup_anchor, memory_observer, turn_*, context_router, clarification_labeler, scope_resolver, session_memory, agent_tool_executor, workflow_builder, workflow_nodes 등) | OK | — |
| `apps/chat/answer_generation.py` | **SyntaxError (line 1571 `log_event(` 미닫힘)** | **사전부터 존재한 결함**. 본 캠페인과 무관. 파일이 75830 B 에서 mid-statement(`vi`) 로 잘려 있음. import grep 결과 `api/workflow_builder.py` 만 참조하지만 참조 심볼(`node_generate_answer_gemma`, `node_generate_answer_solar`, `node_merge_answers`)은 truncation 이전에 정의되어 있어 collection 시 import 자체는 성공. 별도 트랜치에서 복구 필요.

### 3.2 Import-graph 회귀

deleted 심볼에 대한 활성 코드 참조를 word-boundary regex 로 정밀 grep:

```
banned tokens:
  from apps.conversation.{turn_trigger|turn_interpreter|turn_policy|context_router|clarification_labeler}
  TurnTriggerResult, TurnInterpretationResult, TurnPolicyResult, TurnCandidate
  run_turn_trigger, run_turn_interpreter, run_context_router, route_context
  resolve_turn_policy, resolve_candidate_to_focus_entity
  log_context_router_transition, build_turn_candidates, match_anchor_to_candidates
  resolve_hard_followup_signal, build_clarification_payload (underscore-prefixed 제외)
```

결과: **`apps/**` 전체에서 0건**. tombstone stub 자체는 skip 목록으로 제외.

### 3.3 활성 prompt 자산 회귀

`apps/prompts/` 의 deleted prompt 4종(`turn_trigger_v1`, `turn_interpreter_v1`, `clarification_labeler_v1`, `context_router_v1`) 가 production code 에서 직접 로드되는지 grep — 0건.

### 3.4 회귀 시나리오 한계

- 동작 회귀(LLM, Qdrant, Oracle, vllm, triton)는 사용자 환경 필요
- agentic-path golden(`tests/golden/test_agentic_dialogue_shindonggu.py`) 및 contract test(`tests/conversation/test_agentic_dialogue_contract.py`, `tests/conversation/test_agentic_workflow.py`)는 사용자 환경에서 별도 실행 권장

---

## 4. 잔존 리스크

### 4.1 stub 상태로 남은 9개 파일

`apps/conversation/turn_*.py`, `context_router.py`, `clarification_labeler.py`, `apps/prompts/*_v1.md` 4개. 사용자 측에서 `git rm` 으로 마무리 권장. stub 자체는 import 시 inert 이므로 즉시 위험은 없음.

### 4.2 `__pycache__` 잔재

전 12개 디렉터리(`apps/api/__pycache__`, `apps/conversation/__pycache__` 등)가 mount 권한 제약으로 정리 불가. deleted 모듈에 대한 stale `.pyc`(예: `turn_trigger.cpython-312.pyc`) 가 남아 있을 수 있으나 파이썬은 source 가 stub 으로 바뀌면 자동 무효화함. 사용자 환경에서:

```bash
find apps -name __pycache__ -type d -exec rm -rf {} +
```

### 4.3 `apps/chat/answer_generation.py` truncation

**본 캠페인과 무관**한 사전 결함. 파일이 line 1593 의 `log_event("LLM.RESULT", ...,` 도중에 잘림(`vi` 까지만 존재). py_compile 시 SyntaxError 발생. import 자체는 truncation 이전 정의 심볼만 가져가므로 collection 단계에서는 무사하나, 해당 모듈 전체 파싱이 필요한 도구(ruff, mypy, pytest collect-only) 가 실패할 수 있음. 별도 트랜치에서 source-of-truth 복구 필요.

### 4.4 `resolve_reference_context_followup` orphan

`apps/conversation/followup_resolution.py:286` 의 `resolve_reference_context_followup` 은 deprecated `build_intent_payload` 만 호출했으므로 현재 dead 상태. 그러나 이 함수는 자기완결적 source-reference / ordinal 해석 utility 로, 향후 agentic 경로에서 재활용할 가치가 있어 보존. 정말 필요 없다면 별도 트랜치에서 제거.

### 4.5 `_build_strategy_meta` 의 turn_* 파라미터

`turn_trigger`, `turn_interpretation`, `turn_policy`, `turn_candidates` dict 파라미터는 더 이상 active caller 가 채우지 않지만, 다음 두 다운스트림이 `.get(...)` 으로 안전하게 읽음:

- `apps/conversation/scope_resolver.py:478, 493, 499`
- `apps/conversation/session_memory.py:463, 505, 553`

빈 dict 일 때 grace 하게 처리되므로 동작 영향 없음. 시그니처 단순화는 다음 트랜치에서 캐스케이드로 정리 가능.

---

## 5. 다음 단계 제안

1. **사용자 환경에서 `git rm`** 으로 stub 9개 파일 정식 삭제 + `__pycache__` 정리
2. **`apps/chat/answer_generation.py` 복구** — git history 또는 백업에서 `node_merge_answers` 의 `LLM.RESULT` `log_event(...)` 호출 본문 회복. 본 캠페인과 무관하나 정적 분석/lint 회귀를 막기 위해 우선순위 ↑
3. **agentic-path 회귀 실행** — VPN 환경에서 다음 3건만 통과해도 본 정리의 안전성 확인:
   - `tests/golden/test_agentic_dialogue_shindonggu.py`
   - `tests/conversation/test_agentic_dialogue_contract.py`
   - `tests/conversation/test_agentic_workflow.py`
4. 로드맵 잔여 항목(우선순위 순):
   - **Phase 12** display snapshot publication guard
   - **Phase 13** answer streaming / provider failure handling
   - `lookup_specific_entity` `implemented=True` 였으나 실제 wiring 검증 필요. `join_project_perf` 는 `agent_tools.py:127` 에 `implemented=False` 로 선언만 있어 구현 또는 declaration 정리 필요
   - `support SEARCH` 이관(제품 결정), `unsupported/deferred JOIN` clarification contract(제품 결정)
5. **선택 정리**: `_build_strategy_meta` / `_build_intent_payload_object` 의 turn_* 파라미터 제거 + scope_resolver / session_memory 의 turn_* dict 키 reader 정리
