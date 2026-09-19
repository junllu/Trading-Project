# Daily Tier-2 research pulls — headless Claude Code run.
#
# WHY LOCAL AND NOT A CLOUD ROUTINE
#
# This job cannot run in the cloud, for three independent reasons:
#   1. the `robinhood-trading` MCP is a local Claude Code server, not a
#      claude.ai connector, so a cloud routine cannot call it at all;
#   2. `data/` is git-ignored in full, so a fresh cloud checkout has no
#      research brief to read the queue from and no prices to rebuild one;
#   3. the snapshot it produces lands in `data/fundamentals/`, which is also
#      ignored — a cloud run could not commit its own output back.
# Both the data and the broker connection are local by design. So is this.
#
# SAFETY: the tool allowlist below is read-only market data plus file access.
# No order tool is on it, and the order tools are denied explicitly as well.
# An allowlist alone would be enough; the denylist is there so that a future
# edit widening the allowlist still cannot place a trade by accident.

# -IfNeeded: exit immediately when today's snapshot already exists. launch.bat
# passes this, because the launcher can run many times a day and each bare run
# would spin up a Claude session to conclude there was nothing to do. The check
# is pure filesystem — no session is started just to find out.
param([switch]$IfNeeded)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if ($IfNeeded) {
  $todaySnap = Join-Path $repo ("data\fundamentals\{0}.json" -f (Get-Date -Format "yyyy-MM-dd"))
  if (Test-Path $todaySnap) {
    Write-Output "today's fundamentals snapshot already exists - skipping"
    exit 0
  }
}

$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd"
$log = Join-Path $logDir "mcp_pulls_$stamp.log"

$prompt = Get-Content -Raw (Join-Path $PSScriptRoot "daily_mcp_pulls.prompt.md")

$allowed = @(
  "Read", "Write", "Edit", "Glob", "Grep", "Bash",
  "mcp__robinhood-trading__get_equity_fundamentals",
  "mcp__robinhood-trading__get_financials"
)
$denied = @(
  "mcp__robinhood-trading__place_equity_order",
  "mcp__robinhood-trading__place_option_order",
  "mcp__robinhood-trading__place_crypto_order",
  "mcp__robinhood-trading__cancel_equity_order",
  "mcp__robinhood-trading__cancel_option_order",
  "mcp__robinhood-trading__cancel_crypto_order",
  "mcp__robinhood-trading__exercise_option"
)

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  daily MCP pulls ===" | Add-Content $log

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
