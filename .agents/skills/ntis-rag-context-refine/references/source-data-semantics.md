# Source Data Semantics

## 1. 목적

이 문서는 NTIS 원천 데이터의 의미 경계를 정리한다.
검색용 필드와 생성용 필드를 분리하고, raw -> canonical -> prompt 변환에서 의미 오염을 막는 것이 목적이다.

## 2. 프로젝트 / 성과 키 의미

### `pjt_id`
- 과제 instance key
- 개별 과제 레코드를 식별한다
- 동일 과제 그룹 내부의 연차/회차를 구분할 때 `pjt_no`와 분리해 써야 한다

### `pjt_no`
- 과제 group key
- 동일 과제 묶음을 연결할 때 사용한다
- `pjt_id`와 같은 값처럼 취급하지 않는다

### `rst_id`
- 성과 instance key
- 논문, 특허, 보고서 등 성과 레코드를 식별한다

## 3. 기관 역할 의미

### `lead_org_name`
- 수행기관
- 실제 project 필드 매핑: `org_nm`, `PJT_PRFRM_ORG_NM`

### `participant_org_name`
- 참여기관
- 실제 project 필드 매핑: `prtcp_org[].org_nm`, `PRTCP_ORG_NM`

### `people_affiliation_org_name`
- 참여인력 소속기관
- 실제 project 필드 매핑: `prtcp_mp[].blng_org_nm`, `BLNG_ORG_NM`

### 역할 규칙
- 세 기관 역할은 서로 다르다
- 모두 기관명이라고 해서 하나의 `org` 필드로 합치지 않는다
- filter와 prompt rendering에서도 역할을 유지한다

## 4. 사람 관련 의미

### `participant_researcher_name`
- 참여인력 이름
- 실제 원천 예시: `prtcp_mp[].hm_nm`, `HM_NM`

### `person_id` / `hm_id`
- 사람 ID
- 사람 이름과 동일하지 않다

### 주의
- 사람 이름은 ids_map에 임의로 넣지 않는다
- 기본적으로 filter term 또는 role slot으로 다룬다

## 5. retrieval view와 prompt view의 차이

### retrieval view
- `title`
- `answer_public` 또는 `content`
- `meta_flat`
- `tags`
- filter key

### prompt view
- identity
- roles
- key facts
- evidence snippet
- provenance

### 규칙
- retrieval view를 prompt에 그대로 넣지 않는다
- retrieval metadata 전체를 LLM에 직접 노출하지 않는다

## 6. Codex가 지켜야 할 규칙

- `pjt_id != pjt_no`
- `lead_org_name != participant_org_name != people_affiliation_org_name`
- 사람/기관 이름은 기본적으로 filter term이며 ID가 아니다
- retrieval view != prompt view
- raw payload dump 금지
- `output_type`별 context 구성이 달라야 한다

