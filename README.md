# NTIS Domain RAG Chatbot

NTIS domain RAG service with feature-sliced owners for conversation, planner, retrieval, evidence, chat, platform, and API wiring.

## Current Shape
- `apps/api`: app factory, runtime wiring, routes, transport contracts
- `apps/conversation`: follow-up, scope, anchor, view-state, raw payload memory
- `apps/planner`: query intent, planner runtime, planner defaults, planner service
- `apps/retrieval`: retrieval, filter, compile, orchestration, hydration
- `apps/evidence`: canonical evidence, derived facts, prompt envelope, packing, compression, integrity, lineage
- `apps/chat`: answer generation, merge, LLM runtime, streaming
- `apps/platform`: settings, storage, tokenizer adapter, shared DTOs

## Current Evidence Path
- search uses minimal payload projection
- selected candidates and anchor-related targets are hydrated in batch
- canonical follow-up truth is stored as compressed raw payload memory
- prompt uses additive evidence envelopes, not raw payload dumps
- large `prtcp_mp` / `prtcp_org` arrays are converted into facts and previews
- prompt packing uses provisional insert plus Solar-tokenizer exact verification

## Current Validation
- active validation in this worktree:
  - `python -m py_compile ...`
  - import smoke
  - `python -c "from apps.api.app_factory import create_app; create_app()"`
- `tests/` is not the active validation lane in this worktree
- `scripts/run_baseline_checks.ps1` is retired and historical only

## Read Next
- `apps/docs/README.md`
- `apps/docs/CODEX_CONTEXT.md`
- `apps/docs/EVIDENCE_PROMPT_PACKING.md`
- `apps/docs/SESSION_HANDOFF.md`
