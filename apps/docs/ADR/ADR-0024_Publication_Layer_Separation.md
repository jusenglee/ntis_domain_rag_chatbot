# ADR-0024: Publication Layer Separation — answer_curator is evidence-only

Status: accepted, 2026-06-02

## Context

After ADR-0023, `node_answer_curator` carried two unrelated responsibilities:

1. **Evidence selection** (Layer 2): pick `CanonicalEvidence` from accumulated
   observations → build `EvidenceBundle`.
2. **Direct-response publication** (Layer 3): detect `response.*` tool results
   (and `PlannerStep.answer_text`), then emit `final_answer_text`,
   `AnswerArtifact`, and a stream chunk — bypassing `AnswerAgent`/`CriticAgent`.

The name "curator" describes only the first. The second is a publication act.
Mixing them violates ADR-0018's three-layer separation and means every new
direct-response kind (`no_result`, `unsupported`, `direct_answer`,
`planner_answer_text`, …) accretes onto the curator over time.

This is ADR-0025 Issue 3 (answer_curator Layer violation) and Issue 4
(CriticAgent bypass via `PlannerStep.answer_text`).

## Decision

Split the two responsibilities:

- **`answer_curator`** — evidence-only. Builds `EvidenceBundle` (+ `SearchResult`)
  from observations and always flows to `answer_agent`. Never emits text.
- **`emit_tool_response`** (new node, Publication layer) — receives a
  `response.direct_answer` / `response.unsupported` terminal observation and
  emits `final_answer_text` + `AnswerArtifact` + stream chunk.
- **`PlannerStep.answer_text`** is no longer published. The Planner is not a
  publication authority and bypasses grounding/citation checks. `action=answer`
  always routes through the evidence-based path (`answer_curator → answer_agent
  → critic_agent`). Simple replies use the DialogueAgent direct-answer fast-path
  or the `response.direct_answer` tool.

## Routing

```
planner_loop
  → tool_executor
      → emit_tool_response   # response.* terminal only → save_session
      → planner_loop         # search / lookup / manifest / etc.
  (answer)
  → answer_curator           # evidence only
  → answer_agent
  → critic_agent
  → save_session
```

`_route_after_tool_executor`:
- `plan_state` None / `step_no >= 50` → `answer_curator` (safety)
- last obs is `response.*` + `status=ok` → `emit_tool_response`
- everything else → `planner_loop`

`answer_curator → answer_agent` is now an unconditional edge (the former
`_route_after_answer_curator` direct-response branch is removed).

## Responsibility Map (post-change)

| Component | Layer | Role |
|---|---|---|
| `PlannerAgent` | 1 | decide answer / unsupported / search / clarify — not a publisher |
| `answer_curator` | 2 | observations → `EvidenceBundle` (evidence only) |
| `emit_tool_response` | 3 | publish `response.*` terminal text (already-final) |
| `emit_direct_answer` | 3 | publish DialogueAgent direct-answer text |
| `emit_meta_answer` / `emit_children_list` | 3 | publish session-cache fast-path text |
| `emit_*_clarify` / `emit_internal_error` | 3 | publish clarification / error text |
| `AnswerAgent` + `CriticAgent` | 3 | NTIS-evidence-grounded answer publication path |

Two publication paths, by design:
- **already-final text** (`response.*`, dialogue direct, meta/children, clarify,
  error) → `emit_*` nodes.
- **evidence-grounded answer** → `AnswerAgent` (generate) + `CriticAgent`
  (validate citations/grounding, publish references).

## `response.*` is a terminal intent tool, not an evidence tool

`response.direct_answer` / `response.unsupported` produce a finished `final_text`
in their handler. `ToolExecutor` wrapping them as an `Observation` is fine, but
the curator must not publish that observation. `emit_tool_response` owns it.

## Notes / Residual

- `PlannerStep.answer_text` field is retained in the contract (prompt already
  guides `null`) but is now inert — no consumer publishes it. Removing the field
  entirely is a follow-up (touches `PlannerStep`, `_build_step_from_llm`,
  prompt schema).
- `save_session` treats `emit_tool_response` like the other `emit_*` nodes
  (no `evidence_bundle` → `view=None`, non-publishing direct answer).

## Consequences

- `answer_curator` has a single, name-accurate responsibility.
- New direct-response kinds extend the Publication layer, not the curator.
- Planner answer_text can no longer reach users unvalidated.
- Resolves ADR-0025 Issues 3 and 4.
