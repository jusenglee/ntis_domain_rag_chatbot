# Semantic Disambiguation Card

## broad history semantics
- 사람/기관 + 활동이력/활동내역/이력/업적/경력/프로필/현황/참여이력 = broad history query
- 기본은 list이며 detail로 과도 축소하지 않는다.

## detail guards
- perf detail requires explicit perf id
- project detail prefers explicit project id
- generic "성과" alone does not justify perf detail

## relation guards
- `project_perf` / `perf_project` relation은 explicit or recovered anchor가 있을 때만 강화한다.
- anchor가 없으면 relation보다 non-relation broad lookup/list를 우선한다.

## ambiguity policy
- unsure 하면 narrower choice보다 broader choice
- invented id / invented role / invented perf type 금지

## query preservation
- 사람명/기관명을 retrieval_query에서 제거하지 말 것
- broad query를 perf detail exact lookup으로 줄이지 말 것
- validation_hints를 맞추기 위해 질문 의미를 왜곡하지 말 것
