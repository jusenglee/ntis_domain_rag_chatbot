# ADR-0006: Contract Regression Capability Registry

- Status: Proposed
- Date: 2026-04-02

## Context

The current branch has a clear contract surface but no clear executable ownership for that surface.

- Active validation is limited to `py_compile`, import smoke, `create_app()` smoke, and optional workflow compile smoke when `langgraph` is available.
- `pytest.ini` still points at `tests`, but the tracked `tests/` tree is currently absent in this worktree.
- `scripts/run_baseline_checks.ps1` is intentionally retired and exits with guidance instead of running a baseline.
- `apps/docs/GOLDEN_TESTS.md` still defines the high-value SEARCH / LOOKUP / JOIN, follow-up, groundedness, and streaming contracts, but those scenarios no longer map to an active executable lane.
- `apps/api/contracts/repo_manifest.py` only exposes planner prompt defaults, so automations cannot distinguish "covered by an active lane" from "documented only" or "environment-blocked".

This creates three architecture problems.

- A non-negotiable contract can silently move from "tested" to "documented only" without a single source of truth saying that happened.
- Future agents cannot tell whether a failed check is a real regression, a missing dependency, or an intentionally unavailable validation lane.
- Smoke-only validation is too weak for planner immutability, SEARCH/LOOKUP/JOIN gating, follow-up truth, answer-state consistency, and streaming watchlist behavior.

## Decision

Adopt a capability-based validation registry and treat it as architecture, not housekeeping.

- Separate contract inventory from executable availability.
- Keep `apps/docs/GOLDEN_TESTS.md` as the canonical inventory of required behavior until executable coverage is restored.
- Define validation lanes by capability profile instead of one implicit "baseline" bucket.
- Expose those lanes through docs first, then through `repo_manifest.py`, so automation and humans can read the same ownership map.
- Do not restore the old monolithic baseline script as a hidden gate. Each lane should have an explicit command, prerequisites, and failure shape.

## Target State

The repository should converge on three explicit capability profiles.

- `smoke`
  - import-light sanity only
  - always expected to run in the default workspace
  - does not claim contract completeness
- `contract_import_light`
  - primary executable contract lane for immutable planner strategy, SEARCH/LOOKUP/JOIN gates, follow-up truth, answer-state consistency, and streaming policy
  - must avoid heavyweight runtime dependencies where possible
  - becomes the default regression gate once restored and green
- `dependency_rich`
  - optional env-dependent lanes such as workflow compile, provider-backed runtime paths, or heavier retrieval integration
  - failures should report "environment unavailable" separately from contract regressions

Each lane should eventually declare:

- owner
- purpose
- command
- prerequisites
- covered contract families
- expected unavailable/fail-open behavior

## Staged Migration Plan

### Phase 0: Doc Registry

- Publish the capability model in ADR + handoff.
- Mark the current active lane as `smoke`.
- Mark golden scenarios as `covered`, `planned`, or `environment_blocked`.

### Phase 1: Minimal Contract Lane Restoration

Restore a very small import-light test lane instead of the entire deleted historical suite.

Minimum recommended scope:

- planner immutable strategy and prompt-default ownership
- SEARCH people/org hard-must ban
- LOOKUP/JOIN hard gate preservation
- nested people/org same-object gate
- streaming awaited-close / emitted-chunk / TTFT metrics

### Phase 2: Manifested Ownership

Extend `apps/api/contracts/repo_manifest.py` with validation sections such as:

- active validation profile
- optional validation profiles
- commands and prerequisites
- golden coverage inventory or summary counts

At that point docs, automation, and repo inspection should all point to the same validation truth.

### Phase 3: Dependency-Rich Expansion

- Reintroduce optional workflow compile and heavier runtime checks behind explicit prerequisites.
- Keep them additive; they must not replace the import-light contract lane.

### Phase 4: Golden Coverage Accounting

- Every high-value scenario in `apps/docs/GOLDEN_TESTS.md` should map to one executable lane or one explicit backlog item.
- "Documented only" must become a deliberate temporary state, not an accident.

## First Safe Step

The safest next implementation step is one narrow change set that does all of the following together:

- restore only an import-light contract subset under `tests/`
- update `pytest.ini` so it reflects the restored subset instead of a missing tree
- add a validation section to `repo_manifest.py`
- sync `apps/docs/04_회귀기준과_점검.md` and `apps/docs/PRODUCT_BASELINE.md` to the same lane vocabulary

That step is safer than reviving the retired baseline script because it makes the new validation truth explicit and keeps the first restored lane small enough to review.

## Files To Create Or Update

This ADR creates:

- `apps/docs/ADR/ADR-0006-contract-regression-capability-registry.md`

The first implementation wave should update:

- `apps/api/contracts/repo_manifest.py`
- `pytest.ini`
- `apps/docs/04_회귀기준과_점검.md`
- `apps/docs/PRODUCT_BASELINE.md`
- `tests/conftest.py`
- a minimal import-light subset such as:
  - `tests/test_planner_stagewise.py`
  - `tests/test_rag_filter_policy.py`
  - `tests/test_people_filter_nested_gate.py`
  - `tests/test_runtime_helpers_stream_bypass.py`

## Trade-Offs

Positive:

- makes missing contract coverage explicit
- gives Watcher / Improver / Architect a shared language for "active", "planned", and "environment-blocked"
- restores regression value without forcing a speculative full test-tree resurrection

Costs:

- adds one more ownership surface to keep current
- requires docs, manifest, and tests to move together
- may reveal that some older tests are no longer import-light and need rewriting, not simple restoration

## Risks And Unknowns

- The current `tests/` deletion may reflect an intentional intermediate branch state, so the first restoration wave must confirm which subset still matches current module ownership.
- Streaming checks may still pull in heavier dependencies than expected; the import-light lane must stay disciplined.
- The dirty worktree already contains broad code movement, so the first restoration wave should stay small and avoid mixing validation architecture with unrelated runtime cleanup.

## Rollback

If the first restoration wave proves too noisy, keep `smoke` as the only active lane and preserve this registry as documentation only.

Do not:

- re-enable the retired baseline script as an implicit gate
- claim `pytest` is active while `tests/` is still missing
- treat dependency-rich checks as a substitute for the missing import-light contract lane
