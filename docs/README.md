# Docs Guide

This directory contains the authoritative documentation set for the NTIS Domain RAG repository.

The system follows a retrieval-first architecture with chat UX layered on top. Retrieval planning,
contract enforcement, canonical evidence handling, and renderer-specific prompt views remain the
source of truth for application behavior.

## Quick Start

Read these four documents first:

- `ARCHITECTURE_RETRIEVAL_FIRST.md`
- `SYSTEM_FLOW_RETRIEVAL_FIRST.md`
- `CONTRACT.md`
- `RUNBOOK.md`

Then use `MODE_DECISION_GUIDE.md`, `PLANNER_PARAMETER_REFERENCE.md`, and `GOLDEN_TESTS.md`
as the working references for mode selection, planner/runtime vocabulary, and regression baselines.

## Read First

Recommended reading order:

1. `ARCHITECTURE_RETRIEVAL_FIRST.md`
2. `SYSTEM_FLOW_RETRIEVAL_FIRST.md`
3. `CONTRACT.md`
4. `RUNBOOK.md`
5. `MODE_DECISION_GUIDE.md`
6. `PLANNER_PARAMETER_REFERENCE.md`
7. `GOLDEN_TESTS.md`
8. `ENVIRONMENT.md`
9. `RETRIEVAL_ROBUSTNESS_PLAN.md`

## Source Of Truth

### Retrieval-first baseline

- Retrieval intent assembly: `apps/api/services/request_facade.py`
- Planner merge / final strategy application: `apps/api/services/planner_service.py`
- Planner contract validation: `apps/core/planner_contract.py`
- Runtime prelude / contract gate: `apps/core/rag_runtime_prelude.py`
- Runtime dispatcher: `apps/core/rag_pipeline.py`
- SEARCH / LOOKUP orchestration: `apps/core/rag_base_orchestration.py`
- JOIN orchestration: `apps/core/rag_join_orchestration.py`
- Canonical evidence normalization: `apps/core/canonical_evidence.py`
- Result assembly / render bridge: `apps/api/services/rag_result_assembly.py`
- Chat UX entrypoints: `apps/api/routes.py`, `apps/api/services/answer_generation.py`, `apps/api/services/answer_merge.py`

### Runtime entrypoints

- Runtime entry point: `apps/api/main.py`
- App assembly / workflow DI: `apps/api/app_factory.py`
- Workflow graph definition: `apps/api/services/workflow_builder.py`
- Runtime bootstrap / shutdown: `apps/api/runtime.py`

## Document Roles

- `ARCHITECTURE_RETRIEVAL_FIRST.md`: repository structure, layer boundaries, and retrieval-first architecture
- `SYSTEM_FLOW_RETRIEVAL_FIRST.md`: artifact handoff and end-to-end execution flow
- `CONTRACT.md`: retrieval/runtime contracts, fail-close rules, and v3 transport semantics
- `RUNBOOK.md`: triage steps, observability fields, and operational debugging rules
- `MODE_DECISION_GUIDE.md`: SEARCH / LOOKUP / JOIN decision guide
- `PLANNER_PARAMETER_REFERENCE.md`: planner/runtime parameter vocabulary
- `GOLDEN_TESTS.md`: regression baselines and invariants
- `ENVIRONMENT.md`: runtime defaults, validation entrypoints, and deployment-facing configuration
- `MAINTENANCE_TASK_MASTER.md`: maintenance backlog and structural debt tracking
- `RETRIEVAL_ROBUSTNESS_PLAN.md`: future retrieval robustness roadmap
- `NTIS_RAG_Search_Strategy_v1_2.md`: legacy strategy reference

## Current Baseline

The current repository baseline includes the following behavior.

- `ids_map` contains resolved identifiers only.
- `candidate_keys.project_key` stores unresolved exact project keys.
- `project_key_policy=ambiguous_or` means exact OR exact project discovery, not free-text fallback.
- `join_key_mode=deferred` is a valid runtime strategy.
- `lookup` and `join` with `reason=no_reranked` are `normal_no_result` outcomes.
- `search` with `reason=no_reranked` remains `strict_search`.
- `IntentPayloadV3` is the active transport contract and `strategy_version="v3"` is the active semantic contract.
- Planner-first remains the default rule for people, organization, relation, reverse-trace, and pattern meaning.

## Prompt Views

Supported prompt views are:

- `summary`
- `detail`
- `list`
- `stats`
- `relation`
- `comparison`
- `series`

`output_type` controls evidence presentation shape. It is not answer wording policy.

## Extended Runtime Capabilities

The runtime now includes the following retrieval-first extensions.

- `comparison`: precomputed aggregation payloads for project/performance comparisons
- `series`: project-group and year-window series payloads
- `perf_to_project_to_perf`: reverse trace from performance to origin project to follow-up performance
- `anchor_resolution`: planner-first researcher/org anchor normalization before filter compilation
- `pattern_analysis`: runtime-computed pattern payloads for supported pattern kinds
- `multi_hop_bundle`: researcher/org -> project -> multiple downstream targets in one payload

## Update Rules

Update the smallest authoritative document set that matches the behavior change.

- Contract, planner, filter, query intent, or `rag_pipeline` changes: update `CONTRACT.md`, `GOLDEN_TESTS.md`, and `README.md`
- Runtime, route, metrics, or streaming changes: update `RUNBOOK.md`, `ENVIRONMENT.md`, and `README.md`
- Large policy or design changes: add or update an ADR

## Writing Rules

- Keep documents in UTF-8.
- Do not leave broken Korean or mojibake in repository docs.
- Do not treat `pjt_id` and `pjt_no` as interchangeable.
- Do not mix lead / participant / affiliation organization semantics.
- Do not describe raw payload, canonical schema, and prompt view as the same artifact.
- Do not dump raw retrieval payload into prompt-facing documentation.
