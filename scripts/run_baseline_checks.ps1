Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Error @"
scripts/run_baseline_checks.ps1 is a retired validation entrypoint.

Current repo state no longer treats manifest-driven pytest/baseline runs as the active gate:
- the tracked tests tree is intentionally removed in this worktree
- current validation is limited to py_compile, import smoke, create_app() smoke,
  and optional workflow graph smoke when langgraph is available

See:
- README.md
- apps/docs/03_운영과_환경.md
- apps/docs/04_회귀기준과_점검.md
- apps/docs/SESSION_HANDOFF.md
"@

exit 1
