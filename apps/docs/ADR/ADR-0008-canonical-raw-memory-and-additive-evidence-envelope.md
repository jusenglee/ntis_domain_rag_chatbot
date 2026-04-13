# ADR-0008: Canonical Raw Memory and Additive Evidence Envelope

## Status
Accepted

## Context
- NTIS payloads frequently include very large nested arrays such as `prtcp_mp` and `prtcp_org`.
- Search-time minimal payload projection is good for retrieval speed, but it is not safe as follow-up truth.
- Prompt-time direct raw payload injection causes token explosions, unstable truncation, and follow-up regressions.
- `request_id` and conversation turn identity are different concerns and must not be merged.

## Decision
We split the evidence flow into four truth surfaces:

1. search-time minimal projection
2. conversation-owned compressed raw payload memory
3. evidence-owned additive prompt envelope
4. serialized prompt artifact derived from `prompt_units`

We also adopt these rules:
- `request_id` remains transport-scoped.
- `turn_id` is conversation-scoped and is used for raw memory, lineage, and follow-up state.
- raw payload memory is stored as gzip-compressed base64 JSON with bounded retention.
- prompt envelope uses:
  - `identity`
  - `stable_base`
  - `facts`
  - `query_bonus`
  - `previews`
  - `compression`
- `prtcp_mp` and `prtcp_org` raw arrays never enter prompt units directly.
- follow-up resolution runs before evidence packing and may short-circuit with deterministic facts.
- prompt packing uses provisional insert plus Solar-tokenizer exact verification near the boundary, at final emit, and during compression re-pack.

## Consequences
### Positive
- follow-up truth is preserved without forcing raw payload into the prompt
- token growth is controlled for very large NTIS arrays
- prompt artifact telemetry becomes explicit: `prompt_units`, `used_tokens`, `dropped_by_floor`, `dropped_by_budget`, `compressed_count`, `lineages`
- `context` and `used_tokens` are always derived from one internal truth source

### Tradeoffs
- conversation storage now owns compressed raw payload memory lifecycle
- production exact packing depends on Solar tokenizer availability
- smoke-only validation is still temporary until a test lane returns

## Implementation Owners
- conversation memory: `apps/conversation/raw_payload_store.py`
- follow-up fact short-circuit: `apps/conversation/fact_followup_resolver.py`
- derived facts / envelope / packing / compression / integrity / lineage: `apps/evidence/*`
- tokenizer owner: `apps/platform/solar_tokenizer_adapter.py`

## Validation
Current active validation is smoke-only:
- `py_compile`
- import smoke
- `create_app()` smoke
- follow-up drift smoke
- huge-array defense smoke
- hybrid verify safety smoke
