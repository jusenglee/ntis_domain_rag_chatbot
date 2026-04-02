# ADR-0002: Strict Strategy Contract를 현재 Baseline으로 채택

- 상태: Accepted
- 최초 작성: 2026-03-09
- 수정일: 2026-03-16
- 관련 문서: `docs/CONTRACT.md`, `docs/RUNBOOK.md`, `docs/GOLDEN_TESTS.md`, `docs/ENVIRONMENT.md`

## 배경

planner / executor / runtime 계약은 실행 레이어가 전략을 다시 만들어내지 못하게 하고, 계약 위반은 명시적인 오류로 드러나야 한다.
현재 저장소의 운영 기본 경로는 staged-only runtime이며, planner invalid fallback이나 후단 promotion 재실행에 의존하지 않는다.

이 ADR은 더 이상 "strict로 전환할지"를 논의하는 문서가 아니다.
현재 기본 경로 자체가 strict / fail-close라는 점을 기준선으로 고정하기 위한 문서다.

## 결정

1. 운영 기본값은 strict / fail-close로 둔다.
2. planner invalid / contract violation은 `StrategyViolation`으로 종료한다.
3. 하위 execution layer는 planner fallback strategy를 새로 만들지 않는다.
4. promotion은 실행 mode를 재결정하는 경로가 아니며, 현재 전략 내 정책 해석으로만 다룬다.
5. 문서와 테스트는 strict를 예외 경로가 아니라 기본 경로로 가정한다.

## 근거

- `docs/CONTRACT.md`는 final strategy 소유권을 gate / assemble에 두고 실행 레이어의 재결정을 금지한다.
- `docs/RUNBOOK.md`는 운영 triage를 strict 기본 경로 기준으로 설명한다.
- `docs/GOLDEN_TESTS.md`는 strict 경로에서 유지되어야 하는 `mode`, `relation`, `join_key_mode`, `ids_map` invariant를 고정한다.

## 결과

### 긍정적 효과

- 계약 위반을 warning이나 silent correction으로 넘기지 않는다.
- planner와 executor의 책임 경계가 더 분명해진다.
- 추가 테스트와 운영 로그를 해석하기 쉬워진다.

### 남아 있는 리스크

- `apps/retrieval/rag_pipeline.py`가 아직 크기 때문에 strict 기준선이 여러 함수에 흩어져 있다.
- strict 기준선의 실제 소유 코드가 계속 `apps/*` 전반에 퍼져 있다.
- 검증 기준선은 여전히 `tests/` 쪽 확장이 필요하다.

## 후속 작업

1. `apps/retrieval/rag_pipeline.py`에서 compile / filter / join / result 정책을 단계적으로 분리한다.
2. 문서와 테스트에서 strict를 전제한 기준선을 유지하고, 옛 compat 서술은 더 이상 확대하지 않는다.

