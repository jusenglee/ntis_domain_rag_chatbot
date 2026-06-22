# 03. 행동 안전 (검색 실패·오류 시 어떻게 복구하나)

> 검색이 0건이거나 도구가 오류를 낼 때 시스템이 무엇을 하는지(그리고 **무엇을 하면 안 되는지**)를 다룬다. §1~§2가 개념, §3 이후는 정밀 참조다. 소유 코드: `apps/retrieval/execution_manager.py`의 `ExecutionManager`.

## 1. 핵심 원칙 두 가지

1. **결과 0건은 오류가 아니다.** 진짜로 데이터가 없을 수 있다. 억지로 답을 만들지 않는다.
2. **복구는 '정해진 범위 안에서만'.** 검색이 빈손이라고 조건을 마음대로 풀어 더 넓게 긁으면, *엉뚱한 대상*이 답에 섞인다. 그래서 복구는 "이 정책에선 이것만 1회 허용" 식으로 **경계가 박혀 있다.**

비유: 도서관에서 책을 못 찾았을 때, "비슷한 거 아무거나 들고 오기"가 아니라 "철자 한 글자만 바꿔 다시 한 번 찾아보기"까지만 허용하는 식이다.

## 2. 가장 자주 만나는 안전장치 — Detail Guard

"상세 보여줘"인데 대상이 하나로 확정 안 되면:

- 하나로 확정됨 → 상세 조회 진행.
- 여럿 → 되묻거나 짧은 관찰(observation)로 닫는다.
- 없음 → "없음" 또는 되묻기.
- **절대 금지:** 상세 요청을 17~20건짜리 넓은 검색으로 바꾸는 것(`SEARCH_RECOVERY`). 차단되면 `RAG.DETAIL.SINGLE_CANDIDATE_GUARD` 로그 + `detail_guard_blocked=true`.

## 3. 정밀 참조 — 복구 정책표

각 검색 상황마다 "1차/2차로 뭘 시도하고, 무엇이 허용/금지인지"가 정책으로 박혀 있다.

| 정책 | 적용 | 허용된 복구 | 금지 |
|---|---|---|---|
| `LOOKUP_MISSING_RECOVERY` | project LOOKUP | 같은 필터 유지한 SEARCH 1회 | 정확 ID 질의의 임의 완화 |
| `SEARCH_RECOVERY` | project SEARCH | people/org 필터 완화 1회 | 모드 변경, `pjt_id`/`pjt_no` 축 drift, 활성 anchor 무시, detail류 broad 확장, 단일후보 미확정 first-candidate detail |
| `PEOPLE_SEARCH_OBSERVATION` | people SEARCH | `max_steps=1`, 관찰만 | 자동 교정 (근거는 project+perf) |
| `ORG_SEARCH_OBSERVATION` | org SEARCH | 동일 | 동일 |
| `PERF_SEARCH_OBSERVATION` | perf SEARCH | 동일, `target_cols=[COL_PERF]` | `display_source`는 항상 `docs` |
| `JOIN_QUALITY_RECOVERY` | project JOIN | instance JOIN 0행 + anchor에 `pjt_no` 존재 + 명시 `pjt_id` 없음 → `group` 1회 재시도 | 키 날조, 축 자유 재해석, 관계 변경, 2회 이상 재시도 |

> 아직 레거시(이 틀 밖)인 경로: `support SEARCH`, 미지원/지연(deferred) `JOIN`, 계약 없는 specialist flow.

`SEARCH_RECOVERY` 관찰 코드: `empty_primary_search`, `filter_gate_too_strict`, `candidate_underflow`, `relax_budget_exhausted`, `search_recovery_succeeded`.

## 4. 정밀 참조 — 내부 오류 루프 (Internal Error Loop)

도구·플래너·스키마가 *내부적으로* 깨졌을 때(사용자 모호성과 다름):

- 원본 덤프가 아니라 **짧은 관찰**로 만들어 Agent에 1회 되돌린다 → 그래도 안 되면 `agent_internal_error`로 종료.
- 진짜로 사용자 대상이 여러 개라 모호할 때만 → 되묻기(clarification).
- **내부/파서/스키마 오류를 `ClarificationContext`(되묻기 맥락)로 저장하지 않는다.** ("시스템이 고장난 것"을 "사용자가 애매한 것"으로 위장 금지.) — 근거: ADR-0015.
- 정상 순서: `AGENT.TOOL_OBSERVATION(error)` → `AGENT.TOOL_RETRY.START` → `AGENT.TOOL_RETRY.DECISION` → (여전히 실패) `AGENT.INTERNAL_ERROR`.

## 5. 정밀 참조 — 관찰 메타 필드

- `execution_trace`(스텝별): `policy`, `tool_name`, `status`, `reason`, `observation_codes`, `diagnostics`.
- `retrieval_runtime_meta`(최소): `runtime_owner`, `policy_name`, `orchestrator_owned`, `legacy_retry_allowed`, `execution_kind`, `base_route`, `observation_only`. JOIN 복구 시 `group_recovery_attempted`, `group_recovery_applied`, `recovery_join_axis`, `recovery_source` 추가.
- `selected_answer_meta`(사용자용 요약): `recovery_applied`, `recovery_policy`, `recovery_steps`, `recovery_user_notice`.

contract-invalid → LLM 답변 스트리밍 없음. detail-guard 실패 → 결정적 종료 메시지 또는 Agent 명확화 관찰.
