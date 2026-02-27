# CONTRACT — RAG 실행 계약 (SEARCH / LOOKUP / JOIN)

이 문서는 `server3.py`, `rag_pipeline.py`, `rag_parts/result_contract.py`의 현재 동작을 기준으로 한 운영 계약입니다.

## 1) Planner Output Schema (필수 키/enum)

플래너 출력은 아래 필드를 포함해야 합니다.

- `strategy_version`: 문자열 (예: `v2`)
- `mode`: `SEARCH | LOOKUP | JOIN`
- `head`: `project | perf | people | org | support`
- `action`: `topic | list | detail | stats | download`
- `relation`: `project_perf | perf_project | null`
- `join_key_mode`: `instance | group | null`
- `target_cols`: 배열 (예: `["project", "perf"]`)
- `ids_map`: 객체
- `filters`: 객체 (`lookup_title_filter_policy`, `relation_lookup_enforce` 포함 가능)
- `limit`: 정수
- `retrieval_query`: 문자열
- `confidence`: 숫자

예시(JSON):

```json
{
  "strategy_version": "v2",
  "mode": "JOIN",
  "head": "project",
  "action": "detail",
  "relation": "project_perf",
  "join_key_mode": "instance",
  "target_cols": ["project", "perf"],
  "ids_map": {"pjt_id": ["12345"]},
  "filters": {
    "lookup_title_filter_policy": "soft",
    "relation_lookup_enforce": true
  },
  "limit": 20,
  "retrieval_query": "...",
  "confidence": 0.92
}
```

## 2) Mode별 실행 규칙

### SEARCH
- 목적: 후보 확장/탐색.
- 사람/기관 질의라도 계약 위반 보정 외 임의 모드 전환은 금지.

### LOOKUP
- 목적: ID/명시 조건 기반 정확 조회.
- `detail`이 아닌 LOOKUP에서는 `lookup_title_filter_policy=hard` 입력 시 `soft` 강등 가능.

### JOIN
- 목적: project↔perf 2-hop 관계 조회.
- `mode=JOIN`이면 `join_key_mode`는 필수(`instance|group`).
- `join_key_mode=instance`는 `ids_map.pjt_id` 축 사용.
- `join_key_mode=group`은 `ids_map.pjt_no` 축 사용.
- `group + pjt_no 없음 + pjt_id 존재`인 경우 정규화 단계에서 `instance` 보정 + 경고 로그.

## 3) Fallback 정책 (플래너 계약 위반)

현재 구현 기준:

- 플래너 출력이 계약 위반/불능일 때 fallback 재계획 경로가 존재.
- 기본값: `RAG_PLANNER_INVALID_FALLBACK=1` (활성).
- 즉, 문서 취지의 “fallback 전략 없음”과 달리, 운영 기본값은 fallback 허용 상태입니다.

운영 결론을 내려야 할 항목:

1. 기본값을 `0`으로 바꿔 strict contract로 운영할지
2. `1` 유지 시 허용 조건(예: id_query만)과 감사 로그를 어디까지 강제할지

## 4) Promotion 정책 (SEARCH → LOOKUP/JOIN)

현재 구현 기준:

- `RAG_PROMOTION_MODE`가 활성 모드일 때 검색 결과/신호 기반 승격 재실행 로직이 존재.
- 깊이 제한: `RAG_PROMOTION_MAX_DEPTH`.
- 관련 로그: `RAG.PROMOTION`, `RAG.PLAN_PROMOTED`.

정책 계약으로 명시할 항목:

- 승격 허용 시작 모드(예: SEARCH only)
- 최대 승격 깊이
- 승격 전/후 전략을 trace에 남기는 필수 필드

## 5) Result Contract (빈 결과/최소 rerank)

- 결과 계약 강제 지점: `rag_parts/result_contract.py::enforce_reranked_contract()`.
- 빈 결과/계약 실패 에러코드: `RAG_EMPTY_RESULT_CONTRACT`.
- LOOKUP + 명시 ID 맥락에서는 `RAG_MIN_RERANKED_LOOKUP_ID`(기본 1) 완화 규칙이 적용될 수 있음.
- 최종 반환 정책은 `RAG_FORCE_FALLBACK_CHAT`로 제어:
  - `0`: 계약 실패를 오류로 유지
  - `1`: fallback_chat reason 반환 경로 허용

## 6) intent_payload.v2 송신 계약

- 송신/수신 허용 키는 `normalized_intent` 단일 필드.
- 금지: `query_intent`, `raw_intent`, 기타 임의 키.
- 위반 시 `intent_payload.v2 schema mismatch` 경고 또는 fail-fast(`RAG_FAIL_FAST_SCHEMA`) 처리.

자세한 스키마는 `docs/intent_payload_v2_schema.md`를 단일 기준으로 사용합니다.

## 7) 로그 표준 키

아래 키를 운영 표준으로 고정합니다.

- `RAG.PLAN.INVALID_STRATEGY`
- `RAG.PLAN.FALLBACK_ON_CONTRACT_VIOLATION`
- `RAG.PROMOTION`
- `RAG.PLAN_PROMOTED`
- `RAG.CONTRACT.MIN_RERANKED`
- `RAG_EMPTY_RESULT_CONTRACT`(error_code)
- 서버 응답의 `fallback_context` 포함 여부

## 8) 골든 테스트 갱신 트리거

아래 변경 시 골든 케이스를 반드시 갱신합니다.

- planner fallback on/off (`RAG_PLANNER_INVALID_FALLBACK`)
- promotion on/off (`RAG_PROMOTION_MODE`, `RAG_PROMOTION_MAX_DEPTH`)
- empty result contract on/off + fallback_chat on/off
- rerank 최소치 프리셋/임계값(`RAG_MIN_RERANKED_*`) 변경
