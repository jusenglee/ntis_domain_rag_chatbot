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

function Get-RepoManifestSection {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Section
  )

  $json = & python -m apps.api.contracts.repo_manifest --section $Section
  if ($LASTEXITCODE -ne 0) {
    throw "repo manifest lookup failed with code $LASTEXITCODE"
  }
  return $json | ConvertFrom-Json
}

$baseline = Get-RepoManifestSection -Section "baseline_inventory"
$collectOnlyArgs = @($baseline.collect_only_pytest_args | ForEach-Object { [string]$_ })
$sharedPytestArgs = @($baseline.shared_pytest_args | ForEach-Object { [string]$_ })
$coreContractSubset = @($baseline.core_contract_subset | ForEach-Object { [string]$_ })
$evalFixtureTests = @($baseline.eval_fixture_tests | ForEach-Object { [string]$_ })
$requiredFiles = @($baseline.required_files | ForEach-Object { [string]$_ })

Write-Host "[baseline] collect-only"
Invoke-PytestChecked -Arguments (@("-m", "pytest") + $collectOnlyArgs)

Write-Host "[baseline] core contract subset"
Invoke-PytestChecked -Arguments (@("-m", "pytest", "-q") + $coreContractSubset + $sharedPytestArgs)

Write-Host "[baseline] eval fixtures"
foreach ($requiredPath in $requiredFiles) {
  if (-not (Test-Path $requiredPath)) {
    throw "missing $requiredPath"
  }
}
Invoke-PytestChecked -Arguments (@("-m", "pytest", "-q") + $evalFixtureTests + $sharedPytestArgs)

Write-Host "[baseline] ok"
