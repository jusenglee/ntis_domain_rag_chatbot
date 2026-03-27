# CODEX_CONTEXT.md

## Source of truth at bootstrap
- repo: `ntis_domain_rag_chatbot`
- branch: `고도화`

## What this system is
NTIS 도메인 RAG 시스템이며, retrieval strategy와 answer generation을 엄격하게 분리한다.
핵심 설계는 SEARCH / LOOKUP / JOIN의 역할 분리와 planner의 단일·불변 strategy contract다.

## Immutable retrieval contract
### 1) Planner contract
- planner는 질의마다 단 하나의 strategy를 출력한다.
- executor는 그 strategy를 재해석하지 않는다.
- 허용되는 것은 strategy compile 뿐이다.
  - filters -> backend filter 변환
  - topK 적용
  - rerank 적용

### 2) SEARCH
- 목적: 탐색형, 누락 방지
- 원칙: server-side must 금지
- 사람/기관/성과유형은 soft bonus 또는 rerank bonus로만 반영
- 사람 이름이 must로 들어가면 설계 위반

### 3) LOOKUP
- 목적: 정확 조회
- 목록 / 통계 / 상세 / ID 기반 / 사람/기관 기반 질문 우선
- 사람/기관 이름은 should + min_should=1 게이트
- ID가 있으면 must

### 4) JOIN
- 목적: project <-> perf 2-hop
- 사람/기관 -> 과제/성과는 기본 JOIN 아님
- instance join은 `pjt_id`
- group join은 `pjt_no`
- `pjt_id`와 `pjt_no` 혼합 금지

## Domain keys
### Project keys
- `pjt_id`: 단일 시행 인스턴스 키
- `pjt_no`: 그룹 키

### People / org filters
- `lead_org_name` -> `org_nm`
- `participant_org_name` -> `prtcp_org[].org_nm`
- `people_affiliation_org_name` -> `prtcp_mp[].blng_org_nm`
- `participant_researcher_name` -> `prtcp_mp[].hm_nm`
- `participant_researcher_id` -> `prtcp_mp[].hm_id`

## Collections
- `ntis_project_v1`: 과제
- `ntis_perf_v1`: 논문/특허/SW/신품종/생명정보/생물자원/화합물/연구보고서/시설장비/기술요약
- `ntis_supports`: QNA / MANUAL

모든 데이터에는 `prtcp_mp`, `prtcp_org` 객체가 존재한다.

## Operational defaults
기본 운영은 아래 전제를 유지한다.
- `RAG_STRICT_STRATEGY_CONSISTENCY=1`
- `RAG_LOOKUP_FILTER_POLICY=hard`
- `RAG_LOOKUP_TITLE_FILTER_POLICY=soft`
- `RAG_RELATION_LOOKUP_POLICY=filter`
- `RAG_PROMOTION_MODE=disable`
- `RAG_FORCE_FALLBACK_CHAT=0`

## Hard bans
- fallback chat로 전략 위반 감추기
- JOIN인데 group/instance 혼합
- LOOKUP/JOIN인데 BM25-only 우회
- project top-level `pjt_id` 누락을 무시하고 진행
- answer stage에서 planner 결과 재분류

## What Codex should care about most
1. strategy drift
2. answer groundedness
3. verifier strength
4. streaming reliability
5. regression / golden tests
6. docs/prompt/code drift

## Known risk hint at bootstrap
현재 위험 메모상 스트리밍 쪽에서 아래 가능성이 큼.
- async stream close await 누락
- chunk는 오는데 emitted chunk로 인정 못하는 분기
- ttft deadline이 parser/condition 문제로 발동

이 문서는 repo를 직접 더 읽은 뒤 계속 갱신해야 한다.
