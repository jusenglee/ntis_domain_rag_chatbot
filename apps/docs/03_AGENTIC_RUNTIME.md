# Agentic Runtime

Agentic mode is the current default. It makes the chatbot decide turn by turn
whether to call NTIS tools, answer directly, clarify, or refuse.

## PlannerAgent

Source: `apps/pipeline/agents/planner_agent.py`

`PlannerAgent.decide_next` is a two-pass planner.

### Pass 1: Action And Tool

Pass 1 chooses:

- `action`
- optional `tool`
- `reason`
- `confidence`
- optional clarification/direct answer fields

It sees only tool names and one-line descriptions. It does not see full args
schemas. This keeps the main decision focused on capability, domain fit, and
next action.

Thinking mode:

- controlled by `RAG_PLANNER_THINKING_ENABLED`
- default: enabled
- call kwargs when enabled: `disable_thinking=False`,
  `reasoning_effort="medium"`, `include_reasoning=False`

### Pass 2: Tool Args

Pass 2 runs only for `action="call_tool"` and only if the selected tool exists
in the catalog.

It sees only the selected tool's schema and fills args. Thinking is intentionally
not enabled in this pass.

Fallbacks:

- unknown tool from Pass 1 -> `answer` fallback, no Pass 2 call.
- Pass 2 failure -> preserve selected tool and use `args={}`.

## Tool Execution

Source: `apps/pipeline/tools/contracts.py`

`ToolExecutor` dispatches the chosen tool and wraps the result into an
`Observation`.

Failures are not thrown through the graph. They become:

```text
Observation(status="error", error_code=..., error_message=...)
```

The Planner sees prior observations in the next step.

## Adequacy — Planner single authority (ADR-0023)

`AdequacyGate` was removed in ADR-0023. There is no separate adequacy judge.
After each tool call, `_route_after_tool_executor` routes deterministically:

- `plan_state` None or `step_no >= 50` -> `answer_curator` (safety)
- last observation is `response.direct_answer` / `response.unsupported` and
  `status=ok` -> `answer_curator` (direct-answer terminal)
- everything else (search results, errors) -> `planner_loop`

Control returns to the Planner, which decides `answer` (sufficient), another
`call_tool` (refine), or `response.unsupported` (out of scope) per its
system-prompt rule #3, using the observations and `applied_context` (ADR-0022).
Termination is bounded by the duplicate_call guard, `max_steps` (8), and the
`step_no >= 50` safety branch.

## Answer Curator

Source: `apps/pipeline/agentic_workflow.py`

`node_answer_curator` is evidence-only (ADR-0024): it turns accumulated
observations into an `EvidenceBundle` and always flows to `AnswerAgent`.
`response.*` terminal-tool publication is handled by `emit_tool_response`, not
the curator; `PlannerStep.answer_text` is not published.

Current evidence selection priority is:

1. last successful `search.detail`
2. last successful `search.stats`
3. last successful `search` (with fallback to the prior non-empty `search`)
4. empty

(Old tool names `search.exact_lookup` / `search.aggregate` / `search.hybrid` are
still recognized for backward compatibility — ADR-0022.) This is not a full
multi-observation ranking system yet; it is a priority selector over accumulated
observations.

## AnswerAgent

Source: `apps/pipeline/agents/answer_agent.py`

`AnswerAgent` generates an answer from an `EvidenceBundle`. It should only see
canonical evidence, not raw vector payloads.

### Dual-model output (A=Solar main, B=Gemma comparison)

`node_answer` (`apps/pipeline/agent_workflow.py`) runs two `AnswerAgent`
instances over the *same* `EvidenceBundle`/`DialogueIntent`:

- **Main = Solar** (`deps.answer_agent`, `solar_vllm_0`): streams chunks tagged
  `model_key="solar"` (frontend panel A) and is stored as `state.answer_draft`.
  This is the canonical draft `CriticAgent` validates; it drives `repair_answer`,
  `reference.set`, and session writes.
- **Comparison = Gemma** (`deps.answer_agent_secondary`, `gemma_triton_0`):
  streams chunks tagged `model_key="gemma"` (frontend panel B) and is stored as
  `state.secondary_answer_draft`. It is shown raw — no grounding/citation gate,
  no repair, not persisted to session.

Both run concurrently (`asyncio.gather`). The comparison draft is generated only
on the first pass over a non-empty bundle; a `repair_answer` re-pass regenerates
the main (Solar) draft only, and a comparison failure is isolated (it never
blocks the main answer). `[N]` citations reference the shared evidence bundle, so
one `reference.set` applies to both panels.

Disabled with `RAG_DUAL_ANSWER_ENABLED=false`: only the main (Solar) answer is
generated and is fanned out to both panels. The `solar_vllm_0`/`gemma_triton_0`
binding and the streaming lane contract are described in
[ADR-0021](ADR/ADR-0021_Dual_Model_Answer_Output.md) and
[05_API_AND_STREAMING](05_API_AND_STREAMING.md).

## CriticAgent

Sources:

- `apps/pipeline/agents/critic_agent.py`
- `apps/pipeline/agents/grounding/llm_judge.py`

`CriticAgent` can return:

- `publish`
- `repair_answer`
- `clarify`
- `internal_error`

In agentic mode, critic routing now honors those decisions:

- `publish` -> `save_session`
- `repair_answer` -> `answer_agent`
- `clarify` -> `emit_clarification`
- other/none -> `emit_internal_error`

Grounding checker is enabled by default through
`RAG_GROUNDING_CHECKER_ENABLED=true`.

Critic thinking mode:

- controlled by `RAG_CRITIC_THINKING_ENABLED`
- default: enabled
- max token budget: 512 when enabled, 256 when disabled
- thread timeout default: `RAG_CRITIC_THREAD_TIMEOUT_SECONDS=600`

## Direct Response Tools

Source: `apps/pipeline/tools/response_tools.py`

`response.direct_answer` is for greetings, capability explanations, and simple
agent interactions where NTIS evidence is not needed.

`response.unsupported` is for honest refusal when the request is outside NTIS
R&D evidence scope.

These are tools so the Planner can explicitly decide "do not retrieve".
