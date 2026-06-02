# Validation And Review

Use this file as the review checklist for changes to the agent chatbot.

## Minimal Test Commands

Run targeted tests first:

```powershell
python -m pytest tests/pipeline/test_planner_phase2.py -q
python -m pytest tests/pipeline/test_tools_phase1.py -q
python -m pytest tests/pipeline/test_agentic_workflow_phase3.py -q
python -m pytest tests/pipeline/test_thinking_mode_phase5.py -q
python -m pytest tests/pipeline/test_session_state.py -q
```

Then run the broad non-integration suite:

```powershell
python -m pytest tests/ -q --ignore=tests/integration
```

## Current Pipeline Test Areas

Source folder: `tests/pipeline`

Important files:

- `test_planner_phase2.py`
- `test_active_planner_phase5.py`
- `test_agentic_workflow_phase3.py`
- `test_agentic_golden_suite.py`
- `test_tools_phase1.py`
- `test_search_task_contract.py`
- `test_session_state.py`
- `test_thinking_mode_phase5.py`
- `test_regression_incidents.py`

## Review Checklist

For Planner changes:

- Pass 1 prompt must not expose full args schemas.
- Pass 2 prompt must expose only the selected tool schema.
- Unknown tools must not reach `ToolExecutor`.
- `action` must remain one of `call_tool`, `answer`, `clarify`.
- Direct answer behavior must not accidentally bypass domain safety.

For tool changes:

- Add a `ToolSpec`.
- Add a handler that returns a dict.
- Register in `build_default_registry`.
- Add tool args -> `SearchTask` conversion tests when search is involved.
- Avoid raw payloads in tool results that are later prompt-facing.

For search changes:

- Preserve `pjt_id` / `pjt_no` distinction.
- Preserve typed filters.
- Keep `SearchAgent` from reinterpreting user intent.
- Check canonical evidence output.

For answer/critic changes:

- Verify `GuardDecision` routing.
- Verify repair does not loop indefinitely.
- Verify references and manifest ranks map to visible evidence.
- Verify grounding judge failure becomes safe fallback.

For session changes:

- Check subject, manifest, and focused detail behavior independently.
- Check KV round-trip, not only in-memory `SessionState`.
- Be explicit if the current `SessionMemory` compression path is still in use.

## Review Output Format

Findings should lead.

Each finding should include:

- severity,
- source file and line,
- behavioral risk,
- concrete fix.

Summaries and follow-ups come after findings.
