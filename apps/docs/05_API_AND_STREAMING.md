# 05. API와 스트리밍

> 프런트엔드/연동 개발자용. §1이 핵심, §2 이후가 정밀 참조. 정본 코드: `apps/api/routes.py`, 봉투·레인 `apps/api/streaming/`.

## 1. 가장 먼저 알 것

1. **답이 두 개 온다.** 같은 근거로 **Solar**(패널 A)와 **Gemma**(패널 B)가 각각 답한다. `model_key`로 구분: `"solar"` → 패널 A, `"gemma"` → 패널 B. (ADR-0021)
2. **에이전트라서 검색을 안 할 수도 있다.** 인사·거절은 NTIS 없이 바로 답이 온다.

## 2. 엔드포인트

| 메서드 | 경로 | 용도 |
|---|---|---|
| `GET` | `/` | UI 템플릿 또는 폴백 HTML |
| `GET` | `/health` | 그래프·KV 준비 상태 |
| `POST` | `/query` | 단일 JSON 응답 |
| `POST` | `/query/stream` | SSE 응답 |

**요청 `QueryRequest`:** `question`, `conversation_id`(없으면 라우트가 생성), `request_overrides`(`{}`). 빈 질문도 라우트가 막지 않고 그래프에 위임.

## 3. 정밀 참조 — `/query` JSON 응답

`conversation_id`, `request_id`, `output_message`, `answer_kind`, `references[]`, `latencies{}`, `total_ms`, `dialogue_intent{}`, `entity_resolution{}`, `search_plan{}`, `evidence_bundle_view`(`"list_compact"`), `guard_decision{}`, `diagnostics{}`. ADR-0021로 `secondary_output_message` + `model_answers` 추가.

`diagnostics`엔 plan state, search status, evidence count, guard decision, manifest publication, session slot 존재 여부, artifact metadata 등이 들어갈 수 있다.

## 4. 정밀 참조 — `/query/stream` SSE

`StreamEvent` 프레임. 봉투: `data: {"tag":"event","event":{...}}`. 프레임 종류: `conversation`, `answer.chunk`, `reference.set`, `done`. 라우트가 그래프를 백그라운드로 시작하고 emitter 큐를 읽어 흘리며, 그래프 종료 후 닫는다.

### 레인 라우팅 (`apps/api/streaming/model_keys.py::stream_events_for_frontend`)

공개 `answer.chunk` 프레임은 프런트 레인 라벨만 쓴다(소문자 정확 비교):
- `model_key="solar"` → 패널 **A**(`rawA`)
- `model_key="gemma"` → 패널 **B**(`rawB`)

- **듀얼(기본):** 메인(Solar/`solar_vllm_0`) → 패널 A만, 비교(Gemma/`gemma_triton_0`) → 패널 B만.
- **단일 메시지:** 내부/결정적 `model_key`(`solar_vllm_0`, `gemma_triton_0`, `no_result`, `internal_error`, `agentic_direct_answer`, `clarification` 등)는 **두 프레임으로 fan-out**(결정적 분기 + 단일모델 롤백 커버). 상수 `PRIMARY_FRONTEND_KEY`/`SECONDARY_FRONTEND_KEY`.

## 5. 정밀 참조 — AnswerArtifact

`apps/api/streaming/contracts.py`. 그래프 노드 → 라우트로 가는 공개 답변 객체. 필드: `text`, `answer_kind`, `references`, `visible_answer_manifest`, `error`, `meta`. 오류 노드는 `AnswerArtifact(answer_kind="error")` + `ErrorArtifact` 사용.

`answer_kind` 값: `llm_streamed`, `llm_collected`, `detail_cache`, `detail_profile`, `clarification`, `no_result`, `direct_answer`, `error`. (`response.unsupported`는 별도 값 없이 meta `kind=agentic_unsupported`로 흐름.)
