# Docs README

This folder is the current source-of-truth document set for the feature-sliced NTIS RAG repository.

## Read First
1. `00_ONBOARDING.md`
2. `01_아키텍처와_흐름.md`
3. `02_실행계약과_전략규칙.md`
4. `03_운영과_환경.md`
5. `04_회귀기준과_점검.md`
6. `05_유지보수와_확장.md`
7. `CODEX_CONTEXT.md`

## Current Runtime Shape
- conversation owner: `apps/conversation/*`
- planner owner: `apps/planner/*`
- retrieval owner: `apps/retrieval/*`
- evidence owner: `apps/evidence/*`
- chat owner: `apps/chat/*`
- shared platform owner: `apps/platform/*`
- api composition root: `apps/api/*`

## Current Validation Truth
- active validation is smoke-only in this worktree:
  - `py_compile`
  - import smoke
  - `create_app()` smoke
- `tests/` is not the active validation lane here.
- `scripts/run_baseline_checks.ps1` is retired and historical only.

## Evidence Prompt Notes
- canonical prompt path is additive envelope packing.
- canonical memory path is compressed raw payload memory in conversation storage.
- the prompt artifact truth is `prompt_units`; serialized `context` is derived from it.
- follow-up fact resolution happens before evidence packing.

## Doc Sync Rule
- when runtime/contract owners move, patch the matching docs in the same change set.
- for large policy changes, add or update an ADR in `apps/docs/ADR/`.
