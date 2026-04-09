# NTIS Domain Card (Full)

## 1. 시스템 정체성

이 시스템은 NTIS 도메인 전용 retrieval-first RAG 시스템이다.
핵심 엔티티는 project(과제), perf(성과), supports(QNA/MANUAL)다.
플래너는 질의마다 SEARCH / LOOKUP / JOIN 중 하나의 전략을 고정하고, 실행 레이어는 그 전략을 다시 바꾸지 않는다.

## 2. 컬렉션 구조

- `ntis_project_v1` = 과제(project)
- `ntis_perf_v1` = 성과(perf)
- `ntis_supports` = 지원성 정보(supports, QNA / MANUAL)

태그 기준으로 보면:
- project: `IRD_NAI_PJT_INFO`
- perf: `IRD_NAI_RI_PAPER`, `IRD_NAI_RI_IPR`, `IRD_NAI_RI_SW`, `IRD_NAI_RI_NVR`, `IRD_NAI_RI_ORGSM_INFO`, `IRD_NAI_RI_ORGSM_RESOURCE`, `IRD_NAI_RI_COMPOUND`, `IRD_NAI_RI_RSCH_RPT`, `IRD_NAI_RI_FCLT_EQUIP`, `IRD_NAI_RI_TECH_INFO`
- supports: `QNA`, `MANUAL`

## 3. 관계형 전제

NTIS 핵심 데이터는 project와 perf의 관계형 구조를 가진다.
성과(perf)는 독립 텍스트 집합이 아니라 project와 연결되는 파생 엔티티로 해석해야 한다.

중요한 연결 규칙:
- `pjt_id` = 과제 단일 시행 인스턴스 키
- `pjt_no` = 동일 과제의 연도별 시행 인스턴스를 묶는 그룹 키
- project → perf 조회는 `pjt_id` 또는 `pjt_no`를 기준으로 JOIN한다.
- perf 문서에 `pjt_id` / `pjt_no`가 있으면 상위 과제 연결축으로 사용한다.
- `pjt_id`와 `pjt_no`는 절대 같은 의미로 취급하지 않는다.

## 4. 참여연구자 / 기관 정보 전제

NTIS project/perf 데이터에는 문서 내부 객체로 다음 정보가 포함된다.
- 참여인력: `prtcp_mp[]`
- 참여기관: `prtcp_org[]`

즉 연구자/기관 질의는 별도 엔티티 컬렉션을 우선 찾기보다, 문서 내부 객체 필터로 해결하는 것이 기본 원칙이다.

## 5. 기관 의미는 반드시 3종으로 분리한다

기관 질의는 아래 세 가지 의미가 다르다.

- 수행기관(메인 수행기관)
  - 필드: `org_nm`
- 참여기관(공동 참여기관)
  - 필드: `prtcp_org[].org_nm`
- 참여인력 소속기관(사람 affiliation)
  - 필드: `prtcp_mp[].blng_org_nm`

예:
- "ETRI 수행 과제" → `lead_org_name`
- "ETRI 참여 과제" → `participant_org_name`
- "ETRI 소속 연구자 과제" → `people_affiliation_org_name`

## 6. 연구자 의미 해석

연구자 관련 핵심 필드:
- 연구자 이름: `prtcp_mp[].hm_nm`
- 연구자 ID: `prtcp_mp[].hm_id`
- 연구자 소속기관: `prtcp_mp[].blng_org_nm`

예:
- "신동구 연구자 과제" → `participant_researcher_name=[신동구]`
- "신동구(한국과학기술정보연구원) 활동이력" → `participant_researcher_name + people_affiliation_org_name`

## 7. 키 의미

### project exact ids
- `pjt_id` = 과제 단일 시행 인스턴스 exact key
- `pjt_no` = 동일 과제 그룹 key

### perf exact ids
- `rst_id`, `doi`, `issn`, `perf_id`, `paper_id`, `patent_reg_no`, `patent_app_no`

### people / org exact ids
- `person_no`, `org_id`, `org_code`, `biz_no`

규칙:
- `ids_map`에는 explicit 또는 recovered seed만 넣는다.
- 이름/기관명만으로 `ids_map`을 발명하지 않는다.
- explicit perf id가 없으면 perf detail은 금지한다.
- project detail은 explicit project id 또는 recovered anchor가 가장 안전하다.

## 8. 성과(perf) 유형 체계

explicit performance noun / tag 해석:
- 논문 → `IRD_NAI_RI_PAPER`
- 특허 → `IRD_NAI_RI_IPR`
- SW / 소프트웨어 → `IRD_NAI_RI_SW`
- 신품종 → `IRD_NAI_RI_NVR`
- 생명정보 → `IRD_NAI_RI_ORGSM_INFO`
- 생물자원 → `IRD_NAI_RI_ORGSM_RESOURCE`
- 화합물 → `IRD_NAI_RI_COMPOUND`
- 연구보고서 / 보고서 → `IRD_NAI_RI_RSCH_RPT`
- 시설장비 → `IRD_NAI_RI_FCLT_EQUIP`
- 기술요약 → `IRD_NAI_RI_TECH_INFO`

주의:
- "성과"는 broad perf list/topic cue일 수 있지만 perf detail cue는 아니다.
- "활동", "활동이력", "이력", "업적", "프로필", "현황"은 explicit perf type이 아니다.

## 9. 검색 전략 해석

### SEARCH
- 토픽 탐색형
- ID 없음
- broad keyword query
- 누락 방지가 우선
- server-side must 최소화

### LOOKUP
- 정확 조회형
- ID, 사람, 기관, 목록, 통계, 상세 요청 우선
- server-side filter로 정답집합 근처를 강하게 좁힘

### JOIN
- project ↔ perf 2-hop 관계 조회에 한정
- 사람/기관 → 과제/성과는 기본적으로 JOIN보다 LOOKUP 우선

## 10. broad history / detail 해석 가드

broad history semantics:
- 사람/기관 + 활동이력/활동내역/이력/업적/경력/프로필/현황/참여이력 = broad history query
- 기본은 list이며 detail로 과도 축소하지 않는다.

relation guards:
- `project_perf` / `perf_project` relation은 explicit 또는 recovered anchor가 있을 때만 강화한다.
- anchor가 없으면 relation보다 non-relation broad lookup/list를 우선한다.

ambiguity policy:
- unsure 하면 narrower choice보다 broader choice
- invented id / invented role / invented perf type 금지

## 11. field reliability

strong exact anchors:
- `pjt_id`, `pjt_no`, `rst_id`, `doi`, `issn`, `perf_id`, `paper_id`, `patent_reg_no`, `patent_app_no`
- `person_no`, `org_id`, `org_code`, `biz_no`

strong semantic filters:
- `participant_researcher_name`
- `lead_org_name` / `participant_org_name` / `people_affiliation_org_name`
- explicit years
- explicit performance nouns

supporting but weaker:
- `title_text`, `title1` = recall에는 유용하지만 exact detail truth는 아님
- `content_text` = 문서군에 따라 비어 있을 수 있음
- `meta_basic` = 보조 정보

## 12. planner용 핵심 원칙

- `pjt_id`와 `pjt_no`를 섞지 않는다.
- 사람/기관 질의를 무턱대고 JOIN으로 올리지 않는다.
- 수행기관 / 참여기관 / 소속기관을 분리한다.
- explicit perf id 없는 perf detail은 금지한다.
- SEARCH에서는 hard must를 남발하지 않는다.
- LOOKUP/JOIN도 하이브리드 검색을 유지한다.
- must_keep_terms, people_terms, org_terms, years를 retrieval_query 또는 filters에서 잃지 않는다.
