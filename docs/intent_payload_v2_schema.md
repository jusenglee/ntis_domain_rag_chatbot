# intent_payload.v2 스키마

이 문서는 RAG executor에 전달되는 최소 payload를 정의합니다.

## Sender

현재 sender 경로:
- `apps/api/services/request_facade.py`
- runtime wiring: `apps/api/app_factory.py`

Sender는 다음 형태만 전달할 수 있습니다.

```json
{
  "normalized_intent": {
    "action": "topic"
  }
}
```

## Receiver

현재 receiver 경로:
- `apps/core/rag_pipeline.py`

Receiver 기대사항:
- input type: `Mapping | dict | None`
- 허용 top-level key: `normalized_intent`
- `query_intent`, `raw_intent`, `planner_applied`, `planner_failed`, `question_analysis` 같은 legacy key는 `intent_payload.v2`에 포함되지 않습니다.

## 규칙

Sender/receiver 경계는 최소 형태로 유지합니다. 새 필드가 필요하면 다음을 함께 갱신합니다.

1. sender construction
2. receiver validation
3. contract docs
4. tests

