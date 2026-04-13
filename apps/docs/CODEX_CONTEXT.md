# CODEX_CONTEXT.md

## Bootstrap Truth
- repo: `ntis_domain_rag_chatbot`
- branch: `고도화`
- active validation in this worktree:
  - `python -m py_compile ...`
  - import smoke
  - `python -c "from apps.api.app_factory import create_app; create_app()"`
- `tests/` is not the active validation gate in this worktree.
- `scripts/run_baseline_checks.ps1` is a retired historical entrypoint.

## Package Owners
- `apps/api`: composition root, routes, runtime wiring, contracts, streaming transport
- `apps/conversation`: follow-up, scope, anchor, view-state, raw payload memory
- `apps/planner`: intent/planner runtime, planner defaults, planner service
- `apps/retrieval`: retrieval, filter, compile, orchestration, batch hydration
- `apps/evidence`: canonical evidence, derived facts, additive prompt envelope, packing, compression, integrity, lineage
- `apps/chat`: answer generation, merge, LLM runtime, streaming runner
- `apps/platform`: settings, storage, shared DTOs, tokenizer adapter

## Retrieval and Follow-up Rules
- planner strategy is single and immutable for a request.
- answer stage must not re-decide `mode`, `relation`, `target_cols`, or `join_key_mode`.
- `SEARCH` is recall-first and must not add server-side must filters.
- `LOOKUP` and `JOIN` are precision-first and keep server-side gates.
- people/org questions are treated as lookup-oriented by default.
- BM25-only bypass is not allowed.
- fallback chat or mode-changing retries are not allowed.

## Evidence Prompt Truth
- model-facing evidence is a serialized JSON array string.
- internal source of truth is `prompt_units`.
- `context` is only the final serialized artifact from `prompt_units`.
- `used_tokens` is measured from the serialized `context`.
- evidence prompt budget is `RAG_EVIDENCE_TOKEN_BUDGET=20000`.
- production token counting uses the Solar tokenizer adapter in `apps/platform/solar_tokenizer_adapter.py`.

## Canonical Memory and Envelope
- search-time payload projection is minimal and is not canonical truth.
- canonical truth for follow-up lives in compressed raw payload memory owned by `apps/conversation/raw_payload_store.py`.
- raw payload memory is keyed by `conversation_id` and `turn_id`.
- `request_id` stays transport-scoped; `turn_id` is conversation-scoped.
- retention policy:
  - active anchor: `1`
  - inactive recent anchors: `3`
  - gzip + base64 compressed JSON payload
- prompt envelope shape:
  - `identity`
  - `stable_base`
  - `facts`
  - `query_bonus`
  - `previews`
  - `compression`

## Large Array Policy
- `prtcp_mp` and `prtcp_org` raw arrays never go directly into prompt units.
- prompt-safe facts are split into:
  - eager facts: `participant_count`, `participant_org_count`, `lead_researcher_names`, `people_preview`, `org_preview`
  - lazy facts: `role_histogram`, `affiliation_topk`, `matched_people_subset`, `matched_org_subset`
- previews are role-aware, not simple top-N truncation.

## Packing and Compression
- pack flow:
  - provisional insert
  - near-boundary exact verify
  - overflow queue
  - compression
  - re-pack
- exact verify triggers on:
  - near-boundary provisional insert
  - final emit
  - compressed re-pack
- compression may change only narrative fields in `stable_base` and `query_bonus`.
- `identity`, `facts`, and `previews` are immutable through compression and validated by integrity checks.

## Follow-up Priority
- follow-up resolution runs before evidence packing.
- follow-up orchestration is split into `hard signal -> candidate builder -> turn interpreter -> policy validator -> anchor executor`.
- hard signals (`출처 N`, ordinal, explicit ids, guarded relative refs) stay deterministic and run before LLM interpretation.
- ambiguous/deictic/refinement follow-ups may use the turn interpreter, but the policy layer is the final allow/block gate.
- order:
  - hard signal / reference-context owner
  - candidate build from `ConversationViewState`
  - LLM interpretation for ambiguous follow-ups
  - policy validation of reuse rights and candidate validity
  - scope reset/refinement/clarification assist only after policy
  - anchor materialization / raw payload / hydrate
  - exact lookup rerun
  - then envelope build + pack if still needed

## What To Watch
1. strategy drift
2. follow-up candidate/interpreter drift
3. prompt-unit / serialized-context desync
4. raw payload memory retention growth
5. huge `prtcp_mp` / `prtcp_org` prompt explosions
6. Solar tokenizer availability in production environments
