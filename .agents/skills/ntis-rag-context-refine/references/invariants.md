# Invariants

## 의미 불변 조건

- `pjt_id`와 `pjt_no`는 서로 다른 값이다.
- `pjt_id`는 과제 instance key이고 `pjt_no`는 과제 group key다.
- `lead_org_name`, `participant_org_name`, `people_affiliation_org_name`은 서로 다른 역할 필드다.
- `participant_researcher_name`은 기본적으로 사람 이름 역할 정보이며 arbitrary id로 취급하지 않는다.

## 실행 불변 조건

- planner는 질의마다 하나의 strategy를 확정한다.
- executor는 확정된 strategy를 재해석하거나 재결정하지 않는다.
- `SEARCH`, `LOOKUP`, `JOIN`의 계약 경계는 실행 레이어에서 바뀌지 않는다.

## 렌더링 불변 조건

- retrieval view와 prompt view는 같지 않다.
- prompt context는 canonical schema를 거쳐 renderer에서 조립한다.
- `output_type`이 renderer fieldset을 결정한다.
- `summary`, `detail`, `list`, `stats`, `relation`은 같은 context shape를 재사용하지 않는다.

## 변경 관리 불변 조건

- golden test는 정답 문장보다 계약과 invariant를 우선 검증한다.
- planner, contract, filter, canonical mapping, renderer fieldset이 바뀌면 관련 문서를 같이 갱신한다.
- baseline check 상태를 모른 채 작업을 끝내지 않는다.

