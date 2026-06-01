# API 응답 명세

이 문서는 현재 `apps/api/routes.py` 기준의 HTTP/SSE 응답 계약만 정리한다.
내부 실행 계약(planner, retrieval, follow-up, canonical evidence)은 `02_CONTRACTS_AND_RULES.md`를 참고한다.

## Source of Truth

- route surface: `apps/api/routes.py`
- stream envelope: `apps/api/streaming/contracts.py`, `apps/api/streaming/sse_encoder.py`
- 함께 읽을 문서: `06_운영과_환경.md`, `02_CONTRACTS_AND_RULES.md`

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
9. [Contract-invalid 및 provider failure](#contract-invalid-및-provider-failure)
10. [공통 오류 응답](#공통-오류-응답)
11. [유지 규칙](#유지-규칙)

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
- `answer.chunk`는 요청 진행 중의 provisional stream이다. 클라이언트는 같은 UI row에 대해 최초 수신한 `request_id`와 다른 `answer.chunk`/`reference.set`/`done`을 반영하면 안 된다.
- 최종 사용자 가시 본문은 `answer.chunk`로만 전달한다. 정합성 보완이 필요하면 `done.meta.verified_projection_summary`를 별도 보완 레이어로 표시한다.
- `direct_answer`, `clarification`, `no_result`, `error`처럼 deterministic terminal 문장이 있는 경로는 같은 문장을 `answer.chunk(event.model_key="solar")`, `answer.chunk(event.model_key="gemma")` 순서로 먼저 보낸 뒤 `reference.set`과 `done`을 보낸다.
- `reference.set`과 `done`은 정상/오류/강등(degraded) 종료 모두에서 내려보내는 것이 원칙이다.
- `done`은 단순 종료 신호가 아니라 terminal metadata carrier다. clarification, no-result, degraded/error, publication guard 결과는 `done.meta`에 실린다.
- `done`은 모든 종료 사례에서 `event.model_key="solar"`와 `event.model_key="gemma"` 두 프레임으로 fan-out한다. 두 프레임의 `meta`는 동일하며, 프론트엔드는 각 모델 패널에 같은 terminal state를 반영한다.
- `contract-invalid` 상태에서는 LLM 생성 `answer.chunk`를 시작하지 않는다. route는 deterministic terminal message를 양쪽 모델 패널용 `answer.chunk`로 먼저 내보내고, 같은 정보를 `done.meta.output_message`와 `done.meta.clarification`에도 싣는다.
- 모델 provider가 reasoning만 내보내고 content를 만들지 못하면 provider failure로 간주한다. 이 상태는 사용자 모호성 clarification이 아니다.

### 공통 SSE envelope

```json
{
  "tag": "event",
  "event": {
    "kind": "done",
    "request_id": "cid-1234-abcd1234",
    "seq": 4,
    "model_key": "solar",
    "content": null,
    "references": null,
    "meta": {
      "answer_kind": "llm_collected"
    }
  }
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `tag` | string | 항상 `"event"` |
| `event.kind` | string | 아래 event kind 표 참고 |
| `event.request_id` | string | route가 생성한 request id |
| `event.seq` | integer | route 기준 순번 |
| `event.model_key` | string \| null | `answer.chunk`는 실제 모델 키, `done`은 항상 `"solar"`/`"gemma"` fan-out 키. `conversation`, `status`, `reference.set`은 `null` |
| `event.content` | string \| null | 사용자 가시 텍스트 |
| `event.references` | array \| null | `reference.set`에서 사용하며, 다른 event에서는 `null` |
| `event.meta` | object | event별 추가 payload |

### event kind

| kind | content | references | meta |
|---|---|---|---|
| `conversation` | `conversation_id` | `null` | `{ "conversation_id": "..." }` |
| `status` | 없음 | `null` | 현재 구현은 `{ "status": "retrieve" }` |
| `answer.chunk` | 부분 답변 텍스트 | `null` | `{}` |
| `reference.set` | `"null"` | `ReferenceItem[]` | `{ "references": ReferenceItem[] }` |
| `done` | 없음 | `null` | `TerminalDoneMeta` |

### 정상 종료 순서

1. `conversation`
2. `status`
3. `answer.chunk` 0회 이상
4. `reference.set`
5. `done` 2회 (`event.model_key="solar"`, `event.model_key="gemma"`)

deterministic terminal 문장이 있는 정상/terminal 경로의 사용자 가시 종료 순서는 다음을 보장한다.

1. `answer.chunk(event.model_key="solar")`
2. `answer.chunk(event.model_key="gemma")`
3. `reference.set`
4. `done(event.model_key="solar")`
5. `done(event.model_key="gemma")`

### 예외 종료 규칙

- graph 미준비:
  - `answer.chunk(solar)` -> `answer.chunk(gemma)` -> `reference.set` -> `done(solar)` -> `done(gemma)`
- 전략 위반을 사용자 메시지로 강등한 경우:
  - `answer.chunk(solar)` -> `answer.chunk(gemma)` -> `reference.set` -> `done(solar)` -> `done(gemma)`
  - 이 경로는 `done.meta.output_message`에 사용자 가시 fallback 문장을 싣는다.
- 내부 예외:
  - `answer.chunk(solar)` -> `answer.chunk(gemma)` -> `reference.set` -> `done(solar)` -> `done(gemma)`
- 최종 답변이 비어 있는 비정상 종료:
  - route guard가 `done.meta.output_message`를 강제로 생성한다.
  - `done.meta.error_code="MISSING_FINAL_ANSWER"`가 포함된다.

### Contract-invalid 종료 규칙

다음 상태에서는 모델 답변을 사용자에게 스트리밍하지 않는다.

- detail-like 요청이 단일 후보 guard를 통과하지 못함
- detail 요청이 broad `SEARCH_RECOVERY` list 결과로 확장될 위험이 있음
- display snapshot의 visible order와 canonical evidence 식별자 축이 맞지 않음
- answer-state consistency가 final answer publication을 막음

이 경우 API는 다음 중 하나로 닫는다.

- 실제 사용자 대상이 모호하면 `done.meta.clarification`
- 시스템/tool/planner 오류면 `done.meta.answer_kind="error"` 또는 degraded terminal message
- 데이터 없음이면 `done.meta.answer_kind="no_result"`

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

### `TerminalDoneMeta`

`done.meta`는 `AnswerArtifact.to_meta_dict()` 결과를 기준으로 하며, `stream_metrics`(지연 시간 등)와 `meta`(실행 전략 등) 필드가 최상위 객체로 병합된 형태입니다.

| 필드 | 타입 | 조건 | 설명 |
|---|---|---|---|
| `answer_kind` | string | 항상 | `llm_streamed`, `llm_collected`, `detail_cache`, `detail_profile`, `clarification`, `no_result`, `direct_answer`, `error` |
| `user_visible_final_required` | boolean | 항상 | 최종 사용자 노출용 terminal message 필수 여부. `true`이면 클라이언트는 반드시 답변의 마지막에 이 정보를 반영해야 함. |
| `elapsed_ms` | number | 대부분 | 전체 요청 처리 시간(ms) |
| `ttft_any_ms` | number | 모델 경로 | 첫 번째 청크 수신까지의 시간(ms) |
| `ttft_content_ms` | number | 모델 경로 | 유효 텍스트 첫 청크 수신까지의 시간(ms) |
| `content_chars` | integer | 모델 경로 | 최종 생성 답변 글자 수 |
| `answer_source` | string | 대부분 | 선택된 답변 소스 (예: `solar`, `gemma`, `cache`) |
| `model_key` | string | 모델 기반 | `done.meta` 내부의 선택 모델 키. top-level `event.model_key`는 모든 `done`에서 별도로 `"solar"`/`"gemma"` 두 프레임으로 fan-out된다. |
| `groundedness_status` | string | 검증 경로 | 답변의 근거 정합성 상태 (`success`, `fail`, `not_applicable`) |
| `visible_answer_manifest` | object | 목록 응답 | 다음 턴 참조용 엔티티 맵 (ordinal, id, title 포함) |
| `visible_answer_manifest_publication` | object | 목록 발행 | 발행된 매니페스트 상세 정보 및 상태 |
| `agent_current_context_type` | string | Agent 경로 | 현재 에이전트의 문맥 상태 (`search`, `refine`, `clarification`) |
| `output_message` | string | terminal 경로 | deterministic terminal 문장 (clarification, no-result 등) |
| `clarification` | object | clarification 경로 | `ClarificationPayload` 본문 정보 |
| `error_code` | string | 오류 경로 | 구체적인 시스템 또는 전략 위반 코드 |


### `ReferenceItem`

`ReferenceItem`의 외부 contract는 그대로 `tag` / `id` / `title`만 사용한다. 다만 route 내부적으로 `canonical_evidence`를 fallback source로 쓸 때는 raw qdrant payload가 아니므로, `tag`가 없으면 `source_type` known mapping 또는 `pjt_id -> project` bridge 규칙으로만 `tag`를 복원한다.

정상 RAG 경로에서 `selected_answer_artifact.references`는 최종 프롬프트에 실제로 포함된 evidence packer의 `refs` 순서를 우선 소유한다. 따라서 `reference.set.references`의 기본 순서와 개수는 raw/canonical 후보 전체가 아니라 프롬프트 JSON 항목(`identity.rank`)과 lockstep인 prompt refs를 따른다. `canonical_evidence`와 context hit는 artifact reference가 없을 때만 fallback source다.

```json
{
  "tag": "IRD_NAI_PJT_INFO",
  "id": "PJT-2024-0001",
  "title": "과제 제목"
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `tag` | string | 프론트엔드가 그대로 소비하는 raw tag |
| `id` | string | 프론트엔드가 그대로 소비하는 식별자 |
| `title` | string | 프론트엔드가 그대로 소비하는 제목 |
| `invalid` | boolean | optional. `true`이면 필수 reference 필드가 부족한 후보를 버리지 않고 실은 항목 |
| `invalid_reason` | string | optional. `missing_tag`, `missing_project_or_result_id`, `missing_title` 등 |
| `candidate_source` | string | optional. invalid 후보의 수집 단계 (`artifact_references`, `canonical_evidence`, `documents_used`) |

- route는 reference를 `selected_answer_artifact.references -> canonical_evidence -> context hit` 순서로 복구한다.
- route는 `tag`, `title`, `id`를 정규화하지 않는다. 프론트엔드에 전달할 최종 `ReferenceItem`만 구성한다.
- qdrant top-level reference 추출 규칙은 아래와 같다.
  - `tag`: 최상위 `tag` -> `source_table`
  - `title`: 최상위 `title1` -> `title2` -> `title_text` -> `title`
  - `id`: `tag=IRD_NAI_PJT_INFO`이면 최상위 `pjt_id` -> `id` -> `doc_id` -> `rst_id`; 그 외에는 `rst_id` -> `pjt_id` -> `id` -> `doc_id`
- route 내부 호환성 때문에 prompt refs처럼 이미 `tag`/`source_table`, `title`, `id`/`doc_id` 형태로 들어온 artifact reference도 읽을 수 있지만, 프로젝트 tag에서는 `pjt_id` 축이 우선이다.
- invalid reference payload는 더 이상 discard하지 않는다. route는 `REFERENCE.INVALID_PAYLOAD` 로그를 남긴 뒤 원본 candidate의 JSON-safe copy를 `reference.set.references`에 포함한다.
- invalid 항목은 `invalid: true`, `invalid_reason`, `candidate_source`를 포함한다. 클라이언트는 이 항목을 일반 출처 링크와 동일하게 처리하면 안 된다.
- fallback은 현재 source에서 emit된 reference가 하나도 없을 때만 다음 source로 이동한다. invalid 항목도 emit된 reference로 계산한다.

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
| `final_answer_meta` | object \| null | `done.meta`와 같은 계열의 최종 메타 |
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

## Contract-invalid 및 provider failure

`contract-invalid`는 API 오류와 다르다. 요청은 정상 처리됐지만, 실행 계약상 모델 답변을 생성하면 안 되는 상태다.

| 상태 | 사용자 응답 | 로그/메타 |
|---|---|---|
| 단일 후보 없는 detail | `clarification` 또는 deterministic 보류 문장 | `RAG.DETAIL.SINGLE_CANDIDATE_GUARD`, `detail_single_candidate_guard_failed` |
| detail-like broad search 차단 | deterministic 보류 문장 | `RAG.EXECUTION_MANAGER.RESULT`, `DETAIL_SINGLE_CANDIDATE_GUARD` 또는 lookup 정책 전환 |
| display/canonical mismatch | deterministic 보류 문장 | `DISPLAY.SNAPSHOT.BUILT`, `ANSWER.STATE_DIAG` |
| Solar content 없음 | Gemma 또는 fallback terminal message | `STREAM.DONE.solar_error_code`, `TTFT_DEADLINE_EXCEEDED` |
| 두 모델 모두 state-inconsistent | deterministic 보류 문장 | `LLM.RESULT.selection_reason=both_models_state_inconsistent` |

사용자 모호성이 아닌 내부 오류는 `clarification`으로 위장하지 않는다. 이 경우 `agent_internal_error` 또는 degraded terminal answer meta를 사용한다.

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
- 중간 모델 답변은 provisional이다. contract-invalid가 확인된 경우 final answer와 manifest publication guard가 사용자에게 신뢰 가능한 terminal state를 제공해야 한다.
