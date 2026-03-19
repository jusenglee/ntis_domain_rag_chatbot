# Retrieval Robustness 확장 계획

이 문서는 NTIS Domain RAG를 `retrieval-first search system with chat UX`로 유지하면서,
다양한 사용자 질의에 대한 검색 대응력을 확장하기 위한 설계 로드맵을 정리합니다.

## 문서 역할

- 이 문서는 retrieval 확장 로드맵 reference 문서입니다.
- 현재 strict contract나 mode 결정 규칙의 source of truth가 아닙니다.
- 현재 동작 정의는 `docs/CONTRACT.md`, `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`, `docs/MODE_DECISION_GUIDE.md`를 우선합니다.

## 1. 현재 상태 평가

현재 파이프라인은 다음 질의에 강합니다.

- `pjt_id`, `pjt_no`, `rst_id` 같은 식별자 기반 정확 조회
- `project ↔ perf` 관계가 분명한 JOIN 질의
- 사람, 기관, 연도, 성과유형 같은 구조적 힌트가 명시된 질의

반대로 다음 질의에는 보강이 필요합니다.

- 복합 조건이 섞인 탐색형 질의
- 비교형 질의
- 표현 변형이 큰 자연어 질의
- 후속 대화에서 생략이 많은 follow-up 질의
- 필드명을 모르는 사용자의 broad discovery 질의

## 2. 확장 원칙

- planner contract는 strict baseline을 유지합니다.
- `SEARCH / LOOKUP / JOIN` 경계는 그대로 둡니다.
- parser, planner, runtime이 전략 의미를 자동 보정하지 않습니다.
- 대응력 확장은 retrieval policy, reformulation, ranking, evidence shaping 계층에서 수행합니다.
- raw payload를 prompt에 직접 넣지 않고 canonical evidence 경유 원칙을 유지합니다.

## 3. 개선 축

### Query Reformulation

- 원문 질의에서 retrieval 의미를 보존한 reformulation 후보를 만듭니다.
- broad topic 질의는 recall 확장을 위한 reformulated query set을 허용합니다.
- exact lookup 질의는 reformulation보다 identifier fidelity를 우선합니다.

### Ambiguity Handling

- ambiguous query를 `planner contract 위반`과 `retrieval ambiguity`로 구분합니다.
- contract violation은 그대로 fail-close 합니다.
- retrieval ambiguity는 policy layer에서 다중 후보 query / soft filter / ranking diversification으로 처리합니다.

### Retrieval Expansion

- SEARCH 모드에서만 확장 정책을 우선 허용합니다.
- LOOKUP과 JOIN은 precision-first를 유지하고, 확장이 필요할 때도 identifier semantics를 바꾸지 않습니다.
- title / org / people / perf type expansion은 각 필드 의미를 유지하는 범위에서만 적용합니다.

### Ranking / Selection

- retrieval hit가 많을수록 score fusion과 rerank 기준을 더 명시적으로 관리합니다.
- broad query에는 recall 확보 뒤 rerank를 강화하고, lookup/join query에는 hard signal 보존을 우선합니다.
- ranking은 evidence fidelity를 해치지 않아야 하며, source semantics를 섞으면 안 됩니다.

### Evidence Shaping

- canonical evidence는 retrieval robustness 개선 이후에도 source semantics를 그대로 유지해야 합니다.
- `output_type`은 answer style이 아니라 evidence presentation shape라는 원칙을 유지합니다.
- 탐색형 질의와 정확 조회 질의는 같은 prompt view를 재사용하지 않습니다.

## 4. 질의 유형별 목표

| 질의 유형 | 현재 상태 | 목표 |
|---|---|---|
| exact identifier lookup | 강함 | strict precision 유지 |
| relation join | 강함 | group fallback observability 강화 |
| broad topic search | 보통 | reformulation + recall 확장 |
| complex constrained search | 보통 이하 | field-aware policy 추가 |
| comparative query | 약함 | multi-evidence retrieval / aggregation 보강 |
| follow-up query | 보통 | canonical evidence anchor 기반 보강 |

## 5. 후속 구현 우선순위

1. retrieval ambiguity taxonomy 문서화
2. SEARCH 전용 reformulation policy 초안 추가
3. ranking / rerank 관측 포인트 표준화
4. query class별 golden query 묶음 확장
5. evidence shaping acceptance criteria 세분화

## 6. acceptance 기준

- strict contract regression이 없어야 합니다.
- `pjt_id`, `pjt_no`, 기관 역할 필드 의미가 유지되어야 합니다.
- broad query recall 개선이 lookup/join precision 저하로 이어지면 안 됩니다.
- golden query는 strategy correctness, evidence fidelity, render correctness 순으로 검증합니다.
- retrieval robustness 확장은 chat UX가 아니라 retrieval layer 품질로 측정합니다.

## 함께 읽을 문서

- 현재 동작과 흐름: `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`
- mode 결정 규칙: `docs/MODE_DECISION_GUIDE.md`
- 파라미터 정의: `docs/PLANNER_PARAMETER_REFERENCE.md`
- strict contract: `docs/CONTRACT.md`
