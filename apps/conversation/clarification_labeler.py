# Removed per ADR-0015 Stage 4 (Option B' stage 2).
#
# This module's only consumer was the legacy `turn_interpreter`. Production
# clarification messages are now composed by `clarification_prose.py`, which
# is invoked from `scope_resolver.py` and `followup_resolution.py`. The file
# is left as an intentional empty stub because the workspace mount cannot
# delete files; do not import it.
#
# See: apps/docs/reports/ADR-0015_implementation_audit_2026-04-22.md
