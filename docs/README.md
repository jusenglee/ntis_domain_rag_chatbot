# 문서 안내 (Docs Guide)

이 디렉터리는 NTIS Domain RAG 저장소의 기준 문서를 보관합니다.

이 시스템의 공식 정체성은 `retrieval-first search system with chat UX`입니다.
즉, 검색 전략과 근거 정합성이 1차 계약이며, 대화형 경험은 그 위에 얹힌 상위 UX 계층입니다.

## 30초 시작

- 구조 이해: `ARCHITECTURE_RETRIEVAL_FIRST.md`
- 실행 흐름: `SYSTEM_FLOW_RETRIEVAL_FIRST.md`
- 절대 규칙: `CONTRACT.md`
- 운영 / 장애 대응: `RUNBOOK.md`

위 4개를 먼저 보고, 그 다음에 필요하면 `MODE_DECISION_GUIDE.md`, `PLANNER_PARAMETER_REFERENCE.md`, `GOLDEN_TESTS.md`를 봅니다.

## 자세히 읽을 문서

작업을 시작할 때 우선 읽어야 하는 문서:

1. `ARCHITECTURE_RETRIEVAL_FIRST.md`
2. `SYSTEM_FLOW_RETRIEVAL_FIRST.md`
3. `CONTRACT.md`
4. `RUNBOOK.md`
5. `MODE_DECISION_GUIDE.md`
6. `PLANNER_PARAMETER_REFERENCE.md`
7. `GOLDEN_TESTS.md`
8. `ENVIRONMENT.md`
9. `RETRIEVAL_ROBUSTNESS_PLAN.md`

## Source Of Truth

### Retrieval-first 기준선

- Retrieval intent assembly: `apps/api/services/request_facade.py`
- Planner merge / final strategy application: `apps/api/services/planner_service.py`
- Planner contract validation: `apps/core/planner_contract.py`
- Runtime prelude / contract gate: `apps/core/rag_runtime_prelude.py`
- Runtime dispatcher: `apps/core/rag_pipeline.py`
- SEARCH / LOOKUP orchestration: `apps/core/rag_base_orchestration.py`
- JOIN orchestration: `apps/core/rag_join_orchestration.py`
- Canonical evidence normalization: `apps/core/canonical_evidence.py`
- Result assembly / render bridge: `apps/api/services/rag_result_assembly.py`
- Chat UX entrypoints: `apps/api/routes.py`, `apps/api/services/answer_generation.py`, `apps/api/services/answer_merge.py`

### 실행 진입점

- Runtime entry point: `apps/api/main.py`
- App assembly / workflow DI: `apps/api/app_factory.py`
- Workflow graph definition: `apps/api/services/workflow_builder.py`
- Runtime bootstrap / shutdown: `apps/api/runtime.py`

## 문서 역할

- `ARCHITECTURE_RETRIEVAL_FIRST.md`: 시스템 정체성, 레이어 구조, 책임 경계, 왜 이렇게 나뉘는지
- `SYSTEM_FLOW_RETRIEVAL_FIRST.md`: 사용자 질의 이후 단계별 시스템 흐름, artifact handoff, 실행 순서
- `CONTRACT.md`: retrieval contract, runtime enforcement, MUST / MUST NOT / fail-close 기준
- `RUNBOOK.md`: 증상별 triage, observability, 운영 체크리스트
- `MODE_DECISION_GUIDE.md`: SEARCH / LOOKUP / JOIN 결정 규칙 가이드
- `PLANNER_PARAMETER_REFERENCE.md`: 검색 플래너와 실행 전략 파라미터 사전
- `GOLDEN_TESTS.md`: 전략, join/filter, evidence, render invariant 기준 문서
- `ENVIRONMENT.md`: 환경 변수, validation entrypoint, deploy 기본값
- `MAINTENANCE_TASK_MASTER.md`: 유지보수 우선순위와 구조적 debt 관리 문서
- `RETRIEVAL_ROBUSTNESS_PLAN.md`: 검색 대응력 확장 로드맵 reference 문서
- `NTIS_RAG_Search_Strategy_v1_2.md`: 전략 설계 reference 문서

## 문서 업데이트 규칙

- retrieval contract가 바뀌면 `CONTRACT.md`를 갱신합니다.
- 레이어 책임이나 시스템 정체성 설명이 바뀌면 `ARCHITECTURE_RETRIEVAL_FIRST.md`를 갱신합니다.
- 사용자 질의 이후 처리 흐름, 단계 간 handoff, diagram vocabulary가 바뀌면 `SYSTEM_FLOW_RETRIEVAL_FIRST.md`를 갱신합니다.
- mode 결정 규칙이 바뀌면 `MODE_DECISION_GUIDE.md`를 갱신합니다.
- planner/runtime 파라미터 정의나 ownership이 바뀌면 `PLANNER_PARAMETER_REFERENCE.md`를 갱신합니다.
- 운영 triage 순서나 관측 포인트가 바뀌면 `RUNBOOK.md`를 갱신합니다.
- invariant나 golden query 기준이 바뀌면 `GOLDEN_TESTS.md`를 갱신합니다.
- JOIN 실행 메타를 strategy / response에 노출하는 방식이 바뀌면 `CONTRACT.md`, `RUNBOOK.md`, `GOLDEN_TESTS.md`를 함께 갱신합니다.
- 환경 기본값이나 validation 명령이 바뀌면 `ENVIRONMENT.md`를 갱신합니다.
- 설계 참고 문서가 운영 기준선과 충돌하면 `NTIS_RAG_Search_Strategy_v1_2.md`를 함께 정리합니다.

## 문서 작성 원칙

- 모든 문서는 UTF-8 기반 한글로 작성합니다.
- retrieval contract와 chat UX contract를 같은 레이어처럼 섞지 않습니다.
- raw payload, retrieval metadata, prompt view를 같은 개념으로 문서화하지 않습니다.
- `pjt_id`, `pjt_no`, 기관 역할 필드 의미를 임의로 보정하거나 합치지 않습니다.
- `README`는 입구 문서, `ARCHITECTURE`는 구조 문서, `SYSTEM_FLOW`는 순서 문서, `CONTRACT`는 규칙 문서, `RUNBOOK`는 체크리스트 문서 역할을 유지합니다.
- 단계 설명에는 입력, 출력, source of truth, fail-close 지점을 함께 적되 문서 역할과 겹치지 않게 배치합니다.
- 신규 참여자가 코드 없이도 mode 결정과 파라미터 의미를 찾을 수 있게 문서 진입점을 분산시키지 않습니다.
