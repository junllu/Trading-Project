# Daily-bar refresh — plain Python, no Claude session.
#
# Unlike the minute harvest, this needs no connector: yfinance is reachable
# in-process, so the run is free and takes ~30s for the whole cache. Scheduled
# after the US close; the task is set to StartWhenAvailable so a run missed
# while the machine slept fires on wake instead of silently skipping a day.
#
# Reads market data and rewrites local CSV caches only. No broker is touched.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily_bars_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  daily-bar refresh ===" | Add-Content $log
try {
  $out = & (Join-Path $repo ".venv\Scripts\python.exe") -m app.data.refresh 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { "$_" } | Add-Content $log
  "=== exit $code ===" | Add-Content $log
  exit $code
} catch {
  "FAILED: $($_.Exception.Message)" | Add-Content $log
  exit 1
}
