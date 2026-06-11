# ADR-0023: AdequacyGate Removal — Planner as Single Adequacy Authority

Status: accepted, 2026-06-02

## Context

ADR-0019/0020 kept `AdequacyGate` as a separate LLM judge that ran after every
tool execution to decide whether accumulated observations were sufficient to
answer:

```
planner_loop → tool_executor → adequacy_gate (LLM judge) → planner_loop / answer_curator
```

Operational logs exposed a structural defect. For the query
"최근 LLM 관련 연구동향은?":

```
step1: search → 10 results (status=ok)
AdequacyGate (LLM judge): "insufficient" (over-judged results as unrelated)
step2: Planner → re-issued the SAME search (no better move available)
       → duplicate_call guard → forced termination
```

`AdequacyGate` and `PlannerAgent` were two judges competing over the same
decision ("is this enough to answer?"). The gate's over-eager "insufficient"
verdict forced wasteful re-search loops. This is ADR-0025 Issue 5 and the
efficiency review's priority-1 item.

## Decision

Remove `AdequacyGate` (the LLM judge) entirely. Make `PlannerAgent` the single
authority for adequacy. Replace the gate's routing with a deterministic router.

## What the Gate Actually Provided

A multi-agent verification workflow (8 query types, adversarial trace) confirmed
the gate's only *valuable* behavior was deterministic routing, not the LLM
judgment:

| Gate behavior | Value | Replacement |
|---|---|---|
| LLM judge "adequate/insufficient" | **harmful** (over-judges → churn) | removed; Planner decides |
| `response.*` obs → adequate → answer_curator | useful shortcut | new deterministic router |
| error obs → insufficient → planner_loop | useful retry | absorbed by "else → planner_loop" + Planner rule#6 |
| `response.*` final_text delivery | **never owned by gate** | already in `answer_curator` |

## New Routing

`_route_after_tool_executor` becomes deterministic:

```python
def _route_after_tool_executor(state):
    plan_state = state.plan_state
    if plan_state is None or plan_state.step_no >= 50:
        return "answer_curator"          # safety
    last_obs = plan_state.last_observation()
    if (last_obs and last_obs.status == "ok"
            and last_obs.tool in {"response.direct_answer", "response.unsupported"}):
        return "answer_curator"          # 직접응답 종결 (final_text)
    return "planner_loop"                # 그 외 → Planner가 충분성 판단
```

After every search, control returns to `planner_loop`. The Planner sees the
observations (now including `applied_context` from ADR-0022) and decides:
`answer` (sufficient), another `call_tool` (refine), or `response.unsupported`
(genuinely out of scope) per its system-prompt rule #3.

## Termination Safety

With the gate gone, termination relies on the Planner emitting `action=answer`,
backed by three deterministic guards:

- **duplicate_call guard** — identical tool+args → forced termination.
- **max_steps** (default 8) — Planner forces `answer`.
- **step_no >= 50** — router safety to `answer_curator`.
- **evidence fallback** — `_select_evidences_for_answer` uses the best prior
  successful search even if the last step returned 0, so a max_steps
  termination still yields a real answer, not `no_result`.

## Verification (workflow, 8 query types)

| Query type | Regression risk | LLM Δ vs before |
|---|---|---|
| direct_answer / ask_meta / ask_children / unsupported | none | 0 |
| search_hit | low | 0 (neutral) |
| detail | low | -1 |
| topic_multicol | low | -1 |
| search_multistep (2 searches) | low | **-2** |

No high-risk regressions. `response.*` direct-answer path and error→replan
behavior are preserved. Net LLM savings concentrate in the churn cases the gate
itself caused; happy-path is roughly neutral (gate judge call replaced by one
Planner answer-decision call).

## Residual Risk (accepted)

Removing the gate removes the *deterministic* "adequate → answer" shortcut;
adequacy now depends on Planner judgment quality. An args-tweaking Planner could
churn up to `max_steps`. Mitigated by the guards above and the evidence
fallback. Accepted in favor of single-authority simplicity and the elimination
of gate-induced over-search.

## Removed

- `apps/pipeline/agents/adequacy_gate.py` (entire file)
- `tests/pipeline/test_adequacy_gate_phase5.py`
- `_make_node_adequacy_gate`, `_route_after_adequacy_gate` (agentic_workflow.py)
- `adequacy_gate` node + its conditional edges
- `AdequacyGate` injection in `runtime.py`
- `adequacy_gate` field in `AgentPipelineDeps`
- `RAG_ADEQUACY_GATE_ENABLED`, `RAG_ADEQUACY_THINKING_ENABLED` env vars

## Consequences

- One agent removed; single adequacy authority (Planner).
- Resolves ADR-0025 Issue 5 and the over-search churn.
- Rollback requires a code-level revert (no toggle).
