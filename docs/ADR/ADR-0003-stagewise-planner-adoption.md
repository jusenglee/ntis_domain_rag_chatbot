# ADR-0003: Stagewise Planner Adoption with Deterministic Gate

- 상태: Draft
- 날짜: 2026-03-12
- 관련 문서:
  - `docs/CONTRACT.md`
  - `docs/RUNBOOK.md`
  - `docs/GOLDEN_TESTS.md`
  - `docs/README.md`

## 배경
기존 planner는 giant one-shot prompt가 `mode / head / action / relation / join_key_mode / ids_map / filters / target_cols / retrieval_query / confidence`를 한 번에 결정하는 구조였다.
실운영에서는 `PLANNER_ACTION_MODE_MISMATCH`(예: `action=topic, mode=lookup`)가 반복적으로 발생했고, broad perf topic이 blind JOIN으로 과발동하는 문제가 관찰되었다.

또한 parser/validator/merge layer가 planner 출력을 다시 canonicalize/보정/검증하면서, 실제 전략 결정 책임이 하나의 레이어에 집중되지 않는 문제가 있었다.

## 결정
planner 내부 생성 경로를 stagewise로 분리한다.

1. Stage 1
   - 책임: `action`, `head`, `relation_candidate`, `referential_followup`, `confidence`
   - 전략/슬롯을 동시에 결정하지 않는다.

2. Deterministic Gate
   - 책임: `mode`, `relation`, `join_key_mode`, `target_cols`
   - 원칙:
     - `action=topic -> SEARCH`
     - `action in {list,detail,stats,download} -> 기본 LOOKUP`
     - `JOIN`은 relation candidate + join seed가 있을 때만 허용

3. Stage 2
   - 책임: `ids_map`, `filters`, `retrieval_query`, `limit`, `confidence`
   - 전략 필드는 수정하지 않는다.

4. Final Assembly
   - executor-facing final contract(`QuestionAnalysisV2`)는 유지한다.
   - executor는 여전히 single final strategy만 입력으로 받는다.

## 대안 검토

### 대안 A — giant one-shot planner 유지
- 장점: LLM 호출 1회, 구현 간단
- 단점: action/mode 충돌과 JOIN 과발동을 한 prompt 안에서 통제하기 어렵고, parser/validator rescue에 의존도가 높음

### 대안 B — full multi-agent planner
- 장점: 역할 분리 극대화
- 단점: 지연/운영 복잡도 증가, 현재 시스템 규모 대비 과함

### 채택안 — stagewise planner + deterministic gate
- 장점: giant prompt의 책임을 줄이면서도, mode/relation/join_key_mode/target_cols의 최종 소유권을 코드에 둘 수 있음
- 장점: executor-facing final contract는 유지 가능
- 단점: stage2 이후 새로 추출한 ids를 전략 재판정에 반영하는 `post-stage2 re-gate`가 추가로 필요

## 영향

### 긍정적 영향
- action/mode 충돌을 구조적으로 줄일 수 있음
- broad relation keyword가 blind JOIN으로 이어지는 것을 줄일 수 있음
- stage1/2를 분리함으로써 golden/eval을 단계별로 설계할 수 있음
- ids_map semantic invalid value를 별도 로그(`PLANNER.IDS_MAP.INVALID_VALUE`)로 관측 가능

### 부정적/잔존 리스크
- 기본값이 아직 `PLANNER_STAGEWISE_ENABLED=false`이면 운영 기본 경로가 legacy일 수 있음
- stage2에서 새로 얻은 ids가 최종 전략 재판정에 아직 반영되지 않음(`post-stage2 re-gate` open issue)
- stagewise assembled 결과가 이후 old merge layer의 strict mismatch 검사에 의해 재실패할 수 있음

## rollout 제안
1. `PLANNER_STAGEWISE_ENABLED=true`를 staging에서 기본 적용
2. canary 10% → 50% → 100%
3. 아래 지표를 관찰
   - `PLANNER_ACTION_MODE_MISMATCH`
   - `PLANNER_PARSE_FINAL_FAILED`
   - `RAG_EMPTY_RESULT_CONTRACT`
   - `mode=JOIN & docs_found=0`
   - `PLANNER.IDS_MAP.INVALID_VALUE`

## 오픈 이슈
- `post-stage2 re-gate` 도입 여부 및 범위
- stagewise path에서 `apply_planner_strategy()` strict mismatch 검사를 우회/축소할지 여부
- support target 컬렉션(`ntis_supports_v1`)과 런타임 상수 정합화
