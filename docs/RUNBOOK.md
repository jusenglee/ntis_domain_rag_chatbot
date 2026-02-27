# RAG 운영 RUNBOOK

> 운영 진입점 단일화: 상세 계약/추적 문서는 본 RUNBOOK를 기준으로 이동합니다.

## 관련 상세 문서

- 프로젝트 키 ENV 계약: [`project_key_env_contract.md`](./project_key_env_contract.md)
- intent payload v2 스키마: [`intent_payload_v2_schema.md`](./intent_payload_v2_schema.md)
- vLLM request_id 추적: [`vllm_request_id_tracking.md`](./vllm_request_id_tracking.md)

## ENV 운영 기준표

| 분류 | ENV | 기본값 | 허용값 | 영향 범위 | 대표 로그 키 |
| --- | --- | --- | --- | --- | --- |
| 계약/스키마 | `RAG_FAIL_FAST_SCHEMA` | `0` | `1/true/yes/y`(엄격), 그 외(경고 후 진행) | `intent_payload.v2` 수신 스키마 위반 시 에러 승격 여부 | `[RAG] intent_payload.v2 schema mismatch: ...` |
| LOOKUP 정책 | `RAG_LOOKUP_FILTER_POLICY` | `hard` | `hard`, `off`, `must_one_then_should` (그 외 `hard`로 폴백) | LOOKUP 모드 server-side 필터 강도(필터 적용 여부/방식) | `RAG.STRATEGY.FILTER` |
| LOOKUP 정책 | `RAG_LOOKUP_TITLE_FILTER_POLICY` | `soft` | `soft`, `hard` (그 외 `soft`로 폴백, detail 이외 요청의 `hard`는 강제 `soft`) | LOOKUP title_terms 적용 방식(`must` vs soft rerank/post-filter) | `RAG.LOOKUP.TITLE_FILTER_POLICY` |
| 결과/계약 | `RAG_MIN_RERANKED_LOOKUP_ID` | `1` | `0` 이상의 정수 | LOOKUP + 명시적 ID 질의(`pjt_id/pjt_no`)의 최소 reranked 건수 clamp | `RAG.CONTRACT.MIN_RERANKED`, `info.contract_effective_min_reranked` |
| 검색/랭킹 | `RAG_SEARCH_FILTER_MIN_CONF` | `0.6` | 실수(float) | SEARCH 모드에서 planner confidence 기반 필터 활성화 임계치 | `RAG.STRATEGY.FILTER` |
| 검색/랭킹 | `RAG_TITLE_POST_FILTER_TOPN` | `80` | `1` 이상의 정수 | `lookup + title soft` 경로에서 title post-filter 후보군 크기 | `RAG.TITLE_POST_FILTER` |
| 검색/랭킹 | `RAG_TITLE_SOFT_BOOST` | `8.0` | 실수(float) | title soft match 점수 가중치(rerank 가산점) | `RAG.TITLE_POST_FILTER` |
| 키 매핑 | `RAG_KEY_PJT_ID` | `pjt_id` | 비어있지 않은 문자열 (`RAG_KEY_PJT_NO`와 동일값 금지) | 프로젝트 인스턴스 ID 필드 매핑(필터/JOIN/LOOKUP) | `[startup][key-mapping]` |
| 키 매핑 | `RAG_KEY_PJT_NO` | `pjt_no` | 비어있지 않은 문자열 (`RAG_KEY_PJT_ID`와 동일값 금지) | 프로젝트 번호 그룹 키 필드 매핑(필터/JOIN/LOOKUP) | `[startup][key-mapping]` |

## `RAG_EMPTY_RESULT_CONTRACT` 운영 대응 절차

### 1) 사용자 메시지 변환 조건

아래 조건을 모두 만족하면 계약 오류를 사용자 친화 메시지로 변환합니다.

- 오류 코드가 `RAG_EMPTY_RESULT_CONTRACT`
- 질의 모드가 `LOOKUP`
- 아래 중 하나 충족
  - `action == detail`
  - `ids_map`에 명시적 ID(`pjt_id/pjt_no` 등)가 1개 이상 존재

변환 메시지:

`요청하신 식별자(ID)에 해당하는 상세 정보를 찾지 못했습니다. ID를 다시 확인해 주세요.`

그 외 계약 오류는 일반 전략 오류 메시지로 응답합니다.

### 2) 운영자 확인 포인트

1. `question_analysis.mode/action/ids_map`가 위 변환 조건에 맞는지 확인
2. `RAG.CONTRACT.MIN_RERANKED` 로그에서 `effective_min_reranked`, `clamp_reason=lookup_id_query` 여부 확인
3. `info.contract_fail_reason`(예: `insufficient_hits`)으로 근본 원인 분류
4. 키 매핑 배포 이슈 여부 확인: `[startup][key-mapping]`

### 3) 1차 조치 가이드

- ID 오타 가능성이 높으면 사용자에게 ID 재확인 요청
- 대량 누락/반복 발생 시 아래를 순서대로 점검
  1. `RAG_KEY_PJT_ID`, `RAG_KEY_PJT_NO` 매핑 충돌 여부
  2. 인덱스 측 ID 필드 적재 상태
  3. `RAG_MIN_RERANKED_LOOKUP_ID` 과도 설정 여부
