Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "."

function Invoke-PytestChecked {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$Arguments
  )

  & python @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "python exited with code $LASTEXITCODE"
  }
}

Write-Host "[baseline] collect-only"
Invoke-PytestChecked @(
  "-m", "pytest", "--collect-only", "-q",
  "--ignore-glob=pytest-cache-files-*",
  "--ignore-glob=tests/pytest-cache-files-*",
  "-p", "no:cacheprovider"
)

Write-Host "[baseline] core contract subset"
Invoke-PytestChecked @(
  "-m", "pytest", "-q",
  "tests/test_planner_stagewise.py",
  "tests/test_retrieval_workflow_detail_runtime.py",
  "tests/test_rag_anchor_truth.py",
  "tests/test_rag_anchor_truth_active_only.py",
  "tests/test_request_facade_followup_seed_priority.py",
  "tests/test_request_facade_strategy_meta_focus_entity.py",
  "tests/test_retrieval_workflow_detail_cache_gate.py",
  "tests/test_contract_debt_paydown.py",
  "-p", "no:cacheprovider"
)

Write-Host "[baseline] eval fixtures"
if (-not (Test-Path "eval/sample_queries.jsonl")) {
  throw "missing eval/sample_queries.jsonl"
}
if (-not (Test-Path "docs/PRODUCT_BASELINE.md")) {
  throw "missing docs/PRODUCT_BASELINE.md"
}
Invoke-PytestChecked @(
  "-m", "pytest", "-q", "tests/test_eval_fixture_schema.py", "-p", "no:cacheprovider"
)

Write-Host "[baseline] ok"