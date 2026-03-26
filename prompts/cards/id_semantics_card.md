# ID Semantics Card

## project exact ids
- `pjt_id` = 과제 단일 시행 인스턴스 exact key
- `pjt_no` = 동일 과제 그룹 key

## perf exact ids
- `rst_id`
- `doi`
- `issn`
- `perf_id`
- `paper_id`
- `patent_reg_no`
- `patent_app_no`

## people / org exact ids
- `person_no`
- `org_id`
- `org_code`
- `biz_no`

## rules
- `ids_map`에는 explicit 또는 recovered seed만 넣는다.
- 이름/기관명만으로 `ids_map`을 발명하지 않는다.
- ambiguous project key는 `candidate_keys.project_key`에 둔다.
- explicit perf id 없으면 perf detail 금지
- explicit project id 없으면 project detail은 recovered anchor 또는 broader lookup을 우선
- `pjt_id`와 `pjt_no`를 서로 치환하지 않는다.
