# Intraday minute-bar harvest — headless Claude Code run.
#
# WHY A CLAUDE SESSION FOR A DATA FETCH
#
# It is the only path that works. The portal's own `minute.fetch()` hits the
# Webull OpenAPI SDK and gets 403: that key has no OpenAPI market-data
# subscription, and an Advanced Quotes sub bought in the Webull app or desktop
# does not apply to OpenAPI. The claude.ai Webull connector reads the same bars
# at delay_minutes=0, but connectors are reachable only from inside a session.
#
# So this costs a model session per refresh, which makes cadence a budget
# question rather than a technical one. Buying the OpenAPI Advanced Quotes
# subscription would let the portal harvest in-process, continuously, at no
# per-cycle cost — at which point this script should be deleted, not kept.
#
# -MarketHoursOnly: skip when US equities are closed. Harvesting a closed
# market re-fetches bars already stored and spends a session to learn nothing.

param([switch]$MarketHoursOnly)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if ($MarketHoursOnly) {
  # RTH is 13:30-20:00 UTC; DST shifts it, so the window is deliberately wide
  # at both ends rather than precise. An extra run at the edge is cheap; a
  # missed session close is not.
  $utc = (Get-Date).ToUniversalTime()
  $mins = $utc.Hour * 60 + $utc.Minute
  $isWeekday = [int]$utc.DayOfWeek -ge 1 -and [int]$utc.DayOfWeek -le 5
  if (-not $isWeekday -or $mins -lt 800 -or $mins -gt 1265) {
    Write-Output "outside US market hours ($($utc.ToString('ddd HH:mm')) UTC) - skipping"
    exit 0
  }
}

$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("minute_harvest_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

$prompt = Get-Content -Raw (Join-Path $PSScriptRoot "harvest_minute_bars.prompt.md")

# Read-only market data plus the local ingester. No order tool is on the
# allowlist, and the order tools are denied explicitly so that a later edit
# widening the allowlist still cannot place a trade.
$allowed = @(
  "Read", "Write", "Bash", "Glob", "Grep",
  "mcp__claude_ai_Webull__get_stock_bars"
)
$denied = @(
  "mcp__robinhood-trading__place_equity_order",
  "mcp__robinhood-trading__place_option_order",
  "mcp__robinhood-trading__place_crypto_order",
  "mcp__robinhood-trading__cancel_equity_order",
  "mcp__robinhood-trading__cancel_option_order",
  "mcp__robinhood-trading__exercise_option"
)

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  minute harvest ===" | Add-Content $log

try {
  $out = & claude -p $prompt `
      --model claude-sonnet-5 `
      --allowedTools @allowed `
      --disallowedTools @denied 2>&1
  $out | Add-Content $log
  "=== exit $LASTEXITCODE ===" | Add-Content $log
  exit $LASTEXITCODE
} catch {
  "FAILED: $($_.Exception.Message)" | Add-Content $log
  exit 1
}
