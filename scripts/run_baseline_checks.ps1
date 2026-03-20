$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pythonFiles = @(
    "apps/api/routes.py",
    "apps/api/runtime.py",
    "apps/api/services/conversation_store.py",
    "apps/core/planner_staged.py",
    "tests/test_api_routes_runtime.py",
    "tests/test_planner_stagewise.py"
)

python -m py_compile @pythonFiles

try {
    python -m pytest `
        tests/test_api_routes_runtime.py `
        tests/test_planner_stagewise.py
} catch {
    if ($_.Exception.Message -like "*No module named pytest*") {
        Write-Host "pytest is not installed; py_compile completed but pytest step was skipped."
    } else {
        throw
    }
}




