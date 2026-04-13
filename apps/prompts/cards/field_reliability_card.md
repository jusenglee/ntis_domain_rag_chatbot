# Field Reliability Card

## strong exact anchors
- `pjt_id`, `pjt_no`, `rst_id`, `doi`, `issn`, `perf_id`, `paper_id`, `patent_reg_no`, `patent_app_no`
- `person_no`, `org_id`, `org_code`, `biz_no`

## strong semantic filters
- `participant_researcher_name`
- `lead_org_name`
- `participant_org_name`
- `people_affiliation_org_name`
- explicit years
- explicit performance nouns

## supporting but weaker
- `title_text`, `title1` = recall에는 유용하지만 exact detail truth는 아님
- `content_text` = 문서군에 따라 비어 있을 수 있음
- `meta_basic` = 보조 정보
- `flat_text`, `keyword_text` = recall 보조 신호

## guidance
- broad people/org history query에서는 inferred perf tag보다 이름/기관/역할 필터가 더 신뢰할 수 있다.
- detail exact lookup에서는 title similarity보다 exact id / recovered anchor를 우선한다.
