# intent_payload.v2 송신/수신 스키마 계약

> 운영 RUNBOOK 진입점: [`RUNBOOK.md`](./RUNBOOK.md)


`run_rag_ab_compare(..., intent_payload=...)`에서 사용하는 `intent_payload.v2` 계약은 아래처럼 **단일 필드만 허용**합니다.

## Sender (server3.py)
- 타입: `dict | None`
- 허용 키: `normalized_intent` (필수)
- 금지 키(예): `keywords`, `planner_applied`, `planner_failed`, `query_intent`, `question_analysis`, 기타 임의 키

예시:
```json
{
  "normalized_intent": { "action": "topic" }
}
```

## Receiver (rag_pipeline.py)
- `Mapping` 입력 시 허용 키 집합은 `{"normalized_intent"}`만 인정
- 허용되지 않은 키가 있으면 `intent_payload.v2 schema mismatch` 경고(또는 fail-fast)
- `query_intent/raw_intent` legacy 필드는 더 이상 지원하지 않음

## 목표
- 송신/수신 스키마를 단일화하여 `intent_payload.v2 schema mismatch` 경고를 제거


## 랭킹 집계 정책 필드 (normalized_intent 내부)
- `stats_metric` (기본: `project_participation_count`)
- `window_years` (기본: 최근 `3`년)
- `candidate_n` (기본: `50`)
- `top_k` (기본: `1`, 요청값 존재 시 해당 값 사용)
- `tie_break` (기본: `performance_count_desc_name_asc`)

해당 필드는 `action=stats` + `wants_rank=true` 시 집계 실행 단계에서 사용되며, 최종 aggregation `meta.stats.*`에 적용값이 기록됩니다.
