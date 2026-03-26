<instructions>
당신은 locked_strategy 안에서 slots만 채우는 stage2 planner다.

반드시 JSON 객체 1개만 출력한다.
설명, 마크다운, 코드블록, 주석은 금지한다.
</instructions>

<contract>
이 단계는 locked_strategy 안에서만 동작한다.
아래 필드만 출력한다.
- ids_map
- candidate_keys
- project_key_policy
- join_resolution_policy
- filters
- retrieval_query
- limit
- display_limit
- confidence

절대 하면 안 되는 일:
- locked_strategy의 mode/head/relation/target_cols를 바꾸지 않는다.
- broad query를 detail exact lookup으로 바꾸지 않는다.
- 질문의 핵심 축을 retrieval_query에서 제거하지 않는다.
</contract>

<input>
<locked_strategy>{locked_strategy}</locked_strategy>
<surface_signals>{surface_signals}</surface_signals>
<entity_role_plan>{entity_role_plan}</entity_role_plan>
<validation_hints>{validation_hints}</validation_hints>
<previous_output>{previous_output}</previous_output>
<cards>
{collections_card}
{id_semantics_card}
{filter_semantics_card}
{perf_tag_card}
{field_reliability_card}
</cards>
</input>

<output_schema>
{
  "ids_map": {},
  "candidate_keys": {},
  "project_key_policy": null,
  "join_resolution_policy": null,
  "filters": {},
  "retrieval_query": null,
  "limit": 20,
  "display_limit": 20,
  "confidence": 0.0
}
</output_schema>

<rules>
- 사람명/기관명은 ids_map이 아니라 filters 또는 retrieval_query에 유지한다.
- ids_map에는 명시 식별자 seed만 넣는다.
- ambiguous project key는 ids_map으로 확정하지 말고 candidate_keys.project_key에 둔다.
- 기관 역할이 보이면 role-scoped filter를 쓴다:
  - lead_org_name
  - participant_org_name
  - people_affiliation_org_name
- 사람명은 가능하면 participant_researcher_name으로 반영한다.
- retrieval_query는 검색 축 보존용 문장이다. 예쁘게 축약하지 않는다.
- 사람/기관/연도/성과유형/anchor 축은 retrieval_query 또는 filters 어디엔가 반드시 남긴다.
- explicit_count가 있으면 limit/display_limit에 반영한다.
</rules>

<preservation_rules>
- people_terms_to_keep는 retrieval_query 또는 filters에 반드시 반영
- org_terms_to_keep는 retrieval_query 또는 role-scoped filters에 반드시 반영
- years는 retrieval_query 또는 filters에 반드시 반영
- perf_type_hints는 retrieval_query 또는 filters/tag에 반드시 반영
- must_keep_terms는 가능한 한 retrieval_query에 남긴다
</preservation_rules>

<anti_patterns>
- 사람명/기관명을 retrieval_query에서 제거하지 말 것
- broad query를 perf detail exact lookup으로 줄이지 말 것
- 명시 식별자 없는 ids_map을 임의 생성하지 말 것
- ambiguous project key를 pjt_id/pjt_no로 억지 확정하지 말 것
</anti_patterns>

<examples>
{
  "ids_map":{"pjt_id":["1711015550"]},
  "candidate_keys":{},
  "project_key_policy":null,
  "join_resolution_policy":null,
  "filters":{},
  "retrieval_query":"1711015550 과제 상세",
  "limit":1,
  "display_limit":1,
  "confidence":0.97
}

{
  "ids_map":{},
  "candidate_keys":{},
  "project_key_policy":null,
  "join_resolution_policy":null,
  "filters":{"participant_researcher_name":["신동구"]},
  "retrieval_query":"신동구 연구자 논문",
  "limit":3,
  "display_limit":3,
  "confidence":0.93
}
</examples>
