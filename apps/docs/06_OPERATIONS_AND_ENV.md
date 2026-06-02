# Operations And Environment

This document reflects current source defaults as of 2026-05-27.

## Runtime Toggles

| Env var | Default | Effect |
| --- | --- | --- |
| `RAG_AGENTIC_MODE` | `true` | enables agentic graph; `false` uses static seven-agent graph |
| `RAG_DUAL_ANSWER_ENABLED` | `true` | dual-model answer: main Solar (panel A) + comparison Gemma (panel B); `false` = single Solar answer fanned to both panels |
| `RAG_ADEQUACY_GATE_ENABLED` | `true` | enables adequacy judge after tool calls |
| `RAG_GROUNDING_CHECKER_ENABLED` | `true` | enables critic grounding judge |
| `RAG_PLANNER_THINKING_ENABLED` | `true` | enables Solar thinking for Planner pass 1 |
| `RAG_ADEQUACY_THINKING_ENABLED` | `false` | enables Solar thinking for adequacy judge |
| `RAG_CRITIC_THINKING_ENABLED` | `true` | enables Solar thinking for critic grounding |
| `RAG_CRITIC_THREAD_TIMEOUT_SECONDS` | `600` | sync wrapper timeout for critic judge |
| `PIPELINE_SESSION_TTL_SECONDS` | `604800` | KV session TTL, seconds |
| `SOLAR_VLLM_TIMEOUT` | `600.0` | OpenAI-compatible client timeout |

## Rollback

To rollback dynamic planning:

```powershell
$env:RAG_AGENTIC_MODE = "false"
```

This compiles the static seven-agent graph.

To keep agentic mode but remove the extra adequacy LLM call:

```powershell
$env:RAG_ADEQUACY_GATE_ENABLED = "false"
```

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

- `solar_vllm_0`: dialogue/planner/adequacy/critic judge style decisions, and
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
- adequacy verdict and source,
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
