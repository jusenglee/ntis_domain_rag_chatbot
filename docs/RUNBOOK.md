# RUNBOOK

This runbook is the operational triage guide for the NTIS Domain RAG repository.

Use this document to diagnose retrieval-first behavior. For the execution contract itself, see `docs/CONTRACT.md`. For regression baselines, see `docs/GOLDEN_TESTS.md`.

---

## 0. Operational baseline

- System shape: retrieval-first search system with chat UX layered on top
- Triage priority: retrieval correctness before answer wording
- Planner invalid / contract violation: strict fail-close
- Retrieval failure must not be disguised as empty success
- Chat UX issues are triaged after retrieval correctness

---

## 1. Fast triage flow

### Step 1. Query / intent

Check logs:
- `REQ.START`
- `PLANNER.PIPELINE`

Check fields:
- raw user query
- `NormalizedIntent`
- explicit identifier seed presence
- candidate exact-key presence

Healthy signs:
- broad topic, exact lookup, and relation intent map to the expected retrieval family
- cheap precheck only contributes safe hints

Suspicious signs:
- non-id hint bypasses planner unexpectedly
- exact-key candidate falls through to topic-like routing

Primary files:
- `apps/api/services/request_facade.py`
- `apps/core/query_intent.py`
- `apps/core/pipeline_steps.py`

### Step 2. Strategy

Check logs:
- `PLANNER.ASSEMBLE`
- `RAG.PLAN`

Check fields:
- `mode`
- `relation`
- `join_key_mode`
- `target_cols`
- `strategy_summary`

Healthy signs:
- `SEARCH / LOOKUP / JOIN` are fixed deterministically
- execution uses `strategy_summary` as the runtime truth

Suspicious signs:
- planner layer and execution layer disagree
- runtime appears to rebuild strategy from raw text

Primary files:
- `apps/core/planner_contract.py`
- `apps/core/rag_runtime_prelude.py`

### Step 3. Filter compile

Check logs:
- `RAG.FILTER.COMPILED.QDRANT`
- `RAG.COL.RETRIEVE`

Check fields:
- compiled must / should structure
- identifier or role filters
- join-key contract preservation

Healthy signs:
- compiled filter matches retrieval intent

Suspicious signs:
- SEARCH gets hard must constraints
- LOOKUP or JOIN loses exact-key constraints

Primary files:
- `apps/core/planner_contract.py::StrategyCompiler.compile()`
- `apps/core/filters.py::compile_filter()`
- `apps/core/rag_compile_runtime.py`

### Step 4. Retrieval distribution

Check logs:
- `RAG.RETRIEVE`
- `RAG.COL.STATS`

Check fields:
- dense hit / lexical hit
- collection hit distribution
- hybrid retrieval behavior

Healthy signs:
- hit distribution matches query class

Suspicious signs:
- dense and lexical both zero unexpectedly
- retrieval is stuck in one weak path

Primary files:
- `apps/core/retrieval.py::dense_retrieve_hybrid_multi()`
- `apps/core/rag_collection_retrieval.py`

### Step 5. Evidence

Check logs:
- `RAG.RESULT.TOP`
- `RAG.CONTEXT`
- `RAG.CTX`

Check fields:
- `canonical_evidence`
- `render_profile`
- `output_type`

Healthy signs:
- `pjt_id`, `pjt_no`, and organization roles survive
- context shape matches `output_type`

Suspicious signs:
- canonicalization blurs retrieval semantics
- render profile collapses distinct output shapes

Primary files:
- `apps/core/canonical_evidence.py`
- `apps/api/services/rag_result_assembly.py`
- `apps/api/services/context_build_policy.py`

### Step 6. Answer / streaming

Check logs:
- `LLM.RESULT`
- `STREAM.DONE`

Check fields:
- `ttft_any_ms`
- `ttft_content_ms`
- direct-answer mode stream behavior

Healthy signs:
- answer generation follows canonical evidence and render profile

Suspicious signs:
- duplicated stream output
- reasoning leakage or incorrect direct-answer routing

Primary files:
- `apps/api/routes.py`
- `apps/api/services/answer_generation.py`
- `apps/api/services/answer_merge.py`

---

## 2. Minimum observability

Default operational logs:
- `REQ.START`
- `PLANNER.PIPELINE`
- `RAG.PLAN`
- `RAG.RETRIEVE`
- `RAG.RESULT.TOP`
- `RAG.CONTEXT`
- `RAG.RESULT`
- `LLM.RESULT`
- `REQ.SUMMARY`
- `REQ.ERROR`
- `REQ.END`

`REQ.SUMMARY` interpretation:
- `mode`, `relation`, `target_cols` come from execution strategy
- `intent_payload_version` and `strategy_version` must both be visible on v3 paths
- `planner_*` fields are diagnostic only and must not replace execution truth

`RAG.CONTEXT` / `RAG.CTX` interpretation:
- `execution_mode`, `execution_base_route`, and `execution_output_type` describe execution-layer context assembly
- context assembly must be driven by execution request artifacts, not ad-hoc planner reconstruction

---

## 3. Empty-result baseline

Inspect these fields together:
- `contract_fail_reason`
- `empty_result_policy`
- `reranked_count`

Current baseline:
- `lookup` and `join` with `reason=no_reranked` are ordinary `normal_no_result` outcomes
- `search` with `reason=no_reranked` remains `strict_search`
- deferred hop1 0-hit and dual-branch 0-hit are normal no-result outcomes

Strict contract failures include:
- resolved `instance/group` strategy missing the required runtime seed
- invalid enum or unsupported relation
- malformed payload that violates the v3 contract

---

## 4. Stagewise planner triage

Recommended environment:

```bash
PLANNER_STAGE1_PROMPT_VERSION=v1
PLANNER_STAGE2_PROMPT_VERSION=v1
PLANNER_TEMPERATURE=0.0
```

Key logs:
- `PLANNER.STAGE1`
- `PLANNER.GATE`
- `PLANNER.STAGE2`
- `PLANNER.ASSEMBLE`
- `PLANNER.REGATE`
- `PLANNER.IDS_MAP.INVALID_VALUE`

Review order:
- Stage 1: `action`, `head`, `relation_candidate`, `referential_followup`, `confidence`
- Deterministic gate: `mode`, `relation`, `join_key_mode`, `target_cols`, `output_type`
- Stage 2: `ids_map`, `candidate_keys`, `project_key_policy`, `join_resolution_policy`, `filters`, `retrieval_query`, `limit`
- Assemble: verify that execution strategy fields were not mutated downstream
- Prelude: verify strict contract gate and any downgrade

---

## 5. Common failure patterns

### A. JOIN but zero results

Symptoms:
- `JOIN_KEYS_MISSING`
- `mode=join` with `docs_found=0`

Check first:
- `PLANNER.IDS_MAP.INVALID_VALUE`
- `PLANNER.REGATE`
- `candidate_keys.project_key`
- `project_key_policy`
- `join_key_mode`
- `resolved_join_key_mode`
- `dual_branch_used`

Interpretation:
- `과제번호`-style ambiguous project keys should not be force-resolved early
- if sanitize removes a resolved seed, runtime should downgrade or keep deferred discovery
- `JOIN_KEYS_MISSING` is only valid for resolved `instance/group`

### B. LOOKUP/JOIN no-result vs route error

Symptoms:
- `docs_found=0`
- no reranked items

Check:
- `REQ.SUMMARY.contract_fail_reason`
- `REQ.SUMMARY.empty_result_policy`
- `REQ.ERROR.contract_fail_reason`
- `REQ.ERROR.empty_result_policy`

Interpretation:
- `lookup/join + no_reranked` should remain `normal_no_result`
- `search + no_reranked` remains `strict_search`

### C. SEARCH over-constrained

Check:
- compiled must / should structure
- `search_filter_enabled`

Interpretation:
- SEARCH should remain recall-oriented and avoid lookup-style hard filters

### D. Evidence shape mismatch

Check:
- `RAG.CONTEXT`
- `RAG.CTX`
- `canonical_evidence`
- `render_profile`

Interpretation:
- answer generation must follow canonical evidence, render profile, and execution strategy
- `question_analysis` is not the final execution truth

---

## 6. Query graph observability

Inspect these fields in `REQ.SUMMARY` and `/query/debug`:

- `query_graph_kind`: `project_to_perf`, `perf_to_project`, `perf_to_project_to_perf`, `aggregate_comparison`, `project_series`, `pattern_analysis`, `multi_hop_bundle`, or single-route
- `anchor_summary`
- `aggregation_kind`
- `series_kind`
- `bundle_kind`
- `bundle_target_count`
- `bundle_project_count`
- `bundle_item_count`
- `guidance_required`

These fields do not replace planner truth. They explain how runtime executed the plan.

---

## 7. Extended runtime triage

### Aggregation runtime

Inspect:
- `aggregation_metric`
- `aggregation_group_by`
- `aggregation_threshold`
- `aggregation_result_count`
- `failed_step`

### Series runtime

Inspect:
- `series_kind`
- `series_result_count`
- `series_bucket_count`
- `failed_step`

### Reverse trace

Inspect:
- `reverse_trace_enabled`
- `reverse_trace_hop_count`
- `origin_project_count`
- `followup_perf_count`
- `failed_step`

### Anchor resolution

Inspect:
- `anchor_resolution_status`
- `ambiguity_codes`
- `resolved_researcher_count`
- `resolved_org_count`

### Pattern analysis

Inspect:
- `pattern_kind`
- `pattern_result_count`
- `pattern_subject_count`
- `pattern_support_doc_count`

### Multi-hop bundle

Inspect:
- `bundle_kind`
- `bundle_target_count`
- `bundle_project_count`
- `bundle_item_count`
- `guidance_required`

---

## 8. QuestionAnalysis v3 triage

Check fields:
- `candidate_keys.project_key`
- `project_key_policy`
- `join_resolution_policy`
- `resolved_join_key_mode`
- `dual_branch_used`
- `candidate_project_key_count`
- `candidate_perf_key_count`

Interpretation:
- `candidate_keys.project_key` means unresolved exact project key
- `project_key_policy=ambiguous_or` means exact OR exact discovery
- `join_key_mode=deferred` means discovery is part of the legal strategy
- `IntentPayloadV3` and `strategy_version="v3"` must travel together on v3 paths
- `intent_payload_version="v3"` must be visible on v3 transport paths

---

## 9. Minimal reproduction checklist

```txt
[ ] raw query
[ ] normalized_intent
[ ] strategy summary
[ ] ids_map / candidate_keys
[ ] compiled qdrant filter
[ ] retrieval hit distribution
[ ] canonical evidence
[ ] render profile / output_type
[ ] contract_fail_reason / error_code
[ ] environment and request identifiers
```
