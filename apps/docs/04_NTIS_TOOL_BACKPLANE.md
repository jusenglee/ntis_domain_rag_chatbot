# NTIS Tool Backplane

The NTIS vector DB is attached to the agent as a tool backplane.

Use the MCP analogy: the agent can call tools exposed by the backplane when it
needs external NTIS evidence. The backplane does not decide every conversation
turn and does not force RAG retrieval.

## Tool Catalog

Source: `apps/pipeline/tools/registry.py`

The default registry currently contains nine tools. The Planner sees only the
intent-level interface; the SearchRouter resolves collection/strategy/filters
internally from `EntityResolution` (ADR-0022 — no DB schema in the Planner args):

| Tool | Purpose |
| --- | --- |
| `search` | general NTIS search; collection/strategy/filters auto-routed from session context |
| `search.detail` | single-item detail using session identifiers (or query fallback) |
| `search.stats` | grouped counts / stat summaries (`axis`: year / org / type) |
| `lookup.person_by_name` | resolve a person name to NTIS candidates |
| `lookup.org_by_name` | resolve an organization name to NTIS candidates |
| `manifest.get_item` | resolve a visible list rank to item identifiers |
| `manifest.filter` | filter current visible manifest |
| `response.direct_answer` | direct answer without NTIS retrieval (terminal intent tool) |
| `response.unsupported` | honest refusal for unsupported requests (terminal intent tool) |

`response.*` results are published by the `emit_tool_response` node, not the
evidence curator (ADR-0024).

## Search Execution

Source: `apps/pipeline/search_agent.py`

`SearchAgent` receives only `SearchTask` and returns `SearchResult`.

It may execute:

- exact lookup by identifier axis,
- hybrid search,
- subject-anchored hybrid search,
- aggregate scroll and Python grouping.

When no domain target is resolved (a topic query with no subject/identifier),
the `search` tool runs `project` + `perf` in parallel and merges results
(ADR-0022). `applied_context` (searched collections, filters) is returned to the
Planner so it can diagnose zero-hit causes.

It does not reinterpret user intent.
It does not fix Planner mistakes by reading raw question text.
It does not bypass contract fields.

## Collections

Current collection mapping in `build_search_task`:

| Target | Collections |
| --- | --- |
| `project` | `ntis_project_v1` |
| `perf` | `ntis_perf_v1` |
| `people` | `ntis_project_v1`, `ntis_perf_v1` |
| `org` | `ntis_project_v1`, `ntis_perf_v1` |
| `support` | `ntis_supports` |

`settings.py` also has an allowlist default containing `ntis_supports_v1`.
Do not assume those names are interchangeable without checking the retrieval
code path in use.

## Canonical Boundary

The NTIS tool layer must emit `CanonicalEvidence` before answer generation.

Allowed evidence fields:

- identity,
- source type,
- NTIS tag,
- structured ids,
- title,
- summary,
- facts,
- roles,
- child entities,
- provenance,
- score,
- snapshot rank.

Do not expose raw Qdrant payloads as prompt text.

## When Not To Use NTIS Tools

The agent should not call NTIS tools for:

- greetings,
- chatbot capability explanation,
- "what can you do?" style meta turns,
- ambiguous turns that require clarification first,
- unsupported requests such as HR roles, contact details, live facts, general
  web knowledge, or user-provided fact injection.

For those cases use `response.direct_answer`, `response.unsupported`, or
clarification.
