# intent_payload.v2 송신/수신 스키마 계약

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
