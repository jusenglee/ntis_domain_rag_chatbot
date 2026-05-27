# ADR-0019: Agentic Chatbot With NTIS Tool Backplane

Status: accepted, refreshed 2026-05-27

## Context

The desired product is a chatbot agent that interacts with users autonomously.

The NTIS vector DB is available, but it should be treated like a tool server.
The agent should call it only when the user request requires NTIS-backed
evidence.

## Decision

Make the agentic graph the default runtime and expose NTIS retrieval through a
tool catalog.

## Current Implementation

`RAG_AGENTIC_MODE` defaults to `true`.

When enabled, runtime wires:

- `PlannerAgent`
- `ToolExecutor`
- default registry with search, lookup, manifest, and response tools
- optional `AdequacyGate`
- existing `AnswerAgent`
- existing `CriticAgent`

The Planner uses two passes:

1. action/tool selection with thinking mode,
2. args composition without thinking mode.

The adequacy gate separates "did the result answer the question?" from tool
selection.

## Why

The previous RAG-first framing caused design drift:

- every turn looked like retrieval,
- unsupported questions were over-searched,
- tool args and filter contracts drifted,
- the agent did not clearly know when to stop searching.

The new framing makes the agent responsible for interaction and makes NTIS data
a capability it can call.

## Accepted Tradeoffs

- Agentic mode adds LLM calls and latency.
- Direct response tools can bypass `AnswerAgent` and `CriticAgent`; this must
  stay restricted to direct/unsupported response paths.
- Adequacy gate adds one optional judge call after tool execution.
- A static seven-agent graph remains as rollback.

## Required Future Work

- Make the dependency environment reproducible.
- Decide whether `response.unsupported` needs a distinct public `answer_kind`.
- Decide whether direct `PlannerStep.answer_text` should be allowed to bypass
  critic, or only `response.*` tool outputs should.
- Replace priority-based evidence selection with a real multi-observation
  evidence selector if multi-tool plans become common.
- Upgrade KV storage if true three-slot `SessionState` persistence is required.
