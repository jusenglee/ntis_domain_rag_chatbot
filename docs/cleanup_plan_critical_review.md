# Cleanup Plan — Critical Review (비판적 검토)

> **검토 대상**: `docs/cleanup_plan_2026-05-28.md` (v1, 2026-05-28 작성)
> **검토 일자**: 2026-05-28
> **검토 방법**: seungju-critical-review 스킬 적용
> **검토자 입장**: 사용자(도련님)의 시간·코드 안정성을 우선하는 비판적 second-opinion

---

## 결론

이 계획서는 구조와 사전 검증 의도는 좋지만, 다음 세 가지 구조적 약점이 있다.

1. **Task 1을 Task 5 결정 전에 실행하는 권장 순서가 비합리적이다.** Task 5a(영구 폐기)를 선택하면 Task 1의 `if/else` 분기가 곧장 dead code가 되어 두 번 일하게 된다.
2. **수치 근거가 추측이면서 "추측" 표기가 빠진 곳이 다수 있다.** "200~800ms", "토큰 30~40% 절감", "9장 600줄?(물음표)" 등. 사용자 메모리 규칙(추측은 '추측입니다' 명시)에 위배.
3. **Task 1·2의 핵심 전제가 verbatim 인용·grep 결과로 검증되지 않았다.** "5개 legacy agent가 agentic에서 호출되지 않음", "5개 kind가 agentic 분기에 안 쓰임"이라는 단정은 인용된 코드(runtime.py L196-213) 한 블록만으로는 입증 불가.

**핵심 권장**: Task 5 의사결정(Q1)을 Phase 1로 끌어올리고, Task 1·2·3을 그 결정에 종속시키는 순서로 재편.

---

## 맞는 부분 (preserve before correcting)

- **0-4 사전 발견 사실 반영**: 이전 분석의 dead 파일 목록을 실측으로 재확인하여 Task 5 범위를 축소한 것은 매우 좋은 자기 수정.
- **G1~G6 검증 게이트와 Task별 매핑표**(Section 7): 작업 중 판단 흔들림 방지 장치로 우수.
- **Task 3에 BLOCKER 표시**(3-2): 로더 위치 미확정 상태에서 진입 차단한 안전 장치.
- **Task 5에 사용자 의사결정(5a/5b/5c) 명시**: 의사결정 책임을 명확히 분리한 점 합리적.
- **각 task별 rollback 명시**(단일 커밋 revert): 운영 안전망으로 적절.
- **Open Questions 5개**(Section 8): 검토자 입장에서 모르는 것을 명시한 점 정직.

---

## 애매하거나 위험한 부분

### A. Task 1의 "위험: 낮음" 주장이 검증 부족

계획서는 "호출되지 않는 객체를 None으로 대체"라고 단정한다(1-7). 이 주장이 성립하려면 다음 두 가지가 grep으로 확인되어야 하나, **계획서에 확인 흔적이 없다** (확실하지 않음):

1. `apps/pipeline/agentic_workflow.py`와 그 호출 체인 어디에서도 `deps.dialogue_agent` / `entity_resolver` / `search_planner` / `retrieval_agent` / `evidence_curator`를 참조하지 않을 것.
2. `AgentPipelineDeps`가 `Optional[X] = None`을 허용할 것 (1-5 선행 micro-task로 미룬 부분).

위 둘 중 하나라도 위반되면, 부팅(G1/G2)은 성공하지만 첫 요청에서 `AttributeError: 'NoneType' object has no attribute '...'` 류로 폭발한다. **G3에서야 발견될 위험**이고, 운영 배포 후 발견되면 incident.

### B. Task 1의 이득 수치는 측정되지 않은 추측

"부팅 시간 200~800ms 단축"(1-7)은 **추측이다.** 계획서는 "(실측 필요)"라고 적었으나, 다음 컨텍스트가 빠졌다:

- 운영 환경이 long-running uvicorn process라면 cold start 이득은 **user-facing impact 사실상 0**.
- 컨테이너 재시작 빈도가 분당 수 회가 아니면 의미 미미.

운영 환경의 재시작 빈도는 **모르겠습니다** — 정보 없음.

### C. Task 2 옵션 A의 LLM compliance 가정

옵션 A는 "schema에서 5개 kind 제거 + prompt에서 안내만 삭제"(2-4). 그러나 **LLM은 종종 instruction을 어긴다.** 만약 LLM이 학습된 패턴에 따라 여전히 `"kind": "stats"`로 응답하면, downstream의 `Literal["ask_search","ask_detail","direct_answer","clarification"]` validation에서 `ValidationError`. 계획서에 fallback이 없다.

**필요한 안전장치**: DialogueAgent에 "알 수 없는 kind는 `ask_search`로 normalization" 단계 추가. 이를 옵션 A 변경분에 명시적으로 포함해야 한다.

### D. Task 3의 토큰 절감 수치가 비현실적

"30~50% 토큰 절감"(3-6)은 **카드 로더 위치도 모르면서** 산정한 수치 (확실하지 않음). 9장 → 3장은 LOC 감소일 뿐 토큰 감소가 아니다. 카드 간 중복이 정확히 몇 %인지 측정되지 않았고, 통합 카드가 결국 원본의 80%를 그대로 옮기면 LOC는 줄어도 토큰은 거의 그대로일 수 있다.

### E. Task 4의 권장 절차가 자기모순

4-3은 "운영 로그에서 비율 측정 후 결정"이라 했지만, Section 8 Q3에서 **그 측정 가능한 로그/대시보드가 있는지 자가 의심**하고 있다. 측정 인프라가 없다면 권장 절차 자체가 실행 불가. **선행 task로 "AdequacyGate/Critic 결정 로그 emit 추가"** 가 4 앞에 끼워져야 한다.

### F. Phase 1 → Phase 2 작업 중복

Section 6의 권장 순서는 Task 1을 Phase 1로 즉시 실행하지만, Phase 2에서 Task 5a 선택 시 Task 1의 `if agentic_mode_enabled:` 블록 자체를 다시 지우게 된다. **두 번 작업**.

---

## 놓친 전제 (missing assumptions)

1. **테스트 코드 영향 미언급**: `tests/`에 legacy 5개 agent를 import/mock하는 테스트가 있다면 Task 1·5에서 함께 수정 필요. 계획서에 `tests/` 영향 분석 항목 없음.
2. **AGENTS.md / 온보딩 문서**: 워크스페이스 root에 `AGENTS.md`가 존재함이 확인됨. Task 5에서 legacy를 폐기하면 여기 또는 README에 남은 7-agent 설명이 outdated가 됨. 문서 업데이트 누락 시 신규 기여자 혼란.
3. **운영 모니터링 의존성**: Datadog/Grafana 대시보드가 `dialogue_agent.classify` 같은 메트릭/로그 이름에 의존한다면 폐기 시 alert가 깨질 수 있음. **확실하지 않음** — 운영 메트릭 현황 정보 없음.
4. **세션 호환성 (G5)**: Task 1·2에서 `kind` 분류가 바뀌면, 기존 세션 상태에 저장된 `"stats"` 같은 kind 값을 load할 때 Literal validation 실패 가능. **마이그레이션 전략이 없다.**
5. **G3/G4의 single-shot 한계**: smoke test 1건씩으로는 회귀 발견 불가. 최소 5건 미니 회귀가 필요.
6. **부팅 시간 baseline 부재**: Task 1의 이득 주장을 검증할 baseline 측정이 없음.

---

## 수정하면 더 좋아지는 방향

### 1. Execution Order 재편 (가장 임팩트 큰 수정)

```
Phase 0 — Decision gate (의사결정 우선)
  ↳ Q1 (Task 5a/5b/5c) 결정
  ↳ Q4 (회귀 평가 셋 존재 여부) 확인

Phase 1 — Pre-flight grep 검증 (read-only, 위험 0)
  ↳ agentic_workflow에서 legacy 5개 deps 참조 0건 확인
  ↳ tests/에서 legacy agent 의존도 확인
  ↳ Task 3-pre: 카드 로더 위치 확정 (Q2 답변 또는 grep)
  ↳ wc -l apps/prompts/cards/*.md (실측)

Phase 2 — Apply
  if 5a: Task 5 직접 진행 (Task 1·2 흡수)
  if 5b: Task 5 + branch 분기
  if 5c: Task 1 + Task 2 옵션 A

Phase 3 — Cards (Q4 평가 셋 확보 후에만 진입)
Phase 4 — AdequacyGate/Critic (로그 인프라 선행 task 필요)
```

### 2. 검증 게이트 보강

| 신규 게이트 | 방법 | 통과 기준 |
|-------------|------|-----------|
| G7. 단위 테스트 통과 | `pytest tests/` | exit code 0 |
| G8. 부팅 시간 baseline 비교 | `time uvicorn ...` 5회 평균 | Task 1 변경 전/후 측정 |
| G9. 미니 회귀 (5건) | 사전 정의된 5개 질문 동시 호출 | references / answer_kind 분포 동일 |

### 3. Task 1에 prerequisite checklist 명시 추가

```
[ ] grep -n "deps\.\(dialogue_agent\|entity_resolver\|search_planner\|retrieval_agent\|evidence_curator\)" apps/pipeline/agentic_workflow.py apps/pipeline/agents/
    → 0건이어야 함
[ ] AgentPipelineDeps 필드 타입을 Optional[X] = None으로 완화
[ ] tests/ 영향도 스캔: 위 5개 agent를 직접 import/mock하는 테스트 식별
[ ] DialogueAgent에 unknown-kind normalization 안전장치 (Task 2와 동반 진행 시)
```

### 4. 정직한 수치 표기로 교정

| 원문 | 교정안 |
|------|--------|
| "부팅 시간 약 200~800ms 단축 예상" | "추측: 200~800ms (미실측, 컨테이너 재시작 시에만 의미 있음. 운영 재시작 빈도는 모름)" |
| "토큰 30~40% 절감" | "추측: 카드/프롬프트 중복도 측정 후 확정" |
| "9장 600줄?" | 물음표 제거. `wc -l`로 실측 후 기재 |
| "위험: 낮음" (Task 1) | "위험: prerequisite checklist 통과 시 낮음, 미통과 시 중간~높음" |

### 5. 빠진 항목 명시적 추가

- 각 Task에 **테스트 영향도** 라인 추가 (예: "tests/test_dialogue_agent.py에 영향 — 함께 정리")
- Task 5에 **AGENTS.md / 온보딩 문서 갱신** 항목 추가
- Task 1·2에 **세션 상태 마이그레이션 전략** 항목 (또는 "기존 세션은 폐기 가능하므로 N/A" 명시)
- Task 4 앞에 **로그 인프라 추가 micro-task** 끼워넣기

---

## 최종 권장안

1. **Task 1을 즉시 실행하지 말 것.** Open Questions Q1(Task 5 폐기 의향)을 먼저 답하면 Task 1 자체가 불필요해질 수 있다.
2. **다음 즉시 실행 가능한 작업은 "Phase 1 grep 검증"이다** — 위험 없는 read-only 조사. 결과에 따라 Task 1·5의 위험도가 재평가된다.
3. **Task 3는 Q4(회귀 평가 셋 존재 여부) 답변 전까지 진입 금지** — BLOCKER로 격상.
4. **이 비판 내용을 원본 계획서 v2로 반영**하거나, **Section 10 (Addendum: Critical Review)** 으로 추가하는 것을 권장.
5. **계획서의 수치 표기 규칙을 사용자 메모리 규칙에 맞게 정리**: 측정값은 그대로, 추측은 "추측:", 출처 불명은 "확실하지 않음" 명시.

---

## Open Questions (검토자가 추가로 묻고 싶은 것)

1. **Q-Add-1**: `tests/` 디렉터리에 legacy 5개 agent에 직접 의존하는 테스트가 있는가? (있다면 Task 1·5의 변경 범위 확장 필요)
2. **Q-Add-2**: 운영이 long-running process인가, 잦은 재시작 환경인가? (Task 1의 이득 평가에 영향)
3. **Q-Add-3**: 세션 상태(local_kvstore)에 `kind` 값이 영속화되는가? (Task 2의 마이그레이션 필요 여부)
4. **Q-Add-4**: AGENTS.md에 7-agent 구조 설명이 포함되어 있는가? (Task 5의 문서 갱신 범위)
5. **Q-Add-5**: Datadog/Grafana 대시보드가 legacy agent 이름 기반 메트릭에 의존하는가? (Task 5의 운영 영향)

---

*이 비판 검토는 원본 계획서를 부정하는 것이 아니라, 실행 전에 약점을 드러내어 안전한 실행 경로를 확보하기 위한 second-opinion이다. 원본 계획의 의도와 구조는 대체로 합리적이며, 위 수정안은 그 구조 위에 안전장치를 덧대는 성격이다.*

---

## 11. Addendum — Q-Add 답변 반영 (2026-05-28)

사용자(도련님) 답변:

| 질문 | 답변 |
|------|------|
| Q-Add-1: tests에서 legacy 5개 agent 직접 의존 | LLM/Qdrant가 운영에만 붙어 의미 없음 |
| Q-Add-2: 운영 환경 (재시작 빈도) | **long-running process** |
| Q-Add-3: 세션 상태(kind) 영속화 | 영속 안 됨, 브라우저 세션 한정 |
| Q-Add-4: AGENTS.md에 7-agent 설명 | 포함되어 있지 않음 |
| Q-Add-5: Datadog/Grafana 메트릭 의존 | **legacy agent 이름 기반 메트릭에 의존** |

### 11-1. 답변이 계획에 미치는 영향

**(1) Task 1의 ROI 급락 — 단독 실행 가치 거의 없음** (Q-Add-2 영향)
- 운영이 long-running이라면 부팅 200~800ms 단축(미실측 추측)은 user-facing 0.
- 결론: **Task 1을 Phase 1로 단독 실행할 이유가 사라짐**. Task 5와 묶어 처리하는 것이 합리적.

**(2) Task 2 옵션 A의 backward-compat 우려 해소** (Q-Add-3 영향)
- 세션 영속화가 없으므로 Literal 축소로 인한 마이그레이션 부담 없음.
- 단, **LLM unknown-kind normalization 안전장치는 여전히 필요** (LLM의 instruction 위반은 영속화 여부와 무관).
- 결론: **옵션 A 안전**. Task 5와 묶거나 단독 진행 가능.

**(3) Task 5에 신규 BLOCKER 발견 — 원본 계획 미포함** (Q-Add-5 영향)
- legacy agent 이름 기반 메트릭이 운영 대시보드/알람에 사용 중.
- Task 5(영구 폐기 또는 브랜치 분리)는 **모두** 운영 메트릭을 끊는다.
- **필수 선행 작업**:
  - (a) 의존 메트릭 인벤토리: 어떤 Datadog/Grafana 항목이 legacy agent 이름(`dialogue_agent.*`, `entity_resolver.*` 등)을 참조하는가
  - (b) agentic 메트릭으로 매핑 또는 알람 정리 계획
  - (c) Datadog/Grafana 변경분 PR 또는 ticket
- 이 인벤토리 비용이 무겁다면 **사실상 5a/5b 선택이 어려워지고 5c로 강제될 수 있음**.

**(4) tests/ 영향 — CI에서는 무시 가능** (Q-Add-1 영향)
- 운영에서만 도는 통합 테스트는 cleanup PR CI에서 깨지지 않음.
- 단, **확실하지 않음** — `tests/` 내 mock 기반 테스트가 legacy agent를 직접 import하는지는 grep으로 마지막 확인 권장.

**(5) AGENTS.md 갱신 부담 최소** (Q-Add-4 영향)
- AGENTS.md에는 7-agent 설명 없음.
- **확실하지 않음**: README나 다른 docs에 7-agent 언급이 있는지. Task 5 진입 시 `grep -rn "DialogueAgent\|EntityResolver\|RetrievalAgent" --include="*.md"` 한 번이면 충분.

### 11-2. 재정리된 실행 경로

```
Phase 0 — 의사결정 + 인벤토리 (필수 선행)
  ┌────────────────────────────────────────────────────────┐
  │ Step A. 운영 메트릭 인벤토리 (NEW BLOCKER)             │
  │   ↳ Datadog/Grafana에서 legacy agent 이름 참조 식별   │
  │   ↳ 인벤토리 비용 산정 → 5a/5b 가능성 판단             │
  │                                                          │
  │ Step B. Q1 답변 (5a/5b/5c)                              │
  │   ↳ Step A 결과를 근거로 결정                           │
  │                                                          │
  │ Step C. (5a/5b 선택 시) 카드 로더 위치 확정 (Q2)       │
  │ Step D. (Task 3 대비) 회귀 평가 셋 확보 (Q4)           │
  └────────────────────────────────────────────────────────┘
                        ↓
  ┌────────────────────────────────────────────────────────┐
  │ Phase 1 — Read-only grep 검증 (위험 0)                  │
  │   ↳ agentic_workflow에서 legacy 5개 deps 참조 0건      │
  │   ↳ tests/에서 legacy agent 직접 import 스캔            │
  │   ↳ docs/*.md에서 7-agent 언급 스캔                    │
  │   ↳ wc -l apps/prompts/cards/*.md (실측)               │
  └────────────────────────────────────────────────────────┘
                        ↓
  ┌────────────────────────────────────────────────────────┐
  │ Phase 2 — Apply                                          │
  │   if 5a/5b: 메트릭 정리 PR → Task 5 → Task 2 옵션 A    │
  │             (Task 1은 5에 흡수되어 별도 PR 불필요)      │
  │   if 5c:    Task 2 옵션 A만 단독 진행                  │
  │             (Task 1은 ROI 0이므로 보류 또는 폐기 권장)  │
  └────────────────────────────────────────────────────────┘
                        ↓
  Phase 3 — Cards (회귀 평가 셋 확보 후에만)
  Phase 4 — AdequacyGate/Critic (로그 인프라 선행)
```

### 11-3. 최종 권장 (v2)

1. **Task 1 단독 실행은 권장하지 않음** (ROI 0). 5a/5b 선택 시 Task 5에 흡수, 5c 선택 시 보류 또는 폐기.
2. **Task 5의 신규 BLOCKER는 운영 메트릭 인벤토리**. 이걸 먼저 가늠하지 않고 Q1 답변 시 잘못된 결정 위험.
3. **Task 2 옵션 A는 단독 진행 안전** (unknown-kind normalization 동반 시). 5c 선택 시 유일한 의미 있는 cleanup이 될 수 있음.
4. **가장 즉시 실행 가능한 다음 작업**:
   - Step A (메트릭 인벤토리 비용 가늠) 또는
   - Phase 1 read-only grep 검증 묶음 (위험 0, 위 의사결정의 근거 데이터 확보)

### 11-4. 남은 미해결 Open Questions

- **Q1**: Task 5 폐기 의향 (5a/5b/5c) — Step A 결과 본 후 결정 권장
- **Q2**: 카드 로더 위치 — grep으로 자체 확인 가능
- **Q3**: AdequacyGate/Critic 운영 통계 측정 가능 여부 — Task 4 진입 시 다시 묻기
- **Q4**: 답변 품질 회귀 평가 셋 존재 여부 — Task 3 BLOCKER
- **Q5**: 도메인 검토 담당자 — Task 3 진입 시 결정

