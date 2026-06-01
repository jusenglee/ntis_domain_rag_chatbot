# Filter Semantics Card

## 기관 역할 필터
- `lead_org_name` = 수행기관(`org_nm`)
- `participant_org_name` = 참여기관(`prtcp_org[].org_nm`)
- `people_affiliation_org_name` = 참여인력 소속기관(`prtcp_mp[].blng_org_nm`)

## 연구자 필터
- `participant_researcher_name` = 참여인력 이름(`prtcp_mp[].hm_nm`)
- `participant_researcher_id` = 참여인력 ID(`prtcp_mp[].hm_id`)

## 해석 규칙
- 사용자가 "수행" / "주관"을 말하면 `lead_org_name`
- 사용자가 "참여" / "공동" / "컨소시엄"을 말하면 `participant_org_name`
- 사용자가 "~소속 연구자"를 말하면 `people_affiliation_org_name`
- Stage 1.5가 확정 개인명으로 보존한 사람 이름이 나오면 `participant_researcher_name` 우선
- "신동구(한국과학기술정보연구원)"처럼 연구자+소속기관이면 `participant_researcher_name + people_affiliation_org_name` 조합을 우선
- 전문가, 박사급, 분야 전문가, 수행 경험자, 추천 후보 같은 역할/이력 표현은 사람 이름이 아니므로 `participant_researcher_name`에 넣지 않음
- 기술/산업/정책/이력 단어는 사람명 필터가 아니라 `retrieval_query` 의미 축으로 보존

## preservation
- people_terms_to_keep가 비어 있지 않으면 filters 또는 retrieval_query에 반드시 반영
- people_terms_to_keep가 비어 있으면 사람명 필터를 만들지 않음
- org_terms_to_keep는 role-scoped filters 또는 retrieval_query에 반드시 반영
- explicit years는 filters 또는 retrieval_query에 반드시 반영
