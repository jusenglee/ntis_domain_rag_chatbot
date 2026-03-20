# GOLDEN TESTS

This document records the active golden-query invariants for the NTIS Domain RAG repository.

It is not a tutorial. It is the regression and invariant baseline for retrieval-first behavior.

## Current Baseline

- retrieval correctness comes before answer wording
- `apps/core/query_intent.py` only contributes cheap precheck signals
- planner contract enforcement is owned by `apps/core/planner_contract.py`
- execution mode selection and runtime enforcement are validated against the final assembled strategy

## Golden Queries

| ID | Query | Expected mode | Notes |
|---|---|---|---|
| G001 | `AI related projects` | `search` | broad topic query |
| G002 | `1711015550 project detail` | `lookup` | exact project id lookup |
| G003 | `PJT-2020-1234-5678 related outputs` | `join` | unresolved exact project key plus perf/output cue should stay join-classified |
| G004 | `Kim researcher projects` | `lookup` | researcher cue should remain person-constrained lookup/list behavior |
| G005 | `ETRI papers stats 2021 2023` | `lookup` | stats-style perf lookup |

## 1. Strategy Invariants

- planner fixes one strategy per request
- executor validates and executes that strategy; it does not invent another one
- `SEARCH`, `LOOKUP`, and `JOIN` remain separate contracts
- cheap precheck must not override planner truth for people, organizations, or roles
- explicit identifier precheck may skip planner only for strong resolved-id paths
- ambiguous project-key mentions such as `과제번호` must not be promoted to resolved `pjt_id` or `pjt_no`

## 2. Join / Filter Invariants

- JOIN requires a valid relation intent and a legal join strategy
- invalid stage2 seeds removed by sanitize must not leave a strict JOIN behind
- `lookup` / `join` with `no_reranked` are normal no-result outcomes
- relation queries use `output_type=relation`; they do not introduce a separate `action=relation` dialect
- `join_key_mode=instance` and `join_key_mode=group` remain strict resolved-key modes
- `join_key_mode=deferred` is a legal runtime strategy for ambiguous exact project keys
- group fallback must be explicit in runtime metadata rather than silently changing key semantics

## 3. Canonical Evidence Invariants

- canonical evidence preserves `pjt_id`, `pjt_no`, and `rst_id` semantics independently
- lead / participant / affiliation organization semantics remain separate
- retrieval view and prompt view remain distinct artifacts
- answer context is built from canonical evidence, not from raw retrieval payload

## 4. Render / Output Invariants

- `summary`, `detail`, `list`, `stats`, `relation`, `comparison`, and `series` must keep distinct context shapes
- `output_type` controls evidence presentation shape, not answer wording
- people-oriented project lookups must resolve to people-oriented render profiles when appropriate

## 5. Streaming Invariants

- reasoning chunks must not leak into final user-visible answer content
- `ttft_any_ms` and `ttft_content_ms` remain separate metrics
- direct-answer mode must not duplicate final content streams

## 6. Extended Runtime Invariants

### Comparison

- `comparison` must produce aggregation payloads before answer generation
- aggregation payloads expose `metric`, `group_by`, `threshold`, `sort_order`, and `rank_items`
- thresholded comparison requests keep the threshold in payload and summary metadata

### Series

- `output_type=series` must produce runtime evidence, not metadata only
- series payloads expose `series_key_kind`, `series_key`, `instance_projects`, `linked_perf`, and `year_buckets`
- `pjt_no`-based series expansion must not collapse group and instance semantics

### Reverse trace

- `perf -> project -> perf` keeps the relation chain in runtime payloads and logs
- hop3 follow-up must de-duplicate the origin performance hit from hop1
- reverse-trace activation is planner-first

### Anchor resolution

- researcher + generic org ambiguity must remain visible in summary/debug metadata
- resolved role-specific org filters may be compiled only from planner-fixed role truth
- seedless `JOIN(instance)` with unresolved researcher/org pairing must downgrade to `lookup`

### Pattern analysis

- `pattern_analysis` queries must set `query_graph_kind=pattern_analysis`
- supported pattern kinds are runtime-computed, not answer-layer inferred

### Multi-hop bundle

- `multi_hop_bundle` is a planner-first query-graph kind for researcher/org -> project -> multiple downstream targets
- ambiguity must downgrade execution and surface guidance instead of forcing strict JOIN

## 7. QuestionAnalysis v3 Golden Cases

- `과제번호 a4412354543 상세` -> `LOOKUP` + `project_key_policy=ambiguous_or` + unresolved `candidate_keys.project_key`
- `과제번호 a4412354543 성과` -> `JOIN` + `join_key_mode=deferred`
- `과제고유번호 a4412354543 상세` -> resolved `ids_map.pjt_id`
- `동일과제번호 a4412354543 전체 이력` -> resolved `ids_map.pjt_no`
- numeric-only project keys without a strong label must not be auto-promoted to resolved `pjt_id`
- alphanumeric exact project keys may survive as `candidate_keys.project_key` if hygiene validation passes

Transport baseline:
- `IntentPayloadV3`
- `intent_payload_version="v3"`
- `strategy_version="v3"`
