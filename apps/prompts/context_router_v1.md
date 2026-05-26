<!--
Removed per ADR-0015 Stage 4 (Option B' stage 2).

The legacy deterministic pipeline that consumed this prompt
(turn_trigger -> turn_interpreter -> context_router -> turn_policy) has been
deleted. The prompt asset is left as an empty stub because the workspace
mount cannot delete files.

Production reference resolution now flows through the Dialogue Agent's
`call_tool` path (see `apps/prompts/dialogue_agent_v1.md`), with deterministic
follow-up parsing handled by `apps/conversation/followup_anchor.py` and
`apps/conversation/followup_resolution.py`.

See: apps/docs/reports/ADR-0015_implementation_audit_2026-04-22.md
-->
