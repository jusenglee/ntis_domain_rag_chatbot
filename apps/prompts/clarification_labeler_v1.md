<!--
Removed per ADR-0015 Stage 4 (Option B' stage 2).

This prompt was loaded only by `apps/conversation/clarification_labeler.py`,
which itself was a helper of the legacy `turn_interpreter` pipeline. Both
have been deleted. The prompt asset is left as an empty stub because the
workspace mount cannot delete files.

Production clarification copy is now produced by
`apps/conversation/clarification_prose.py` via
`apps/prompts/clarification_prose_v1.md`.

See: apps/docs/reports/ADR-0015_implementation_audit_2026-04-22.md
-->
