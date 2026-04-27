# ADR-0007 Solar-Exact Evidence Packing

## Status
Accepted

## Context
- evidence prompt context mixed approximate token counting, line-oriented list context, and raw payload-heavy docstyle rendering.
- the old `build_context_mixed()` path used `_approx_token_len`, per-doc char trimming, and overflow `break`, so one long document could evict later shorter high-quality documents.
- the Solar answer path also clipped evidence-derived context by character count, which conflicted with the zero-truncation policy for evidence packing.

## Decision
- evidence prompt budget is fixed at `RAG_EVIDENCE_TOKEN_BUDGET=20000` and applies only to the evidence JSON array.
- Solar tokenizer counting is the primary packing source-of-truth via `apps/platform/solar_tokenizer_adapter.py`.
- evidence prompt assembly is now:
  - score gate
  - identity-preserving envelope projection
  - exact pack
  - overflow compression
  - re-pack
- `canonical_evidence` remains the truth/view artifact.
- `prompt_units` and `lineages` are separate prompt artifacts.
- answer generation no longer character-clips evidence-derived `answer_context_text` or `debug_answer_context_text`.

## Consequences
- evidence path no longer depends on `_approx_token_len`, line-oriented list context, or `build_context_mixed()`.
- overflow candidates are limited by queue size instead of terminating the scan.
- identity integrity is checked before compressed evidence is admitted back into the prompt.
- current validation for this worktree remains smoke-only.
