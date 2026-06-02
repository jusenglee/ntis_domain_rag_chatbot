# ADR-0021: Dual-Model Answer Output (Solar A / Gemma B)

Status: accepted, 2026-06-02

## Context

Until now the pipeline produced a single answer. `runtime.py` bound
`AnswerAgent` to `gemma_triton_0`, so `gemma_triton_0` was the answer model and
`solar_vllm_0` was used only for dialogue/planner/critic-grounding judgments.

The frontend (`templates/index.html`) already ships a two-panel compare UI. It
consumes `POST /query/stream` and demultiplexes `answer.chunk` frames by a
top-level `model_key`:

- `model_key === "solar"` → panel **A** (`rawA`)
- `model_key === "gemma"` → panel **B** (`rawB`)

Strict, lowercased equality. Frames are wrapped in the envelope
`data: {"tag":"event","event":{...}}`. An in-progress streaming layer
(`apps/api/streaming/model_keys.py`, wired at `routes.py`) fanned **every**
answer chunk out to both lanes, so the single Gemma answer showed identically in
both panels.

The requirement: make panel A and panel B show *different* answers — A from
Solar (vLLM, ~102B), B from Gemma (Triton, gemma-3-27b) — and, per product
decision, **Solar is the main (canonical) answer**.

## Decision

Generate two answers per turn over the same evidence and route each to its own
panel.

- **A = Solar (`solar_vllm_0`) = main / canonical.** Stored as
  `state.answer_draft`. This is the draft `CriticAgent` validates (citation `[N]`
  range, grounding, `repair_answer`) and the one that publishes `reference.set`
  and writes session state (manifest / subject / focused detail). Its text is
  `final_answer_text` / `output_message`.
- **B = Gemma (`gemma_triton_0`) = comparison / secondary.** Stored as
  `state.secondary_answer_draft`. Shown raw — no grounding/citation gate, no
  repair, not persisted. Gemma is still used by the `response.*` direct-answer
  tools.

This moves the answer-model role from Gemma to Solar (supersedes the role split
in [01_ARCHITECTURE](../01_ARCHITECTURE.md) / [06_OPERATIONS_AND_ENV](../06_OPERATIONS_AND_ENV.md)).

### Streaming lane contract

`stream_events_for_frontend` (`apps/api/streaming/model_keys.py`) routes by
`model_key`:

- A chunk already tagged with a frontend lane (`solar` / `gemma`) is forwarded to
  **that single lane** (per-model A/B separation).
- Any other key — internal model ids (`solar_vllm_0`, `gemma_triton_0`),
  deterministic branches (`no_result`, `clarification`, `agentic_direct_answer`,
  `internal_error`) — is **fanned out to both lanes**, so a single message shows
  identically in both panels.

Because `[N]` citations reference the shared `EvidenceBundle`, one
`reference.set` is correct for both panels.

### Toggle

`RAG_DUAL_ANSWER_ENABLED` (default `true`). When `false`, only the main (Solar)
answer is generated and is fanned out to both panels (single-answer rollback).
The main answer model stays Solar regardless of this flag.

## Implementation

| File | Change |
| --- | --- |
| `apps/api/runtime.py` | `answer_agent = AnswerAgent(llm=dialogue_llm)` (Solar, main); `answer_agent_secondary = AnswerAgent(llm=answer_llm)` (Gemma) gated by `RAG_DUAL_ANSWER_ENABLED` |
| `apps/pipeline/agent_workflow.py` | `AgentPipelineDeps.answer_agent_secondary`; `_make_node_answer` runs main + comparison via `asyncio.gather`, main → `model_key="solar"`, comparison → `model_key="gemma"` |
| `apps/pipeline/agent_state.py` | `secondary_answer_draft` field |
| `apps/api/streaming/model_keys.py` | `stream_events_for_frontend` lane routing; `PRIMARY_FRONTEND_KEY`/`SECONDARY_FRONTEND_KEY` |
| `apps/api/routes.py` | response payload adds `secondary_output_message` + `model_answers` |

Behavior details:

- The comparison (Gemma) draft is generated only on the **first pass** over a
  non-empty bundle. A `repair_answer` re-pass regenerates the **main (Solar)**
  draft only — the comparison panel keeps its first answer.
- A comparison-generation failure is isolated and never blocks the main answer.
- An empty-result turn produces a single deterministic `no_result` message
  (fanned to both panels); the comparison draft is not generated.

## Consequences

Positive:

- True side-by-side model comparison with the same evidence and prompt.
- Frontend needs no change — it already demuxes by `model_key`.
- Citation/reference numbering stays consistent across both panels.
- Single env flag for rollback.

Negative / risks:

- **The production answer body is now Solar**, not Gemma. Citation-format and
  grounding behavior depend on Solar following the inline answer prompt.
- Higher cost/latency per turn (two generations; mitigated by concurrency).
- On `repair_answer`, the repaired main draft is **appended** in panel A (the
  frontend does not reset `rawA`) — a pre-existing single-model behavior.
- Per-panel citations are not displayed: the frontend currently ignores
  `reference.set` (no citation UI). Splitting references per panel needs a
  frontend change (out of scope here).

## Rollback

```powershell
$env:RAG_DUAL_ANSWER_ENABLED = "false"
```

Single Solar answer, fanned to both panels. CriticAgent / references / session
behavior is unchanged.
