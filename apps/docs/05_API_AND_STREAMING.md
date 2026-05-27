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
