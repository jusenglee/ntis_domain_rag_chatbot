# Architecture

The product architecture is agent-first.

The chatbot owns the interaction. NTIS vector search is a callable evidence
tool, not the central workflow identity.

## Runtime Topology

`apps/api/runtime.py` builds shared resources:

- Qdrant clients and embedding models from `apps.retrieval.rag_store`.
- `solar_vllm_0` for dialogue, planning, grounding judgments, and the
  **main answer** (compare panel A).
- `gemma_triton_0` for the **comparison answer** (compare panel B) and the
  `response.*` direct-answer tools.

Answer generation is dual-model by default (`RAG_DUAL_ANSWER_ENABLED=true`): the
main answer (Solar) is the canonical one that `CriticAgent` validates and that
drives `reference.set` and session state; the comparison answer (Gemma) is shown
raw. See [ADR-0021](ADR/ADR-0021_Dual_Model_Answer_Output.md).
- `SearchAgent` as the NTIS vector DB executor.
- `ToolExecutor` and default tool registry.
- LangGraph compiled graph.

## Single Agentic Pipeline

ADR-0020 removed the static graph; there is one pipeline. ADR-0023 removed the
AdequacyGate; adequacy is the Planner's sole authority.

Flow:

```text
load_session
  -> dialogue_agent          (direct_answer / clarification -> emit, no Planner)
  -> entity_resolver         (ask_meta / ask_children -> fast-path emit)
  -> planner_loop
  -> tool_executor
       -> emit_tool_response (last obs = response.* terminal -> publish)
       -> planner_loop       (everything else -> Planner decides answer/refine)
  (answer)
  -> answer_curator          (evidence only)
  -> answer_agent
  -> critic_agent
  -> save_session | answer_agent | emit_clarification | emit_internal_error
```

Direct response tools short-circuit (ADR-0024):

```text
planner_loop -> response.* tool -> tool_executor -> emit_tool_response -> save_session
```

The `response.*` handler already produced the final text, so `emit_tool_response`
(Publication layer) publishes it directly; `AnswerAgent` and `CriticAgent` are
skipped. `answer_curator` is evidence-only and always flows to `answer_agent`.

## Authority Separation

The current implementation keeps three layers separate:

1. Interaction and intent authority:
   `DialogueAgent` and `PlannerAgent` decide what should happen next.
   The Planner is the single adequacy authority (ADR-0023).
2. Evidence authority:
   `ToolExecutor`, NTIS tools, and `SearchAgent` retrieve or transform data but
   do not reinterpret the user.
3. Publication authority:
   `AnswerAgent` drafts, `CriticAgent` validates, and terminal emit nodes decide
   what reaches the user.

## NTIS Vector DB Role

The NTIS vector DB is a tool backplane.

It is used through tools such as `search`, `search.detail`, and `search.stats`
(ADR-0022 — the Planner does not see DB schema; the SearchRouter resolves
collection/strategy/filters internally). It supplies canonical evidence for
NTIS-backed answers. It does not define all conversational behavior.

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
