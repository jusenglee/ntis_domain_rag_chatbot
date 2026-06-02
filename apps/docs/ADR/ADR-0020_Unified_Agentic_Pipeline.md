# ADR-0020: Unified Agentic Pipeline

Status: accepted, 2026-06-02

## Context

Two separate LangGraph pipelines have been maintained in parallel since ADR-0019:

- **Static graph** (`agent_workflow.py`): fixed chain —
  `DialogueAgent → EntityResolver → SearchPlanner → RetrievalAgent → EvidenceCurator → AnswerAgent → CriticAgent`
- **Agentic graph** (`agentic_workflow.py`): ReAct loop —
  `PlannerAgent → ToolExecutor → AdequacyGate → answer_curator → AnswerAgent → CriticAgent`

The dual-pipeline strategy created several structural problems.

### Problems

**1. DialogueAgent and EntityResolver absent from the agentic graph.**

The agentic graph skips both agents. `PlannerAgent` implicitly absorbs intent
classification and NER inside its Pass 2 args composition. This means:

- The 10-kind intent taxonomy (`ask_search`, `ask_detail`, `ask_meta`,
  `ask_children`, `ask_similar`, `refine_previous`, `compare`, `stats`,
  `direct_answer`, `clarification`) is not preserved beyond the Planner.
- `AnswerAgent` receives a synthetic `DialogueIntent(kind="ask_search")`
  regardless of the actual intent — subject personalization and template
  selection degrade.
- `EntityResolver`'s deterministic name-lookup (person_no / org_id resolution,
  manifest-rank → identifier mapping) does not run.

**2. AdequacyGate and PlannerAgent hold duplicate authority.**

The Planner decides `action=answer` when it judges evidence sufficient.
`AdequacyGate` runs after every tool execution and can override a Planner
`call_tool` decision with `verdict=adequate`. Two LLM calls compete for the
same judgment.

**3. `node_answer_curator` mixes routing and data roles.**

It both decides the response path (direct vs. evidence-based) and synthesizes
the `EvidenceBundle`. The routing decision belongs to Layer 1
(Interaction Authority per ADR-0018); the evidence selection belongs to
Layer 2 (Evidence Authority).

**4. Dual-pipeline maintenance cost.**

Bug fixes, prompt changes, and grounding improvements must be applied to both
graphs. Behavioral divergence between paths has grown over time.

## Decision

Replace both graphs with a single unified agentic pipeline.
Delete the static graph and all legacy components. Remove `RAG_AGENTIC_MODE`
toggle.

## New Pipeline

```
load_session
  → dialogue_agent          (Layer 1 — intent classification, 2-pass LLM)
      ├─ direct_answer       → emit_direct_answer → save_session
      ├─ clarification       → emit_clarification → save_session
      └─ ask_* / stats / compare / refine
          → entity_resolver  (Layer 2 — deterministic ID resolution, no LLM)
              ├─ clarification_needed → emit_clarification → save_session
              └─ resolved
                  → planner_loop     (Layer 1 — ReAct tool selection, 2-pass LLM)
                      ├─ call_tool   → tool_executor (Layer 2) → planner_loop
                      └─ answer
                          → answer_curator  (Layer 2 — evidence selection only)
                              → answer_agent   (Layer 3 — generation, LLM streaming)
                                  → critic_agent (Layer 3 — validation + manifest)
                                      ├─ publish        → save_session
                                      ├─ repair_answer  → answer_agent (1× limit)
                                      ├─ clarify        → emit_clarification → save_session
                                      └─ internal_error → emit_internal_error → save_session
```

## Agent Responsibilities

| Agent | Layer | Decides | Output |
|---|---|---|---|
| `DialogueAgent` Pass 1 | 1 | intent kind (10-way) | `IntentClassification` |
| `DialogueAgent` Pass 2 | 1 | kind-specific slots | `SlotExtraction` |
| `EntityResolverAgent` | 2 | person_no / org_id / manifest-rank → ID | `EntityResolution` |
| `PlannerAgent` Pass 1 | 1 | which tool, or answer | tool name (1 value) |
| `PlannerAgent` Pass 2 | 1 | tool args (NER from question) | args dict |
| `ToolExecutor` | 2 | execute tool | `Observation` |
| `answer_curator` | 2 | select evidence from observations | `EvidenceBundle` |
| `AnswerAgent` | 3 | generate answer text | `AnswerDraft` |
| `CriticAgent` | 3 | validate + publish manifest | `GuardDecision` |

## Key Changes from ADR-0019

### DialogueAgent restored to agentic path

`DialogueAgent` runs before `PlannerAgent`. Its `DialogueIntent` is stored in
state and passed through to `AnswerAgent` unchanged. The synthetic
`DialogueIntent(kind="ask_search")` fallback in `node_answer_curator` is
removed.

### EntityResolver restored to agentic path

`EntityResolverAgent` runs after `DialogueAgent`. Its `EntityResolution` is
stored in `AgentPipelineState.entity_resolution` and included in the session
summary passed to `PlannerAgent`. Pass 2 uses resolved `person_no` / `org_id`
instead of raw `subject_name`.

### AdequacyGate — superseded by ADR-0023

This ADR originally retained `AdequacyGate` as a second-opinion judge. In
practice the gate's LLM judge over-judged successful searches as "insufficient"
and forced re-search churn, competing with the Planner for the same decision.
**ADR-0023 removed `AdequacyGate` entirely** — adequacy is now the Planner's
sole authority, with `tool_executor` routing deterministically to
`answer_curator` (response.* terminal) or `planner_loop` (everything else).

### `answer_curator` scope reduced

`node_answer_curator` is restricted to evidence selection only (Layer 2).
Direct-response routing is handled upstream by the `dialogue_agent` router and
the `response.*` tool path in `planner_loop`. The curator no longer emits
streaming tokens or sets `final_answer_text` directly.

### Static graph deleted

`agent_workflow.py` static graph builder and all related node functions that
are not shared with the agentic graph are deleted. `RAG_AGENTIC_MODE`
environment variable is removed.

## Implementation Phases

### Phase 1 — Add DialogueAgent node to agentic graph
- Add `_make_node_dialogue(deps)` to `agentic_workflow.py`.
- Add `_route_after_dialogue()` router.
- Wire: `load_session → dialogue_agent → entity_resolver / emit_*`.

### Phase 2 — Add EntityResolver node to agentic graph
- Add `_make_node_entity_resolver(deps)` to `agentic_workflow.py`.
- Add `_route_after_entity_resolver()` router.
- Wire: `dialogue_agent → entity_resolver → planner_loop`.

### Phase 3 — Planner uses EntityResolution
- Include `state.entity_resolution` in `_summarize_session()`.
- Pass resolved identifiers to Planner Pass 2 context.

### Phase 4 — Fix answer_curator
- Remove synthetic `DialogueIntent` fallback.
- Remove direct streaming emission from `node_answer_curator`.
- Keep evidence selection logic only.

### Phase 5 — Delete static graph and toggle
- Delete static graph builder and static-only node functions from
  `agent_workflow.py`. Keep shared nodes (`node_load_session`,
  `node_save_session`, `node_emit_clarification`, `node_emit_internal_error`,
  `_make_node_answer`, `_make_node_critic`).
- Remove `RAG_AGENTIC_MODE` check from `apps/api/routes.py`.
- Remove static-only deps fields from `AgentPipelineDeps` or mark optional.

## Files Changed

| File | Phase | Change |
|---|---|---|
| `apps/pipeline/agentic_workflow.py` | 1–4 | node additions, routing, curator fix |
| `apps/pipeline/agents/planner_agent.py` | 3 | `_summarize_session` extension |
| `apps/pipeline/agent_workflow.py` | 5 | delete static builder, keep shared nodes |
| `apps/api/routes.py` | 5 | remove `RAG_AGENTIC_MODE` branch |

## Consequences

- Single pipeline to maintain.
- `AnswerAgent` receives real `DialogueIntent` — template selection and subject
  personalization are restored.
- `EntityResolver` name-lookup runs on every search turn — resolved IDs reach
  `ToolExecutor` args.
- LLM calls per turn increase by 2 (DialogueAgent 2-pass) for search-type
  turns. Direct-answer and clarification turns are unaffected.
- Static graph and `RAG_AGENTIC_MODE` are removed; rollback requires a
  code-level revert.
