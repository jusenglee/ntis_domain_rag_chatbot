# ADR-0018: Three-Layer Authority Separation

Status: accepted, refreshed 2026-05-27

## Context

The system is no longer a fixed "question -> retrieval -> answer" chain.

It is an autonomous agent chatbot. The user interacts with the agent, and the
agent may use the NTIS vector DB as a tool when NTIS evidence is required.

This makes authority boundaries more important, not less important.

## Decision

Keep three authority layers separate.

## Layer 1: Interaction Authority

Owners:

- `DialogueAgent`
- `PlannerAgent` (also the sole adequacy authority — ADR-0023)
- routing functions in LangGraph

Responsibilities:

- classify the turn,
- decide if NTIS evidence is needed,
- choose direct answer, clarification, refusal, or tool call,
- decide if accumulated observations are adequate.

Not allowed:

- fabricate NTIS evidence,
- mutate retrieval results,
- collapse identifiers into unstructured text.

## Layer 2: Evidence Authority

Owners:

- `ToolExecutor`
- tool handlers under `apps/pipeline/tools`
- `SearchAgent`
- canonical retrieval modules under `apps/pipeline/retrieval`

Responsibilities:

- execute the chosen tool,
- query NTIS vector DB or session manifest,
- normalize data to `CanonicalEvidence`,
- return structured `Observation` or `SearchResult`.

Not allowed:

- reinterpret the user's intent,
- silently swap identifier axes,
- pass raw Qdrant payloads to answer generation.

## Layer 3: Publication Authority

Owners:

- `AnswerAgent`
- `CriticAgent`
- grounding checker
- terminal emit nodes
- API serializer

Responsibilities:

- produce answer text from canonical evidence,
- validate grounding,
- publish references,
- repair, clarify, or error out when needed.

Not allowed:

- publish ungrounded NTIS claims,
- hide an internal error as a confident answer,
- invent references.

## Consequences

- The NTIS vector DB remains a tool, not the controlling workflow.
- RAG can be skipped for direct interaction and unsupported requests.
- Search contracts must stay typed and explicit.
- Review must check boundary violations before style or refactor preferences.
