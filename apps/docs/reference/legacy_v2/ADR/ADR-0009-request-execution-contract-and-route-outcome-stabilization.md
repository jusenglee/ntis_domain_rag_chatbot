# ADR-0009: Request Execution Contract and Route Outcome Stabilization

- Status: Proposed
- Date: 2026-04-13

## Context

The repository already has strong architecture rules:

- planner strategy is single and immutable per request
- `SEARCH` is recall-first and must not grow hidden hard-must gates
- `LOOKUP` and `JOIN` are precision-first and keep hard gating
- people/org queries default to lookup-oriented handling
- no hidden fallback chat path is allowed
- no BM25-only shortcut is allowed for `LOOKUP` or `JOIN`

Recent work improved those rules, but the current implementation spine is still highly transitional.

- `apps/conversation/request_facade.py` is about 1.5k lines and currently owns turn trigger, interpretation, turn policy, scope resolution, context-router rescue, follow-up materialization, count-contract validation, and `strategy_meta` assembly.
- `apps/retrieval/retrieval_workflow.py` is about 1.8k lines and consumes many `strategy_meta` keys as execution inputs, clarification triggers, and detail-anchor hints.
- `apps/chat/answer_generation.py` builds `AnswerArtifact`, then also emits `merge_debug` and `selected_answer_meta`.
- `apps/api/routes.py` rebuilds final user-visible outcome from multiple state surfaces, while still carrying legacy stream-emission helpers.
- `apps/docs/02_실행계약과_전략규칙.md` already records that part of section 10 was lost to encoding damage, so some contract recovery currently depends on code inspection more than document truth.

This ADR does not change retrieval semantics. It defines a safer contract surface for the semantics the system already enforces.

## Current State Summary

The current architecture is functionally layered but contract ownership is still spread across free-form dicts and route-time reconstruction.

- planner legality and strategy hints are carried in `question_analysis.hard_contract` and `question_analysis.soft_strategy_hints`
- pre-planner follow-up behavior is decided across turn trigger, turn interpreter, turn policy, scope resolver, and optional context-router rescue
- `request_facade` serializes much of that into one large `strategy_meta` dict
- retrieval re-reads `strategy_meta` to decide count-contract clarification, follow-up clarification, detail anchor behavior, and some display behavior
- answer generation publishes `AnswerArtifact` but also duplicates route-relevant facts into `merge_debug` and `selected_answer_meta`
- routes reconstruct a final artifact and clarification payload from whichever of several state fields happen to be present

The practical result is that the runtime mostly honors the intended contracts, but future changes must understand multiple overlapping state surfaces before they can safely modify one request path.

## Pain Points

### 1. Free-form cross-layer contract bag

`strategy_meta` currently mixes several ownership domains in one dict:

- immutable planner contract summary
- follow-up resolution facts
- turn-policy output
- count-contract validation
- context-router audit data
- candidate and focus-entity display data

That makes it useful for logging, but risky as an execution truth surface. A new field can accidentally become runtime truth simply because downstream code starts reading it.

### 2. Transitional overlap in pre-planner request handling

The main path is now interpreter-first, but `request_facade` still contains active rescue and compatibility logic around:

- turn policy
- scope resolution
- context-router rescue
- clarification assembly
- anchor materialization

The overlap is intentional for safety, but it is no longer small. This increases the chance that a future refactor changes who is allowed to choose an anchor or when clarification is allowed to short-circuit.

### 3. Route outcome truth is duplicated

The route layer can currently derive a final user-visible outcome from:

- `final_answer_artifact`
- legacy `answer_artifact`
- `selected_answer_meta`
- `merge_debug`
- state-level `clarification`
- `retrieval_bundle.clarification`

This is workable, but it means transport correctness depends on reconstruction logic rather than one canonical route outcome object.

### 4. Logging and transport metadata are too close to execution truth

`merge_debug`, `selected_answer_meta`, and parts of `strategy_meta` serve two jobs at once:

- runtime inputs or route outputs
- operator diagnostics

Those two jobs evolve at different speeds. Diagnostics want additive flexibility. Execution truth wants strict ownership and narrow change control.

### 5. Contract docs are partially degraded

Section 10 of `apps/docs/02_실행계약과_전략규칙.md` already declares partial source loss from encoding damage. That raises the cost of safe changes because code, tests, and docs are not equally authoritative in that area.

## Proposed Target State

Stabilize the request pipeline around three explicit typed artifacts and make all other dict surfaces derived, not primary.

### A. `RequestExecutionContract`

Owned at the conversation/planner boundary. This artifact should carry only request-time execution truth:

- `planner_contract`
  - immutable strategy summary
  - `hard_contract`
  - `soft_strategy_hints`
  - resolved project-key axis metadata
- `followup_contract`
  - execution path
  - selected anchor or clarification
  - owner lock
  - candidate summary
  - context-router audit as provenance only
- `count_contract`
  - requested count
  - display count
  - validation status
  - clarification payload when invalid
- `diagnostics`
  - trigger/interpreter summaries
  - recent-mention counts
  - alias warnings

Rule: downstream retrieval may read this typed contract, but must not mine arbitrary diagnostics to invent new strategy behavior.

### B. `RetrievalExecutionEnvelope`

Owned at the retrieval boundary. This is the retrieval-facing projection of the request contract:

- immutable planner strategy
- normalized intent
- resolved execution constraints
- explicit clarification short-circuit, when already authorized upstream
- typed follow-up seed information

Rule: retrieval logic should not depend on the raw shape of `strategy_meta`. If logging still needs `strategy_meta`, it should be serialized from the typed contract after execution inputs are fixed.

### C. `RouteOutcome`

Owned at the API transport boundary. This becomes the only canonical route output surface:

- final answer artifact
- clarification payload
- degraded/error status
- references
- manifest publication status
- transport-ready metadata

Rule: `merge_debug` and `selected_answer_meta` remain diagnostics, not route truth. The route may publish them in debug responses, but `/query/stream` and `/query/debug` should derive user-visible outcome from one canonical `RouteOutcome`.

## Design Constraints That Must Stay True

This target state must preserve the existing behavior contracts:

- planner strategy stays single and immutable
- `SEARCH` stays recall-first and does not gain hidden hard-must filters
- `LOOKUP` and `JOIN` keep hard gating and do not downgrade to BM25-only shortcuts
- people/org default lookup treatment stays intact
- clarification and degraded final answers remain explicit; no hidden fallback chat path is introduced
- route transport remains a projection of internal truth, not a place where strategy is repaired

## Staged Migration Plan

### Phase 0: Contract Inventory and Doc Recovery

- Publish this ADR.
- Inventory current `strategy_meta`, `selected_answer_meta`, and `merge_debug` fields by owner and consumer.
- Recover the missing detail-contract notes in `apps/docs/02_실행계약과_전략규칙.md` from code and current tests, without changing runtime behavior.

Exit criteria:

- every currently-consumed `strategy_meta` field is tagged as either execution truth, diagnostic only, or deprecated
- route outcome reconstruction sources are listed explicitly

### Phase 1: Typed Mirror Models

- Introduce typed models for `RequestExecutionContract` and `RouteOutcome`.
- Add pure adapter functions that project current runtime state into those models.
- Keep existing dict fields in place for compatibility.

Exit criteria:

- no behavior change
- typed models can be generated in unit tests from current state
- existing logs and responses remain byte-compatible where required

### Phase 2: Retrieval Consumer Narrowing

- Update retrieval code to consume typed request contract projections instead of ad hoc `strategy_meta` fields.
- Limit `strategy_meta` reads in retrieval to logging or explicitly transitional compatibility shims.

Exit criteria:

- clarification short-circuit, detail-anchor handling, and count-contract checks read typed inputs first
- no new retrieval behavior is added in this phase

### Phase 3: Route Outcome Canonicalization

- Introduce a single route-outcome assembler near the API boundary.
- Have `/query/stream` and `/query/debug` read one canonical `RouteOutcome`.
- Keep legacy SSE wire-shape adapters as thin compatibility wrappers only.

Exit criteria:

- route no longer reconstructs final answer truth from multiple unrelated state fields
- `AnswerArtifact`, clarification, degraded status, and references come from one canonical object

### Phase 4: Transitional Surface Reduction

- Deprecate raw `strategy_meta` execution reads that have typed replacements.
- Reduce route dependence on `merge_debug` and `selected_answer_meta` for non-debug paths.
- Remove only the compatibility code proven to be redundant by tests and docs.

Exit criteria:

- remaining free-form dicts are diagnostic or compatibility-only
- request and route behavior contracts are documented in one place each

## First Safe Step

The safest first implementation step is:

1. add typed mirror models and pure adapter functions
2. emit them alongside existing dict surfaces
3. add import-light tests that prove old and new projections describe the same runtime truth

Why this is safe:

- it does not alter planner, retrieval, or route behavior
- it preserves all current invariants
- it makes future deletions evidence-based instead of aspirational

What not to do first:

- do not rewrite `request_facade.py` wholesale
- do not remove context-router rescue in the same change that introduces typed contracts
- do not change SSE wire format while route outcome truth is still duplicated

## Files To Create Or Update

This ADR creates:

- `apps/docs/ADR/ADR-0009-request-execution-contract-and-route-outcome-stabilization.md`

Recommended first implementation wave:

- create `apps/api/contracts/request_execution_contract.py`
- create `apps/api/contracts/route_outcome.py`
- update `apps/conversation/request_facade.py`
- update `apps/retrieval/retrieval_workflow.py`
- update `apps/chat/answer_generation.py`
- update `apps/api/routes.py`
- update `apps/docs/02_실행계약과_전략규칙.md`
- update `apps/docs/06_API_응답명세.md`
- update `apps/docs/SESSION_HANDOFF.md`
- add import-light regressions such as:
  - `tests/test_request_execution_contract_adapters.py`
  - `tests/test_route_outcome_contract.py`
  - targeted updates to existing request-facade, retrieval, and route tests

## Trade-Offs

Positive:

- narrows what counts as execution truth
- keeps diagnostics flexible without letting them become accidental contracts
- gives future agents a safer seam for pre-planner and route-level changes
- makes doc recovery possible without forcing a speculative runtime rewrite

Costs:

- adds new typed models that must be kept aligned with existing runtime state during migration
- temporarily increases duplication while old and new surfaces coexist
- requires disciplined phase boundaries to avoid mixing contract cleanup with behavior changes

## Risks And Unknowns

- the worktree is already dirty across request, conversation, chat, and docs surfaces, so the first implementation wave must stay small and avoid opportunistic cleanup
- some current `strategy_meta` reads may be effectively undocumented contract dependencies; inventory must happen before pruning
- route-level legacy SSE helpers may still support callers or UI assumptions not yet captured in tests
- the damaged section of `apps/docs/02_실행계약과_전략규칙.md` may hide detail-cache nuances that need to be reconstructed from code before type boundaries are finalized

## Rollback

If typed mirror models add confusion without reducing ambiguity, keep this ADR as documentation only and stop before Phase 2.

Do not:

- loosen planner immutability to make migration easier
- let retrieval read new diagnostics fields as strategy truth
- replace current degraded/clarification behavior with hidden fallback chat
- delete compatibility paths before route-level and request-level regressions prove they are redundant
