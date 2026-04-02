# ADR-0003: Deterministic Gate를 포함한 Stagewise Planner 채택

- 상태: Accepted
- 작성일: 2026-03-12
- 수정일: 2026-03-16

## 현재 메모

이 ADR은 planner 생성을 stagewise로 유지하기로 한 결정을 기록한다.
현재 runtime baseline은 staged-only + strict다.
하위 레이어 fallback이나 promotion 재실행은 현재 baseline에 포함되지 않는다.

## 런타임 형태

- runtime entry: `apps/api/main.py`
- app assembly: `apps/api/app_factory.py`
- workflow graph: `apps/api/workflow_builder.py`
- planner / executor source of truth: `current feature package modules`

## 결정

Planner 생성은 네 단계로 나눈다.

1. Stage 1
   - `action`, `head`, `relation_candidate`, `referential_followup`, `confidence`를 출력한다.
2. Deterministic gate
   - `mode`, `relation`, `join_key_mode`, `target_cols`, `output_type`을 결정한다.
3. Stage 2
   - `ids_map`, `filters`, `retrieval_query`, `limit`, `confidence`를 출력한다.
4. Final assembly
   - executor-facing final strategy object를 만든다.

## 이유

- action / mode mismatch를 줄인다.
- 근거 없는 JOIN 과다 분기를 줄인다.
- strategy ownership을 명시적으로 유지한다.
- stage 단위 validation과 logging을 가능하게 한다.

## 결과

긍정적 효과:
- strategy field 소유권이 더 분명해진다.
- contract validation이 쉬워진다.
- stagewise planner 흐름 observability가 쉬워진다.

잔여 리스크:
- `apps/retrieval/rag_pipeline.py`는 여전히 크다.
- 테스트 baseline은 compile check를 넘어 더 확장되어야 한다.


