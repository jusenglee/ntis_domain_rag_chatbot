# API 응답 명세

이 문서는 현재 `apps/api/routes.py` 기준의 HTTP/SSE 응답 계약만 정리한다.

> **2026-05-19 갱신**: [ADR-0019](./ADR/ADR-0019_Seven_Agent_Agentic_Redesign.md) 7-agent
> 재설계 적용. `/query` 응답 payload의 진단 키가 다음으로 교체되었다:
>
> | 폐기된 키 | 후속 키 |
> |---|---|
> | `judgment` | `dialogue_intent` (+ `entity_resolution`) |
> | `search_task` | `search_plan` |
> | `final_answer` | `guard_decision` (+ `evidence_bundle_view`) |
>
> SSE `StreamEvent.kind`는 4종만 유효: `conversation`, `answer.chunk`, `reference.set`, `done`.
> 이전 `status` kind는 사용처 없이 남아 있어 [ADR-0019에서 제거됨](./ADR/ADR-0019_Seven_Agent_Agentic_Redesign.md).
>
> **빈 질문**(`question == ""`)은 라우트에서 400으로 거부하지 않고 워크플로우에 위임 →
> `DialogueAgent`가 `answer_kind="clarification"` 응답을 만든다.
>
> 내부 실행 계약은 [`02_CONTRACTS_AND_RULES.md`](./02_CONTRACTS_AND_RULES.md)와
> [ADR-0019](./ADR/ADR-0019_Seven_Agent_Agentic_Redesign.md)를 참고하라.
> 폐기된 엔드포인트(`/query/debug`, `/metrics`, `/metrics/stream`, `/health/details`)는
> 그대로 폐기 상태.

## Source of Truth

- route surface: `apps/api/routes.py`
- stream envelope: `apps/api/streaming/contracts.py`, `apps/api/streaming/sse_encoder.py`
- 함께 읽을 문서: [`06_운영과_환경.md`](./06_운영과_환경.md), [`02_CONTRACTS_AND_RULES.md`](./02_CONTRACTS_AND_RULES.md)

---

## 엔드포인트 요약

| 엔드포인트 | 메서드 | 응답 타입 | 설명 |
|---|---|---|---|
| `/` | `GET` | `text/html` | 메인 UI 페이지 (templates/index.html) |
| `/health` | `GET` | `application/json` | 단순 헬스체크 |
| `/query` | `POST` | `application/json` | 단일 응답 (스트리밍 없음) |
| `/query/stream` | `POST` | `text/event-stream` | SSE 스트리밍 응답 (주 엔드포인트) |

폐기된 엔드포인트:
- `/query/debug` — debug payload는 `done.meta`로 통합
- `/metrics`, `/metrics/stream` — 외부 Prometheus 직접 조회로 이관
- `/health/details` — `/health` 응답에 통합

---

## 공통 요청 바디 (`/query`, `/query/stream`)

```json
{
  "question": "신동구 연구자(한국과학기술정보연구원)의 활동내역을 알려줘",
  "conversation_id": "9c6e0bee-5ab2-419d-8a9a-3d6aa14777ba",
  "request_overrides": {}
}
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `question` | string | ✅ | 사용자 질문 텍스트 |
| `conversation_id` | string \| null | ❌ | 대화 세션 UUID. 없으면 신규 생성 |
| `request_overrides` | object | ❌ | 요청별 메타 (현재 비활성, 향후 확장용) |

### 입력 검증

- `question` 이 빈 문자열이면 `400 question is empty`
- LLM 샘플링 파라미터(Temperature/Top-P/Max-Token/Top-K) 오버라이드는 폐기. LLM 어댑터 기본값 사용.
- RAG override 파라미터(RAG_MIN_DENSE_SCORE 등)도 폐기. SearchAgent가 단일 strategy로 동작.

---

## `/query/stream`

### 전송 규칙

- media type: `text/event-stream`
- canonical envelope: 모든 frame은 `data: {"tag":"event","event":...}` 형태
- 사용자 가시 텍스트는 `answer.chunk`로만 전달 (reasoning 청크 분리 출력 없음)
- 정상/오류 종료 모두 `reference.set` (있을 때) + `done` 으로 마감
- 30초 idle 시 SSE `: keep-alive\n\n` 주석 frame 전송 (브라우저 연결 유지)

### 공통 SSE envelope

```json
{
  "tag": "event",
  "event": {
    "kind": "done",
    "request_id": "9c6e0bee-...-a1b2c3d4",
    "seq": 4,
    "model_key": null,
    "content": null,
    "references": null,
    "meta": {
      "conversation_id": "9c6e0bee-...",
      "request_id": "9c6e0bee-...-a1b2c3d4",
      "output_message": "신동구 연구자는...",
      "answer_kind": "llm_streamed",
      "references": [...]
    }
  }
}
```

### event kind

| kind | content | references | meta | 발행 시점 |
|------|---------|------------|------|-----------|
| `conversation` | null | null | `{conversation_id, turn_id}` | turn 시작 직후 |
| `answer.chunk` | 부분 답변 텍스트 | null | `{}` | LLM 스트리밍 또는 결정적 메시지 청크 |
| `reference.set` | null | `ReferenceItem[]` | `{}` | publish 결정 시 manifest와 함께 (있을 때) |
| `done` | null | null | `TerminalDoneMeta` | 항상 마지막에 1회 |

레거시 `status` 이벤트는 폐기 (retrieval 단계가 충분히 빨라 불필요).

### 정상 종료 순서

1. `conversation`
2. `answer.chunk` 0회 이상
3. `reference.set` (publish이면서 manifest가 비어있지 않을 때)
4. `done`

### 예외 종료

| 경로 | answer.chunk 내용 | reference.set | done.meta.answer_kind |
|------|-------------------|---------------|------------------------|
| `JudgmentDecision.direct_answer` | direct_answer.text | 생략 | `direct_answer` |
| `JudgmentDecision.clarification` | clarification.question | 생략 | `clarification` |
| `SearchResult.status=empty` | no-result 메시지 | 생략 | `llm_streamed` (text만, manifest empty) |
| `SearchResult.status=error` | "내부 오류로..." | 생략 | `error` |
| `FinalAnswer.decision=clarify` | clarification.question | 생략 | `clarification` |
| `FinalAnswer.decision=internal_error` | "내부 오류로..." | 생략 | `error` |
| graph 실행 중 예외 | "내부 오류: {exc}" | 생략 | (done은 정상 전송) |

### `TerminalDoneMeta`

`done.meta`는 라우트가 워크플로우 최종 state에서 추출한 payload다.

| 필드 | 타입 | 설명 |
|------|------|------|
| `conversation_id` | string | 세션 UUID |
| `request_id` | string | 요청 식별자 |
| `output_message` | string | 최종 사용자 답변 텍스트 |
| `answer_kind` | string | `llm_streamed` / `clarification` / `direct_answer` / `error` / `llm_collected` |
| `references` | `ReferenceItem[]` | 발행된 출처 목록 (publish일 때) |
| `latencies` | object | 노드별 latency (`judgment_agent`, `search_agent`) |
| `total_ms` | number | 요청 처리 총 시간 |
| `judgment` | object \| null | JudgmentDecision 직렬화 |
| `search_task` | object \| null | SearchTask 직렬화 |
| `final_answer` | object \| null | FinalAnswer 직렬화 |

### `ReferenceItem`

ADR-0017 published manifest 형식.

```json
{
  "rank": 1,
  "id": "1711121955",
  "tag": "IRD_NAI_PJT_INFO",
  "title": "과학기술 기계학습 데이터 구축"
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `rank` | integer | published_rank (사용자가 본 번호) |
| `id` | string | `tag=IRD_NAI_PJT_INFO`이면 `pjt_id`, 성과 계열이면 `rst_id` |
| `tag` | string | DataTag (예: `IRD_NAI_PJT_INFO`, `IRD_NAI_RI_FCLT_EQUIP`) |
| `title` | string | CanonicalEvidence.title (없으면 `(제목 없음 #N)`) |

ADR-0017에 따라:
- `tag=IRD_NAI_PJT_INFO` → `id` 의미 = `pjt_id`
- 성과 계열(`IRD_NAI_RI_*`) → `id` 의미 = `rst_id`
- `pjt_no`는 ReferenceItem.id 로 사용하지 않는다 (group key는 internal manifest에만)

---

## `/query`

스트리밍 없이 워크플로우 실행 결과만 JSON으로 반환.

### 성공 응답 (200)

```json
{
  "conversation_id": "9c6e0bee-...",
  "request_id": "9c6e0bee-...-abc",
  "output_message": "신동구 연구자는 ScienceON 융합서비스를...",
  "answer_kind": "llm_streamed",
  "references": [...],
  "latencies": {"judgment_agent": 1.42, "search_agent": 0.18},
  "total_ms": 1830.5,
  "judgment": {...},
  "search_task": {...},
  "final_answer": {...}
}
```

스키마는 `/query/stream`의 `done.meta`와 동일.

### 실패 응답

| 상태 | 본문 |
|------|------|
| `400` | `{"detail": "question is empty"}` |
| `500` | `{"detail": "<exception message>"}` |
| `503` | `{"detail": "pipeline graph not initialized"}` |

---

## `/health`

### 응답 (200)

```json
{
  "ok": true,
  "graph_compiled": true,
  "kv_ready": true
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `ok` | boolean | 항상 `true` (HTTP 200으로 판별) |
| `graph_compiled` | boolean | LangGraph 컴파일 완료 여부 |
| `kv_ready` | boolean | KV 스토어 연결 여부 |

`/health`는 항상 `200`을 반환한다. 컴파일 미완료/KV 미연결은 응답 본문 boolean으로만 표시. 운영 시스템이 실제 미준비 판별을 원하면 `graph_compiled && kv_ready` 두 값이 모두 `true`인지 클라이언트가 확인.

---

## `/` (메인 UI)

`templates/index.html`을 그대로 반환. 파일이 없으면 `<h1>NTIS RAG (pipeline)</h1>` 정도의 기본 HTML.

---

## 폐기된 응답 개념

다음 개념은 ADR-0018 이후 더 이상 라우트 응답에 등장하지 않는다.

- `done.meta.visible_answer_manifest` / `visible_answer_manifest_publication` — `references` (ReferenceItem 배열)로 통합
- `done.meta.merge_debug` / `selected_answer_meta` — 단일 LLM 생성으로 통합되어 의미 없음
- `done.meta.groundedness_status` / `state_consistency_status` — `final_answer.reasoning` 으로 통합
- `done.meta.knowledge_sufficiency` / `processing_strategy` — 단계 자체가 폐기됨
- `done.meta.degraded` / `error_code` 상세 — `answer_kind=error` 한 가지로 단순화
- `clarification` payload 의 `clarification_type` / `candidates` / `resume_token` — `Clarification.question` + `options` 로 단순화
- `event.kind=status` — 폐기

---

## 유지 규칙

- `/query/stream`은 canonical `tag="event"` envelope 하나만 유지한다.
- retrieval metadata 전체를 route 응답에 dump하지 않는다 (Pydantic `.model_dump()`된 SearchTask/FinalAnswer만).
- ReferenceItem은 `rank/id/tag/title`의 최소 canonical payload로만 노출한다.
- `pjt_id`와 `pjt_no` 의미는 reference id 선택에서도 섞지 않는다 (ADR-0017).
- `answer.chunk` 텍스트는 LLMGenerator가 `stream_emitter.publish()`로 직접 보내는 토큰. 라우트는 인코딩만 한다.
