# CONTRACT

This document records the retrieval-first execution contract for the NTIS Domain RAG repository.

For parameter vocabulary, see `docs/PLANNER_PARAMETER_REFERENCE.md`.
For SEARCH / LOOKUP / JOIN selection, see `docs/MODE_DECISION_GUIDE.md`.
For end-to-end execution flow, see `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`.

## Current Baseline

- System shape: retrieval-first search system with chat UX layered on top
- Runtime baseline: staged-only + strict
- Planner invalid / contract violation: fail-close
- Lower-layer fallback or silent strategy promotion: disabled by default

## 1. Retrieval Intent Contract

### Purpose

Retrieval intent must be fixed before answer generation. Lower layers must not reinterpret the request and build a second strategy.

### Core fields

- `mode`: `search | lookup | join`
- `action`: `topic | list | detail | stats | download`
- `relation`: `('project', 'perf') | ('perf', 'project') | None`
- `join_key_mode`: `instance | group | deferred | None`
- `target_cols`
- `ids_map`
- `candidate_keys`
- `output_type`

### MUST

- `SEARCH` is the recall-oriented mode.
- `LOOKUP` is the exact or strongly constrained lookup mode.
- `JOIN` is the relation-oriented multi-step retrieval mode.
- `output_type` means evidence presentation shape, not answer wording.
- `ids_map` contains resolved identifiers only.
- `candidate_keys.project_key` stores unresolved exact project keys.
- `project_key_policy=ambiguous_or` means exact OR exact project discovery, never free-text fallback.

### MUST NOT

- `pjt_id` and `pjt_no` must not be treated as the same key.
- The same lookup/join seed must not carry both `pjt_id` and `pjt_no` in resolved `ids_map`.
- Ambiguous project-key mentions such as `과제번호` must not be force-promoted to resolved `pjt_id` or resolved `pjt_no`.

### Fail-close

- Resolved identifier semantics conflict -> fail-close
- Invalid enum or unsupported relation -> fail-close

## 2. Strategy Assembly Contract

### Purpose

Planner and deterministic gates must produce one final strategy. After assembly, lower layers may validate and execute, but must not replace the strategy.

### Stage 1 allowed fields

- `action`
- `head`
- `relation_candidate`
- `referential_followup`
- `confidence`

### Stage 1 forbidden fields

- `mode`
- `join_key_mode`
- `target_cols`
- `ids_map`
- `filters`
- `retrieval_query`
- `limit`

### Deterministic gate fields

- `mode`
- `relation`
- `join_key_mode`
- `target_cols`
- `output_type`

### Stage 2 allowed fields

- `ids_map`
- `candidate_keys`
- `project_key_policy`
- `join_resolution_policy`
- `filters`
- `retrieval_query`
- `limit`
- `confidence`

### Stage 2 forbidden fields

- `mode`
- `head`
- `action`
- `relation`
- `join_key_mode`
- `target_cols`
- `strategy_version`

### MUST

- Planner may propose structure and hints, but deterministic gates own final strategy fields.
- `과제번호` is an ambiguous alias and must remain unresolved unless a stronger label resolves it.
- Planner-first applies to people, organization, role, reverse-trace, and pattern semantics.
- The executor receives one final strategy object.
- `IntentPayloadV3` is the active transport contract and `strategy_version="v3"` is the active semantic contract.

### MUST NOT

- Parser, merge, or prelude must not silently repair strategy semantics.
- Lower layers must not rebuild planner truth from raw text.

## 3. Runtime Enforcement Contract

### Purpose

Runtime validates and executes the planner-fixed strategy. It does not invent a second strategy.

### Source files

- `apps/api/services/planner_service.py`
- `apps/core/planner_contract.py`
- `apps/core/rag_runtime_prelude.py`
- `apps/core/rag_pipeline.py`

### MUST

- Planner invalid / contract violation -> `StrategyViolation`
- Runtime prelude output must remain within the `RuntimePreludeResult` contract.
- Evidence assembly must normalize raw retrieval payload into canonical evidence.
- Chat UX must use canonical evidence and render profile first.

### JOIN rules

- `mode=join` requires a valid `join_key_mode`.
- `join_key_mode=instance` means resolved `pjt_id` execution.
- `join_key_mode=group` means resolved `pjt_no` execution.
- `join_key_mode=deferred` means hop1 discovery is allowed before runtime resolves the execution key kind.
- `resolved_join_key_mode` is a runtime result, not a planner field.
- `dual_branch_used` is a runtime observability field, not a planner field.

### JOIN fail-close

- `mode=join` with missing required resolved seed -> fail-close
- `resolved_pjt_id` with empty `ids_map.pjt_id` -> fail-close
- `group` join with missing `pjt_no` -> fail-close
- `JOIN_KEYS_MISSING` is strict only for resolved `instance/group`

### Normal no-result

- `lookup` or `join` with `reason=no_reranked` -> `normal_no_result`
- `search` with `reason=no_reranked` -> `strict_search`
- deferred discovery 0-hit and dual-branch 0-hit are ordinary no-result outcomes

## 4. Evidence / Render Contract

### Purpose

Raw retrieval payload, canonical evidence, and prompt-facing render views must remain distinct artifacts.

### MUST

- Canonical evidence must preserve `pjt_id`, `pjt_no`, `rst_id`, and role-specific organization semantics.
- Output shaping must be determined from `output_type` and execution strategy.
- `summary`, `detail`, `list`, `stats`, `relation`, `comparison`, and `series` must keep distinct evidence shapes.
- `mode=join` with relation must align `head` with the relation target.

### MUST NOT

- Raw retrieval payload must not be dumped into prompt context.
- Retrieval metadata and prompt-facing facts must not be treated as the same layer.

## 5. Chat UX Boundary

### MUST

- Chat UX may manage memory, streaming, and answer phrasing.
- Chat UX must rely on canonical evidence, render profile, and execution strategy.

### MUST NOT

- Chat UX must not rewrite retrieval contracts.
- Chat UX must not use `question_analysis` as if it were the final runtime strategy.
- Retrieval workflow and answer generation must not bypass canonical evidence with raw payload.

## 6. Extended Runtime Contracts

### Comparison runtime

- `output_type=comparison` has active runtime behavior.
- Aggregation is computed before answer generation.
- Supported metrics currently include `paper_count`, `patent_count`, `report_count`, and `perf_total_count`.
- Supported groupings currently include `project` and `project_group`.

### Series runtime

- `output_type=series` has active runtime behavior.
- Supported axes currently include `pjt_no` and year-window expansion.
- Series payloads must expose `series_key_kind`, `series_key`, `instance_projects`, `linked_perf`, and `year_buckets`.

### Reverse trace

- `QueryGraphPlan.kind=perf_to_project_to_perf` means `lookup_perf -> join_perf_to_project -> followup_project_to_perf`.
- Reverse trace activation is planner-first.
- Hop3 empty is partial success, not strict join failure.

### Anchor resolution

- `ResolvedAnchorSet` is the planner-first execution contract for researcher/org anchors.
- Generic org anchors remain visible for observability, but role-specific execution requires resolved role truth.
- Seedless `JOIN(instance)` with unresolved researcher/org pairing must downgrade instead of forcing strict join execution.

### Pattern analysis

- `pattern_kind` is planner-first metadata.
- Runtime may compute `pattern_analysis` only for supported kinds.
- The answer layer must not derive pattern conclusions from raw payload on its own.

### Multi-hop bundle

- `multi_hop_bundle` is a planner-first query-graph kind for researcher/org -> project -> multiple downstream targets.
- Ambiguity should downgrade execution and surface guidance instead of forcing strict JOIN.

## 7. QuestionAnalysis v3 / IntentPayloadV3

- `ids_map` contains resolved identifiers only.
- `candidate_keys.project_key` stores unresolved exact project keys.
- `project_key_policy=ambiguous_or` means exact OR exact project discovery.
- `join_key_mode=deferred` is a legal strategy and does not mean contract failure.
- `join_resolution_policy` controls deferred resolution behavior.
- Summary/debug fields include `project_key_policy`, `join_resolution_policy`, `resolved_join_key_mode`, `dual_branch_used`, and `candidate_project_key_count`.

Transport baseline:
- `IntentPayloadV3`
- `intent_payload_version="v3"`
- `strategy_version="v3"`
