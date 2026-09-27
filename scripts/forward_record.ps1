# Forward record — one signal snapshot per trading day, near the close.
#
# The evidence the capital gate grades (data/forward_record.jsonl). It was only
# written by the dashboard's in-process live loop, so it stopped on 2026-09-16
# the same way the daily plan did. The gate now counts one signal per name per
# DAY, so a 5-minute cadence adds rows but no evidence; one snapshot shortly
# before the close matches the backtest's signal-at-close convention.
#
# --max-cycles 1 records only while the market is open: a woken-late run after
# the close, or a holiday, records nothing rather than a fake trading day.
# live_runner never places orders (DailyAgent execute=False).

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"

$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("forward_record_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  forward record ===" | Add-Content $log
try {
  $out = & (Join-Path $repo ".venv\Scripts\python.exe") -m app.agent.live_runner --max-cycles 1 2>&1
  $code = $LASTEXITCODE
  $out | ForEach-Object { "$_" } | Add-Content $log
  "=== exit $code ===" | Add-Content $log
  exit $code
} catch {
  "FAILED: $($_.Exception.Message)" | Add-Content $log
  exit 1
}
