# Operations And Environment

This document reflects current source defaults as of 2026-05-27.

## Runtime Toggles

| Env var | Default | Effect |
| --- | --- | --- |
| `RAG_DUAL_ANSWER_ENABLED` | `true` | dual-model answer: main Solar (panel A) + comparison Gemma (panel B); `false` = single Solar answer fanned to both panels |
| `RAG_GROUNDING_CHECKER_ENABLED` | `true` | enables critic grounding judge |
| `RAG_PLANNER_THINKING_ENABLED` | `true` | enables Solar thinking for Planner pass 1 |
| `RAG_CRITIC_THINKING_ENABLED` | `true` | enables Solar thinking for critic grounding |
| `RAG_CRITIC_THREAD_TIMEOUT_SECONDS` | `600` | sync wrapper timeout for critic judge |
| `PIPELINE_SESSION_TTL_SECONDS` | `604800` | KV session TTL, seconds |
| `SOLAR_VLLM_TIMEOUT` | `600.0` | OpenAI-compatible client timeout |

> Removed toggles: `RAG_AGENTIC_MODE` (ADR-0020 — single agentic pipeline, no
> static graph), `RAG_ADEQUACY_GATE_ENABLED` / `RAG_ADEQUACY_THINKING_ENABLED`
> (ADR-0023 — AdequacyGate removed, Planner is sole adequacy authority).
> Rollback for these is now a code-level revert.

## Rollback

To reduce thinking latency:

```powershell
$env:RAG_PLANNER_THINKING_ENABLED = "false"
$env:RAG_CRITIC_THINKING_ENABLED = "false"
```

To rollback to a single answer (no comparison panel):

```powershell
$env:RAG_DUAL_ANSWER_ENABLED = "false"
```

Only the main (Solar) answer is generated; it is fanned out to both compare
panels.

## Model Roles

Current runtime construction:

- `solar_vllm_0`: dialogue/planner/critic judge style decisions, and
  the **main answer** (compare panel A) — the draft `CriticAgent` validates and
  that drives `reference.set` and session state.
- `gemma_triton_0`: the **comparison answer** (compare panel B, raw — not
  validated, not persisted) and the `response.*` direct-answer tools.

The answer-model role moved from Gemma to Solar; see
[ADR-0021](ADR/ADR-0021_Dual_Model_Answer_Output.md).

Thinking mode should stay selective. It is useful for action selection and
grounding judgment, but not for every NER or args composition call.

## Logs To Watch

Agentic flow logs use `[agentic_trace]`.

Useful events:

- planner step action/tool/reason,
- tool result status and latency,
- tool_executor routing (response.* terminal vs. back to planner),
- answer curator selected path,
- critic decision,
- plan summary,
- session save status.

Session save logs include:

- whether the turn published,
- evidence view,
- current subject presence,
- manifest presence,
- focused detail presence,
- KV save result,
- total latency.

## KV Storage

Pipeline sessions are stored under:

```text
pipeline:v1:{conversation_id}:session
```

The backend is `KVStore`; runtime tries Redis first and falls back to file KV.

## Dependency Environment

The checked tree currently does not include a root dependency manifest. Before
claiming full validation, make the Python environment explicit or capture the
installed package set used for the test run.
