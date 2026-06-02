# Architecture

The product architecture is agent-first.

The chatbot owns the interaction. NTIS vector search is a callable evidence
tool, not the central workflow identity.

## Runtime Topology

`apps/api/runtime.py` builds shared resources:

- Qdrant clients and embedding models from `apps.retrieval.rag_store`.
- `solar_vllm_0` for dialogue, planning, adequacy, grounding judgments, and the
  **main answer** (compare panel A).
- `gemma_triton_0` for the **comparison answer** (compare panel B) and the
  `response.*` direct-answer tools.

Answer generation is dual-model by default (`RAG_DUAL_ANSWER_ENABLED=true`): the
main answer (Solar) is the canonical one that `CriticAgent` validates and that
drives `reference.set` and session state; the comparison answer (Gemma) is shown
raw. See [ADR-0021](ADR/ADR-0021_Dual_Model_Answer_Output.md).
- `SearchAgent` as the NTIS vector DB executor.
- `ToolExecutor` and default tool registry when agentic mode is enabled.
- `AdequacyGate` when `RAG_ADEQUACY_GATE_ENABLED` is enabled.
- LangGraph compiled graph.

## Two Graphs

### Agentic Graph

Default when `RAG_AGENTIC_MODE` is not set or is truthy.

Flow:

```text
load_session
  -> planner_loop
  -> tool_executor
  -> adequacy_gate
  -> planner_loop | answer_curator
  -> answer_agent
  -> critic_agent
  -> save_session | answer_agent | emit_clarification | emit_internal_error
```

Direct response tools can short-circuit:

```text
planner_loop -> response.* tool -> adequacy_gate -> answer_curator -> save_session
```

In that path the output is already final text, so `AnswerAgent` and
`CriticAgent` are skipped.

### Static Seven-Agent Graph

Enabled with `RAG_AGENTIC_MODE=false`.

Flow:

```text
load_session
  -> dialogue_agent
  -> entity_resolver
  -> search_planner
  -> retrieval_agent
  -> evidence_curator
  -> answer_agent
  -> critic_agent
  -> save_session | repair | clarify | error
```

This graph remains the rollback path and preserves deterministic responsibility
boundaries.

## Authority Separation

The current implementation keeps three layers separate:

1. Interaction and intent authority:
   `DialogueAgent`, `PlannerAgent`, and `AdequacyGate` decide what should
   happen next.
2. Evidence authority:
   `ToolExecutor`, NTIS tools, and `SearchAgent` retrieve or transform data but
   do not reinterpret the user.
3. Publication authority:
   `AnswerAgent` drafts, `CriticAgent` validates, and terminal emit nodes decide
   what reaches the user.

## NTIS Vector DB Role

The NTIS vector DB is a tool backplane.

It is used through tools such as `search.hybrid`, `search.exact_lookup`, and
`search.aggregate`. It supplies canonical evidence for NTIS-backed answers. It
does not define all conversational behavior.

The agent may avoid NTIS access for:

- greetings,
- capability explanations,
- simple meta answers,
- clarification,
- unsupported or out-of-domain requests.

## Session Model

The agentic pipeline uses `SessionState` with three conceptual slots:

- `current_subject`: person or organization anchor.
- `published_manifest`: the list visible to the user in a previous turn.
- `focused_detail`: the most recent detailed entity, including cached evidence
  fields for follow-up answers.

The persistence adapter still writes through `SessionMemory.current_context`.
This means in-memory state is richer than the current KV storage format. See
[02_CONTRACTS_AND_RULES.md](02_CONTRACTS_AND_RULES.md) for the exact constraint.

## Architecture Diagram

See [architecture_flow.mermaid](architecture_flow.mermaid).
