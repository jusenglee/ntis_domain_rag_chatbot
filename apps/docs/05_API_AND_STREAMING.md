# API And Streaming

Source: `apps/api/routes.py`

The HTTP layer is intentionally thin. It builds `AgentPipelineState`, invokes
the compiled graph, and serializes the final state.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | serve the UI template or fallback HTML |
| `GET` | `/health` | graph and KV readiness |
| `POST` | `/query` | single JSON response |
| `POST` | `/query/stream` | SSE response |

## Request

`QueryRequest`:

```json
{
  "question": "사용자 질문",
  "conversation_id": "optional conversation id",
  "request_overrides": {}
}
```

If `conversation_id` is absent, the route creates one.
Blank questions are delegated to the graph instead of rejected at the route.

## JSON Response

`/query` returns:

```json
{
  "conversation_id": "...",
  "request_id": "...",
  "output_message": "...",
  "answer_kind": "...",
  "references": [],
  "latencies": {},
  "total_ms": 0,
  "dialogue_intent": {},
  "entity_resolution": {},
  "search_plan": {},
  "evidence_bundle_view": "list_compact",
  "guard_decision": {},
  "diagnostics": {}
}
```

`diagnostics` may include plan state, search status, evidence count, guard
decision, manifest publication, session slot presence, and artifact metadata.

## SSE Events

`/query/stream` emits `StreamEvent` frames:

- `conversation`
- `answer.chunk`
- `reference.set`
- `done`

The route starts graph execution in the background, reads the emitter queue, and
closes after the graph finishes.

Public `answer.chunk` frames use frontend lane labels only — `model_key="solar"`
(compare panel **A**) and `model_key="gemma"` (compare panel **B**). The
translation happens at the HTTP streaming boundary
(`apps/api/streaming/model_keys.py::stream_events_for_frontend`):

- **Dual-model answers** (default, `RAG_DUAL_ANSWER_ENABLED=true`): the pipeline
  generates two answers for the same evidence. The **main** answer (Solar /
  `solar_vllm_0`) streams tagged `model_key="solar"` and routes to panel A only;
  the **comparison** answer (Gemma / `gemma_triton_0`) streams tagged
  `model_key="gemma"` and routes to panel B only. The main (Solar) answer is the
  one CriticAgent validates and that drives `reference.set` + session state; the
  comparison (Gemma) answer is shown raw. A chunk already tagged with a frontend
  lane (`solar`/`gemma`) is forwarded to that single lane.
- **Single message** — any chunk with an internal/deterministic `model_key`
  (`solar_vllm_0`, `gemma_triton_0`, `no_result`, `internal_error`,
  `agentic_direct_answer`, `clarification`, …) is fanned out into two frames
  (`solar` + `gemma`) so the one message shows identically in both panels. This
  covers deterministic branches and the single-model rollback
  (`RAG_DUAL_ANSWER_ENABLED=false`).

## AnswerArtifact

Source: `apps/api/streaming/contracts.py`

`AnswerArtifact` is the public answer object carried from graph nodes to routes.

Important fields:

- `text`
- `answer_kind`
- `references`
- `visible_answer_manifest`
- `error`
- `meta`

If a node emits an error, use `AnswerArtifact(answer_kind="error")` with an
`ErrorArtifact`.
