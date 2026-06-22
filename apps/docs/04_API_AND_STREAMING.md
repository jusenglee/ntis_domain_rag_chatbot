# 04. API와 스트리밍

> 프런트엔드/연동 개발자가 "무슨 요청을 보내고, 어떤 이벤트를 받는가"를 다룬다. §1~§2가 꼭 알아야 할 핵심, §3 이후가 정밀 참조다. 정본 코드: `apps/api/routes.py`, 봉투 `apps/api/streaming/contracts.py`.

## 1. 가장 먼저 알 두 가지

1. **답이 두 개 온다.** 같은 질문에 **Solar**(메인)와 **Gemma**(비교) 두 모델이 각각 답한다. 이벤트의 `model_key`로 구분(`"solar"` / `"gemma"`)하고, **프런트엔드가 둘 다 화면에 그린다.**
2. **본문 `[N]`과 출처 목록이 일치한다.** 답변 문장의 `[1]`은 `reference.set`의 1번과 정확히 같은 항목이다. (이게 깨지면 발행을 보류한다.)

### 한 요청의 이벤트 순서 (정상)

```
conversation         (conversation_id 알려줌)
status               (검색 중...)
answer.chunk × N     (답변이 토막토막 흘러나옴)
reference.set        (출처 목록 [1],[2],...)
done (model_key=solar)
done (model_key=gemma)   ← done은 항상 2번!
```

## 2. 엔드포인트

| 메서드/경로 | 형식 | 비고 |
|---|---|---|
| `POST /query/stream` | `text/event-stream` | 주 스트리밍 엔드포인트 (`routes.py` ~523행) |
| `POST /query/debug` | JSON | `ENABLE_DEBUG_ROUTES`일 때만, 아니면 `404` |
| `GET /health` | JSON | `200`/`503` |
| `GET /health/details` | JSON | 항상 `200` |
| `GET /metrics` | JSON | Prometheus 스냅샷 |
| `GET /metrics/stream` | SSE | `/query/stream`과 다른 형식(event name `metrics`) |

**요청 본문(`/query/*`):** `question`(유일 필수), `conversation_id`, `Temperature`, `Top-P`, `Max-Token`, `Top-K`, `RAG_MIN_DENSE_SCORE`, `RAG_TOPK_DENSE`, `RAG_W_LEX`, `RAG_TOPK_LEX_CAND`. override 검증 실패 → `422`(`Temperature>=0`, `0<Top-P<=1`, `Max-Token>=1`, `Top-K>=1`).

## 3. 정밀 참조 — 봉투와 이벤트

모든 프레임은 단일 정본 봉투: `data: {"tag":"event","event":...}`. `event` 필드: `kind`, `request_id`, `seq`, `model_key`, `content`, `references`, `meta`.

**이벤트 종류:** `conversation`(meta `{conversation_id}`) · `status`(meta `{status:"retrieve"}`) · `answer.chunk`(부분 답변) · `reference.set`(`ReferenceItem[]`) · `done`(`TerminalDoneMeta`).

**듀얼 답변 세부:** `done`은 항상 `solar`·`gemma` 2프레임(동일 `meta`). `conversation`/`status`/`reference.set`의 `model_key`는 null. 결정적 종료(`direct_answer`/`clarification`/`no_result`/`error`)도 같은 텍스트를 `answer.chunk(solar)` → `answer.chunk(gemma)` → `reference.set` → `done`×2로 보낸다.

## 4. 정밀 참조 — `TerminalDoneMeta` 주요 필드

`AnswerArtifact.to_meta_dict()` 기반 + `stream_metrics`/`meta` 병합.

`answer_kind`(`llm_streamed`/`llm_collected`/`detail_cache`/`detail_profile`/`clarification`/`no_result`/`direct_answer`/`error`), `user_visible_final_required`, `elapsed_ms`, `ttft_any_ms`, `ttft_content_ms`, `content_chars`, `answer_source`(`solar`/`gemma`/`cache`), `model_key`, `groundedness_status`(`success`/`fail`/`not_applicable`), `visible_answer_manifest`, `visible_answer_manifest_publication`, `agent_current_context_type`(`search`/`refine`/`clarification`), `output_message`, `clarification`, `error_code`.

## 5. 정밀 참조 — `ReferenceItem` (출처)

외부 계약 = `tag`/`id`/`title`만. 선택: `invalid`(bool), `invalid_reason`(`missing_tag`/`missing_project_or_result_id`/`missing_title`), `candidate_source`(`artifact_references`/`canonical_evidence`/`documents_used`).

- 복구 순서: `selected_answer_artifact.references → canonical_evidence → context hit`.
- Qdrant 추출: `tag`=top-level `tag`→`source_table`; `title`=`title1`→`title2`→`title_text`→`title`; `id`= tag가 `IRD_NAI_PJT_INFO`면 `pjt_id`→`id`→`doc_id`→`rst_id`, 아니면 `rst_id`→`pjt_id`→`id`→`doc_id`. (`pjt_no`는 1차 `id`가 되지 않음 — ADR-0017)
- 정상 RAG 경로의 `reference.set`은 프롬프트 참조(`identity.rank`)와 lockstep → 본문 `[N]` == reference.set N번 보장.
- invalid payload는 폐기하지 않고 `REFERENCE.INVALID_PAYLOAD` 로그 + `invalid:true`로 포함.

## 6. 정밀 참조 — contract-invalid / provider 실패 매핑

| 상태 | 응답 |
|---|---|
| 단일후보 없는 detail | 명확화 또는 결정적 보류 (`RAG.DETAIL.SINGLE_CANDIDATE_GUARD`) |
| detail류 broad-search 차단 | 결정적 보류 (lookup 전환) |
| display ↔ canonical 불일치 | 결정적 보류 (`DISPLAY.SNAPSHOT.BUILT`, `ANSWER.STATE_DIAG`) |
| Solar content 없음(reasoning만) | Gemma/폴백 (`TTFT_DEADLINE_EXCEEDED`) |
| 두 모델 모두 state-inconsistent | 결정적 보류 (`both_models_state_inconsistent`) |

> contract-invalid는 LLM 답변 스트림 없이 닫는다(거짓 출처·환각 방지).
