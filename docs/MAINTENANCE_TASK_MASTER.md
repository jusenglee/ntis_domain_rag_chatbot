# 유지보수 태스크 마스터

이 문서는 retrieval-first 기준선 아래에서 남아 있는 구조적 정리 항목과 우선순위를 기록합니다.

## 문서 역할

- 이 문서는 유지보수 우선순위와 구조적 debt를 관리하는 문서입니다.
- 신규 참여자의 온보딩 문서가 아닙니다.
- 파라미터 정의는 `docs/PLANNER_PARAMETER_REFERENCE.md`, mode 결정 규칙은 `docs/MODE_DECISION_GUIDE.md`를 우선합니다.

## 현재 우선순위

### P0. strict contract 유지

- planner / contract / runtime 사이에 silent correction이나 compat fallback이 다시 유입되지 않도록 막습니다.
- `pjt_id`와 `pjt_no` 의미가 섞이지 않도록 관련 회귀를 계속 유지합니다.

### P1. JOIN runtime observability 정리

- `join_compile_selection`, `hop2_key_strategy`, `resolved_runtime_key_kind`를 단일 vocabulary로 유지합니다.
- Hop2 filter `_meta`를 source of truth로 두고 상위 orchestration은 소비만 하도록 유지합니다.
- JOIN triage 문서와 golden invariant를 코드와 같이 갱신합니다.

### P1. 텍스트 무결성 / UTF-8 위생

- 핵심 코드와 문서에서 mojibake, 깨진 한글, 중복/legacy 설명 문자열을 계속 제거합니다.
- `tests/test_text_integrity.py` 범위는 runtime / contract / 핵심 문서를 우선 포함합니다.

### P2. retrieval robustness 확장

- broad query, 비교형 질의, 복합 조건 질의 대응력은 `docs/RETRIEVAL_ROBUSTNESS_PLAN.md` 기준으로 확장합니다.
- strict planner contract는 유지하고, retrieval policy / reformulation / ranking 계층에서 개선합니다.

## 작업 원칙

- 코드 변경과 문서 변경을 같은 태스크로 묶습니다.
- raw -> canonical -> prompt 흐름의 의미 보존을 우선합니다.
- retrieval correctness가 answer style보다 우선입니다.
- 단계별 시스템 흐름과 artifact handoff는 `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`를 기준으로 유지합니다.
