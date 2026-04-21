# API 응답 명세

이 문서는 현재 `apps/api/routes.py` 기준의 HTTP/SSE 응답 계약만 정리한다.
내부 실행 계약(planner, retrieval, follow-up, canonical evidence)은 `02_실행계약과_전략규칙.md`를 참고한다.

## Source of Truth

- route surface: `apps/api/routes.py`
- stream envelope: `apps/api/streaming/contracts.py`, `apps/api/streaming/sse_encoder.py`
- 함께 읽을 문서: `03_운영과_환경.md`, `02_실행계약과_전략규칙.md`

---

## 목차

1. [Source of Truth](#source-of-truth)
2. [엔드포인트 요약](#엔드포인트-요약)
3. [공통 요청 바디](#공통-요청-바디query)
4. [/query/stream](#querystream)
5. [/query/debug](#querydebug)
6. [/health](#health)
7. [/metrics](#metrics)
8. [/metrics/stream](#metricsstream)
9. [공통 오류 응답](#공통-오류-응답)
10. [유지 규칙](#유지-규칙)

---

## 상세 기준
- `/query/stream` event contract: `apps/api/streaming/contracts.py`, `apps/api/streaming/sse_encoder.py`
- `/metrics` payload: `apps/platform/metrics.py`
- debug strategy 직렬화: `apps/platform/schemas.py`

---

## 엔드포인트 요약

| 엔드포인트 | 메서드 | 응답 타입 | 비고 |
|---|---|---|---|
| `/query/stream` | `POST` | `text/event-stream` | 주 질의 스트림 API |
| `/query/debug` | `POST` | `application/json` | `ENABLE_DEBUG_ROUTES` 활성 시만 사용 |
| `/health` | `GET` | `application/json` | ready 여부에 따라 `200/503` |
| `/health/details` | `GET` | `application/json` | 항상 `200` |
| `/metrics` | `GET` | `application/json` | Prometheus snapshot |
| `/metrics/stream` | `GET` | `text/event-stream` | metrics SSE. `/query/stream`과 포맷이 다름 |

---

## 공통 요청 바디(`/query/*`)

```json
{
  "question": "질문 문자열",
  "conversation_id": "optional-conversation-id",
  "Temperature": 0.2,
  "Top-P": 0.9,
  "Max-Token": 512,
  "Top-K": 20,
  "RAG_MIN_DENSE_SCORE": 0.2,
  "RAG_TOPK_DENSE": 50,
  "RAG_W_LEX": 0.4,
  "RAG_TOPK_LEX_CAND": 100
}
```

- `question`만 필수다.
- override 필드는 route에서 먼저 범위 검증한다.
- 잘못된 값은 `422`와 FastAPI `HTTPException.detail` 문자열로 반환한다.

---

## `/query/stream`

### 전송 규칙
- media type: `text/event-stream`
- canonical envelope: 모든 frame은 `data: {"tag":"event","event":...}` 형태다.
- route-level reasoning chunk는 내보내지 않는다. 사용자 가시 텍스트만 `answer.chunk`로 보낸다.
- `answer.chunk`는 요청 진행 중의 provisional stream이다. 클라이언트는 같은 UI row에 대해 최초 수신한 `request_id`와 다른 `answer.chunk`/`answer.final`/`reference.set`/`done`을 반영하면 안 된다.
- `answer.final`은 LLM 스트림 본문을 임의로 덮어쓰기 위한 이벤트가 아니다. 정합성 보완이 필요하면 `meta.verified_projection_summary`를 별도 보완 레이어로 표시한다.
- `reference.set`과 `done`은 정상/오류/강등(degraded) 종료 모두에서 내려보내는 것이 원칙이다.

### 공통 SSE envelope

```json
{
  "tag": "event",
  "event": {
    "kind": "answer.final",
    "request_id": "cid-1234-abcd1234",
    "seq": 4,
    "model_key": "solar",
    "content": "최종 답변",
    "references": null,
    "meta": {}
  }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `tag` | string | 항상 `"event"` |
| `event.kind` | string | 아래 event kind 표 참고 |
| `event.request_id` | string | route가 생성한 request id |
| `event.seq` | integer | route 기준 순번 |
| `event.model_key` | string \| null | `answer.chunk`, `answer.final` 등 모델 종속 event에서만 사용 |
| `event.content` | string \| null | 사용자 가시 텍스트 |
| `event.references` | array \| null | `reference.set`에서 사용하며, 다른 event에서는 `null` |
| `event.meta` | object | event별 추가 payload |

### event kind

| kind | content | references | meta |
|---|---|---|---|
| `conversation` | `conversation_id` | `null` | `{ "conversation_id": "..." }` |
| `status` | 없음 | `null` | 현재 구현은 `{ "status": "retrieve" }` |
| `answer.chunk` | 부분 답변 텍스트 | `null` | `{}` |
| `clarification` | clarification 메시지 | `null` | `{ "clarification": ClarificationPayload }` |
| `answer.final` | 최종 사용자 답변 | `null` | `AnswerFinalMeta` |
| `reference.set` | `"null"` | `ReferenceItem[]` | `{ "references": ReferenceItem[] }` |
| `error` | 없음 | `null` | `{ "error": "...", "error_code": "...", "reason": "..." }` |
| `done` | 없음 | `null` | `{}` 또는 `{ "error": true }` 또는 `{ "degraded": true }` |

### 정상 종료 순서

1. `conversation`
2. `status`
3. `answer.chunk` 0회 이상
4. `clarification` 또는 `answer.final`
5. `reference.set`
6. `done`

### 예외 종료 규칙

- graph 미준비:
  - `error` -> `reference.set` -> `done`
- 전략 위반을 사용자 메시지로 강등한 경우:
  - `answer.final` -> `reference.set` -> `done`
  - 이 경로는 `error` event 대신 사용자 가시 fallback 문장을 반환한다.
- 내부 예외:
  - `error` -> `reference.set` -> `done`
- 최종 답변이 비어 있는 비정상 종료:
  - route guard가 `answer.final`을 강제로 생성한다.
  - `meta.error_code="MISSING_FINAL_ANSWER"`가 포함된다.

### `ClarificationPayload`

```json
{
  "clarification_type": "followup_reference",
  "message": "어느 항목을 말씀하시는지 한 번 더 지정해 주세요.",
  "candidates": [],
  "resume_token": {}
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `clarification_type` | string | clarification 분류 |
| `message` | string | 사용자에게 그대로 보일 메시지 |
| `candidates` | array | 재선택 후보 목록 |
| `resume_token` | object | 후속 재개용 토큰 |

### `AnswerFinalMeta`

`answer.final.meta`는 `AnswerArtifact.to_meta_dict()` 결과를 기준으로 한다.
route 문서에서 안정적으로 기대해도 되는 필드는 아래와 같다.

| 필드 | 타입 | 조건 | 설명 |
|---|---|---|---|
| `answer_kind` | string | 항상 | `llm_streamed`, `llm_collected`, `detail_cache`, `detail_profile`, `clarification`, `no_result`, `direct_answer`, `error` |
| `user_visible_final_required` | boolean | 항상 | 최종 사용자 노출용 terminal message 여부 |
| `answer_source` | string | 대부분 | 선택된 답변 소스 |
| `model_key` | string | 모델 기반 최종답변 | 보통 `solar` 또는 `gemma` |
| `selection_reason` | string | dual-model 병합 경로 | 최종 답변 선택 사유 |
| `groundedness_status` | string | 병합 경로 | groundedness verdict |
| `groundedness_reason_codes` | array | 병합 경로 | groundedness 보조 코드 |
| `answer_state_consistency` | object | list-family 검증 경로 | state consistency verdict 전체 |
| `answer_state_consistency_status` | string | list-family 검증 경로 | state consistency 상태 |
| `answer_state_consistency_reason_codes` | array | list-family 검증 경로 | state consistency 보조 코드 |
| `verified_projection_summary` | object | list-family 보완 경로 | LLM 본문을 교체하지 않고 보완 UI에 표시할 검증된 projection 요약. `text`, `groundedness`, `state_consistency`를 포함한다. |
| `answer_augmentation_mode` | string | list-family 보완 경로 | 현재는 `verified_projection_summary`. LLM 본문 유지 후 검증 데이터 레이어를 보강했음을 나타낸다. |
| `visible_answer_manifest_status` | string | 병합 경로 | `approved`, `withheld_partial`, `blocked_*`, `not_applicable` |
| `visible_answer_manifest` | object | publishable list-family | 다음 turn의 ordinal/source follow-up truth |
| `visible_answer_manifest_publication` | object | list-family publication | answer-owned publication artifact; `approved` contains `published_manifest`, blocked/withheld statuses must not fall back to stale `view_state.visible_answer_manifest` |
| `error_code` | string | guard/degraded error 경로 | route 또는 strategy 위반 코드 |
| `reason` | string | guard/degraded error 경로 | 오류 설명 |
| `degraded` | boolean | degraded 경로 | 사용자 메시지로 강등된 종료 여부 |

- 추가 stream metric 필드(`elapsed_ms`, `ttft_any_ms`, `ttft_content_ms`, `content_chars` 등)는 모델 실행 경로에 따라 더 붙을 수 있다.
- 문서상 안정 계약은 위 표의 공통 필드까지로 본다.

### `ReferenceItem`

```json
{
  "tag": "IRD_NAI_PJT_INFO",
  "id": "PJT-2024-0001",
  "title": "과제 제목"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `tag` | string | `DataTag` enum 값 |
| `id` | string \| null | source semantics를 보존한 canonical id |
| `title` | string \| null | 사용자 표시 제목 |

- route는 reference를 `selected_answer_artifact.references -> canonical_evidence -> context hit` 순서로 복구한다.
- invalid reference payload는 fail-open으로 드롭되며, route 전체를 실패시키지 않는다.
- project reference는 `pjt_id` 축을 우선하고, performance 계열은 `rst_id/perf_id/paper_id` 축을 우선한다.

---

## `/query/debug`

### 성공 응답

```json
{
  "success": true,
  "conversation_id": "cid-1234",
  "answer_gemma": "모델 원본 답변",
  "answer_solar": "모델 원본 답변",
  "output_message": "최종 사용자 답변",
  "final_answer_meta": {},
  "question_analysis": {},
  "strategy_summary": {},
  "knowledge_sufficiency": {},
  "documents_used": 3,
  "latencies": {},
  "total_time": 1234.5,
  "processing_strategy": "high",
  "clarification": null,
  "merge_debug": {},
  "selected_answer_meta": {}
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `success` | boolean | 성공 시 항상 `true` |
| `conversation_id` | string | conversation 식별자 |
| `answer_gemma` | string \| null | Gemma 모델 원본 답변 |
| `answer_solar` | string \| null | Solar 모델 원본 답변 |
| `output_message` | string | 최종 사용자 답변 |
| `final_answer_meta` | object \| null | `answer.final.meta`와 같은 계열의 최종 메타 |
| `question_analysis` | object \| null | assembled planner contract |
| `strategy_summary` | object | `StrategySpec` 직렬화 결과 |
| `knowledge_sufficiency` | object \| null | retrieval 필요성 판단 결과 |
| `documents_used` | integer | context 문서 수 |
| `latencies` | object | workflow latency map |
| `total_time` | number | 총 처리 시간(ms) |
| `processing_strategy` | string | 현재는 `knowledge_sufficiency.requires_new_knowledge`의 축약 표현 |
| `clarification` | object \| null | clarification payload |
| `merge_debug` | object | answer merge 진단 정보 |
| `selected_answer_meta` | object | 최종 선택 답변의 meta |

### 실패 응답

```json
{
  "success": false,
  "error": "runtime_not_ready",
  "error_code": "RUNTIME_NOT_READY",
  "reason": "compiled graph unavailable"
}
```

- debug route가 비활성화되면 `404 {"detail":"not found"}`를 반환한다.
- debug route가 활성화돼 있어도 graph가 없거나 실행 중 예외가 나면 본문은 위 shape의 `success=false` JSON이다.
- 내부 예외 시 추가 `contract_failure_details` 필드가 붙을 수 있다.

---

## `/health`

### 응답 본문

```json
{
  "status": "healthy",
  "ready": true,
  "memory_backend": "redis",
  "memory_backend_effective": "RedisKVStore",
  "kv": "connected",
  "graph": "compiled",
  "metrics": "ready"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `status` | string | `healthy`, `degraded`, `not_ready` |
| `ready` | boolean | graph + KV ping 기준 최종 ready |
| `memory_backend` | string | 논리적 backend 이름. 현재 구현은 `"redis"` 고정 |
| `memory_backend_effective` | string | 실제 store 클래스명 또는 `"none"` |
| `kv` | string | `connected` 또는 `disconnected` |
| `graph` | string | `compiled` 또는 `not_ready` |
| `metrics` | string | `ready` 또는 `not_ready` |

- `/health`는 `ready=true`일 때 `200`, 아니면 `503`을 반환한다.
- `/health/details`는 같은 payload를 항상 `200`으로 반환한다.

---

## `/metrics`

### 성공 응답

```json
{
  "requestCount": 2.0,
  "gpuUtilPercent": 0.0
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `requestCount` | number \| null | 진행 중 vLLM 요청 수. 수집 실패 시 `null` 가능 |
| `gpuUtilPercent` | number | GPU 평균 활용률. empty vector나 GPU 수집 실패 시 `0.0`으로 강등 |

### 실패 응답

```json
{
  "error": "runtime_not_ready",
  "error_code": "RUNTIME_NOT_READY",
  "reason": "metrics client unavailable"
}
```

- metrics client가 없으면 `503`을 반환한다.

---

## `/metrics/stream`

- media type: `text/event-stream`
- `/query/stream`과 달리 canonical `tag="event"` envelope를 쓰지 않는다.
- SSE event name은 `metrics`다.

### 정상 frame

```text
event: metrics
data: {"requestCount":2.0,"gpuUtilPercent":0.0}
```

### 미준비 frame

```text
event: error
data: {"error":"runtime_not_ready","error_code":"RUNTIME_NOT_READY"}
```

- 연결 시작 시 `retry:`와 padding comment frame을 먼저 보낼 수 있다.

---

## 공통 오류 응답

### 422 override validation

다음 조건을 위반하면 FastAPI `HTTPException`이 그대로 나간다.

| 필드 | 조건 | 예시 detail |
|---|---|---|
| `Temperature` | `>= 0` | `"Temperature must be >= 0"` |
| `Top-P` | `0 < value <= 1` | `"Top-P must be > 0 and <= 1"` |
| `Max-Token` | `>= 1` | `"Max-Token must be >= 1"` |
| `Top-K` | `>= 1` | `"Top-K must be >= 1"` |

### 404 debug route disabled

```json
{
  "detail": "not found"
}
```

---

## 유지 규칙

- `/query/stream`은 canonical `tag="event"` envelope 하나만 유지한다.
- retrieval metadata 전체를 route 응답에 직접 dump하지 않는다.
- reference는 `tag/id/title`의 최소 canonical payload로만 노출한다.
- `pjt_id`와 `pjt_no` 의미는 reference id 선택에서도 섞지 않는다.
