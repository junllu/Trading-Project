# Daily cycle (plan + paper execution) — runs without the dashboard.
#
# Before this, the 09:00 cycle lived only inside the dashboard process, so a
# closed window or a sleeping PC skipped the day silently. The Python side
# (app/agent/scheduled.py) refuses LIVE mode and claims the day atomically, so
# the dashboard's own scheduler and this task can never both run it.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"

$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily_cycle_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  daily cycle ===" | Add-Content $log
try {
  $out = & (Join-Path $repo ".venv\Scripts\python.exe") -m app.agent.scheduled 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { "$_" } | Add-Content $log
  "=== exit $code ===" | Add-Content $log
  exit $code
} catch {
  "FAILED: $($_.Exception.Message)" | Add-Content $log
  exit 1
}
