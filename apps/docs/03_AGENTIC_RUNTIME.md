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

## AdequacyGate

Source: `apps/pipeline/agents/adequacy_gate.py`

After each tool call, the adequacy gate judges whether the accumulated
observations can answer the user question.

Deterministic cases:

- no observations -> insufficient
- last observation error -> insufficient
- `response.direct_answer` or `response.unsupported` success -> adequate

Otherwise it uses an LLM judge on summarized observations, not raw evidence.

`RAG_ADEQUACY_GATE_ENABLED` defaults to `true`.
`RAG_ADEQUACY_THINKING_ENABLED` defaults to `false`.

## Answer Curator

Source: `apps/pipeline/agentic_workflow.py`

`node_answer_curator` turns accumulated observations into either:

- final text from `response.*` tools or `PlannerStep.answer_text`, or
- an `EvidenceBundle` for `AnswerAgent`.

Current evidence selection priority is:

1. last successful `search.exact_lookup`
2. last successful `search.aggregate`
3. last successful `search.hybrid`
4. empty

This is not a full multi-observation ranking system yet. It is a priority
selector over accumulated observations.

## AnswerAgent

Source: `apps/pipeline/agents/answer_agent.py`

`AnswerAgent` generates an answer from an `EvidenceBundle`. It should only see
canonical evidence, not raw vector payloads.

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
