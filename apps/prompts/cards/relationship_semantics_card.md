# Relationship Semantics Card

## 기본 관계
- NTIS 핵심 구조는 `project ↔ perf` 관계형 데이터다.
- 성과(perf)는 project와 무관한 독립 엔티티가 아니라 상위 과제와 연결되는 파생 엔티티로 해석한다.

## 연결 키
- `pjt_id` = 과제 단일 시행 인스턴스 key
- `pjt_no` = 동일 과제의 연도별 시행 인스턴스를 묶는 그룹 key

## JOIN 해석
### project → perf
- 인스턴스 기준: `pjt_id`로 연결
- 그룹 기준: `pjt_no` 또는 hop1에서 확보한 `pjt_id` 목록으로 연결

### perf → project
- explicit perf id로 perf를 확정한 뒤,
- perf 내부 `pjt_id` / `pjt_no`를 project 연결축으로 사용한다.

## 규칙
- `pjt_id`와 `pjt_no`는 절대 같은 의미로 취급하지 않는다.
- 사람/기관 기반 질의는 기본적으로 JOIN보다 문서 내부 객체 필터 기반 LOOKUP이 우선이다.
- explicit / recovered anchor 없이는 relation을 과도하게 강화하지 않는다.
