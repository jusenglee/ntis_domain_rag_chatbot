# Onboarding

This repository implements an autonomous agent chatbot with an NTIS evidence
tool attached.

The fastest way to understand the system is to follow one request through the
runtime, graph, tool layer, and response surface.

## Request Path

1. `apps/api/routes.py` receives `/query` or `/query/stream`.
2. `QueryRequest` is converted into `AgentPipelineState`.
3. The compiled graph from `app.state.graph` runs.
4. `apps/api/runtime.py` compiles the single agentic pipeline via
   `build_agentic_pipeline_graph` (ADR-0020 removed the static graph and the
   `RAG_AGENTIC_MODE` toggle).
5. The graph publishes an `AnswerArtifact` and optional references.
6. Routes return JSON or SSE frames.

## Core Mental Model

The chatbot should not behave like a fixed RAG chain.

It should behave like an agent:

- Read the user turn and session state.
- Decide whether the question is within its capability.
- Use NTIS tools only when needed.
- Ask a clarifying question when user intent or target is not actionable.
- Refuse out-of-domain requests honestly.
- Generate evidence-grounded answers only after canonical evidence is available.

## Important Source Files

| Need | File |
| --- | --- |
| Runtime composition | `apps/api/runtime.py` |
| Request/response schema | `apps/api/routes.py` |
| Shared state object | `apps/pipeline/agent_state.py` |
| Shared nodes / deps | `apps/pipeline/agent_workflow.py` |
| Agentic graph (live) | `apps/pipeline/agentic_workflow.py` |
| Planner decisions | `apps/pipeline/agents/planner_agent.py` |
| Tool contracts | `apps/pipeline/tools/contracts.py` |
| Tool catalog | `apps/pipeline/tools/registry.py` |
| NTIS search executor | `apps/pipeline/search_agent.py` |
| Canonical evidence | `apps/pipeline/contracts.py` |
| Session slots | `apps/pipeline/agents/session_state.py` |

## Typical Development Flow

1. Inspect the source path that owns the behavior.
2. Check the matching contract model before changing call sites.
3. Add or adjust focused tests under `tests/pipeline`.
4. Run targeted tests first, then broader tests.
5. Update these docs only for source-backed behavior.

## Current Validation Caveat

This repository currently has no root dependency manifest in the checked source.
The local validation command used during review was:

```powershell
python -m pytest tests/ -q --ignore=tests/integration
```

In the current shell, collection initially failed because the active Python
environment was missing packages such as `langchain_core` and `loguru`. Treat a
passing test report as environment-specific unless the dependency environment is
made reproducible.
