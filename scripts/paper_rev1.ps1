# rev1 paper strategy — the close job (fills, exits, new signals) or the
# intraday job (shadow entries, pre-close preview, stop alerts).
# Paper only: this never touches a broker.

param([ValidateSet("close", "intraday")][string]$Job = "close")

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"

$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("paper_rev1_{0}_{1}.log" -f $Job, (Get-Date -Format "yyyy-MM-dd"))

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  rev1 $Job ===" | Add-Content $log
try {
  $out = & (Join-Path $repo ".venv\Scripts\python.exe") -m app.paper.rev1 $Job 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { "$_" } | Add-Content $log
  "=== exit $code ===" | Add-Content $log
  if ($Job -eq "close") {
    # Monthly tracks (trend1, mom1) mark daily and decide on month-end closes.
    $m = & (Join-Path $repo ".venv\Scripts\python.exe") -m app.paper.monthly 2>&1
    $m | ForEach-Object { "$_" } | Add-Content $log
    "=== monthly exit $LASTEXITCODE ===" | Add-Content $log
  }
  exit $code
} catch {
  "FAILED: $($_.Exception.Message)" | Add-Content $log
  exit 1
}
