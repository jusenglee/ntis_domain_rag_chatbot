# Removed per ADR-0015 Stage 4 (Option B' stage 2).
#
# This module was part of the legacy deterministic pipeline
# (turn_trigger -> turn_interpreter -> context_router -> turn_policy) and is
# no longer reachable from the production agentic workflow. The file is left
# as an intentional empty stub because the workspace mount cannot delete
# files; do not import it.
#
# Replacement entry points:
#   - apps.conversation.agent_tool_executor
#   - apps.conversation.request_facade.build_agent_intent_payload
#
# See: apps/docs/reports/ADR-0015_implementation_audit_2026-04-22.md
