# NTIS Agent Chatbot Documentation

Last rewritten: 2026-05-27

This project is an autonomous conversational agent chatbot.

The attached NTIS vector database is not the product boundary. It is a tool
backplane, similar in role to an MCP server: the agent may call it when the
user's question requires NTIS evidence, and it may avoid it when direct
interaction, clarification, or honest refusal is the better action.

## Operating Model

- User interaction is agent-first. The system decides whether to answer,
  clarify, refuse, or call a tool.
- RAG is one available capability, not a mandatory path for every turn.
- NTIS data is used for R&D projects, research outputs, participating people,
  participating organizations, and support/manual content.
- External facts, live news, HR roles, contact information, and user-injected
  "remember this" facts are outside the current evidence boundary.
- All evidence that reaches answer generation must be canonicalized into
  `CanonicalEvidence`; raw Qdrant payloads are not a prompt contract.

## Source Of Truth

The code is currently ahead of older design notes. These documents describe the
current implementation, not historic intent.

| Area | Primary source |
| --- | --- |
| Runtime assembly | `apps/api/runtime.py` |
| HTTP/SSE routes | `apps/api/routes.py` |
| Shared nodes / deps | `apps/pipeline/agent_workflow.py` |
| Agentic graph (live) | `apps/pipeline/agentic_workflow.py` |
| Agent contracts | `apps/pipeline/agents/contracts.py` |
| Search contracts | `apps/pipeline/contracts.py` |
| Planner loop | `apps/pipeline/agents/planner_agent.py` |
| Tool registry | `apps/pipeline/tools/registry.py` |
| NTIS search execution | `apps/pipeline/search_agent.py` |
| Session state | `apps/pipeline/agents/session_state.py` |

## Document Set

- [00_ONBOARDING.md](00_ONBOARDING.md): quick map for maintainers.
- [01_ARCHITECTURE.md](01_ARCHITECTURE.md): current system architecture.
- [02_CONTRACTS_AND_RULES.md](02_CONTRACTS_AND_RULES.md): contracts and hard rules.
- [03_AGENTIC_RUNTIME.md](03_AGENTIC_RUNTIME.md): Planner, tool loop, critic routing.
- [04_NTIS_TOOL_BACKPLANE.md](04_NTIS_TOOL_BACKPLANE.md): NTIS vector DB as a tool backplane.
- [05_API_AND_STREAMING.md](05_API_AND_STREAMING.md): HTTP response and SSE surface.
- [06_OPERATIONS_AND_ENV.md](06_OPERATIONS_AND_ENV.md): runtime toggles and operational notes.
- [07_VALIDATION_AND_REVIEW.md](07_VALIDATION_AND_REVIEW.md): validation scope and review checklist.
- [ADR/ADR-0018_Three_Layer_Authority_Separation.md](ADR/ADR-0018_Three_Layer_Authority_Separation.md):
  current authority separation decision.
- [ADR/ADR-0019_Agentic_Chatbot_With_NTIS_Tool_Backplane.md](ADR/ADR-0019_Agentic_Chatbot_With_NTIS_Tool_Backplane.md):
  current agentic product decision.
- [ADR/ADR-0020_Unified_Agentic_Pipeline.md](ADR/ADR-0020_Unified_Agentic_Pipeline.md):
  unified pipeline decision (static graph removed; single agentic pipeline).
- [ADR/ADR-0021_Dual_Model_Answer_Output.md](ADR/ADR-0021_Dual_Model_Answer_Output.md):
  dual-model answer output — main Solar (A) + comparison Gemma (B).
- [ADR/ADR-0022_SearchRouter_Abstraction.md](ADR/ADR-0022_SearchRouter_Abstraction.md):
  SearchRouter — Planner sees `search/search.detail/search.stats`; DB schema hidden.
- [ADR/ADR-0023_AdequacyGate_Removal.md](ADR/ADR-0023_AdequacyGate_Removal.md):
  AdequacyGate removed — Planner is sole adequacy authority.
- [ADR/ADR-0024_Publication_Layer_Separation.md](ADR/ADR-0024_Publication_Layer_Separation.md):
  answer_curator is evidence-only; response.* publication moved to emit_tool_response.
- [ADR/ADR-0025_Architecture_Design_Issues.md](ADR/ADR-0025_Architecture_Design_Issues.md):
  design issues register (findings after ADR-0020 and the ADRs that resolved them).

## Current Default Runtime

As implemented in `apps/api/runtime.py`:

- Single agentic pipeline (no `RAG_AGENTIC_MODE` toggle; static graph removed — ADR-0020).
- Adequacy is the Planner's sole authority (no AdequacyGate — ADR-0023).
- `RAG_GROUNDING_CHECKER_ENABLED` defaults to `true`.
- `RAG_DUAL_ANSWER_ENABLED` defaults to `true`: answers are dual-model — main
  Solar (panel A, canonical) + comparison Gemma (panel B, raw). The answer-model
  role is Solar, not Gemma (see ADR-0021).
- Planner and critic grounding use Solar thinking mode by default.
- `RAG_DUAL_ANSWER_ENABLED=false` rolls back to a single Solar answer fanned to
  both panels.

## Non-Negotiable Rules

- Do not mix `pjt_id` and `pjt_no`.
- Do not flatten structured identifiers into retrieval text.
- Do not let raw vector payloads reach prompts or public API responses.
- Do not treat NTIS vector DB access as mandatory for greetings, simple
  capability explanations, clarification, or out-of-domain refusal.
- Do not document a behavior unless a current source path backs it.
