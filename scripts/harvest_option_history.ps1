# Historical option-price harvest for the single-stock IV-spike pilot.
# Read-only Claude session: only Robinhood get_option_instruments / get_option_historicals,
# every order tool explicitly denied. Loops in batches until every planned task has a file.

param([int]$Batch = 12, [int]$MaxRuns = 25)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("option_history_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

$prompt = (Get-Content -Raw (Join-Path $PSScriptRoot "harvest_option_history.prompt.md")).Replace("{MAX_TASKS}", "$Batch")
$allowed = @("Read", "Write", "Bash",
             "mcp__robinhood-trading__get_option_instruments",
             "mcp__robinhood-trading__get_option_historicals")
$denied = @("mcp__robinhood-trading__place_equity_order", "mcp__robinhood-trading__place_option_order",
            "mcp__robinhood-trading__place_crypto_order", "mcp__robinhood-trading__cancel_equity_order",
            "mcp__robinhood-trading__cancel_option_order", "mcp__robinhood-trading__exercise_option",
            "mcp__claude_ai_Webull__place_stock_instruction",
            "mcp__claude_ai_Webull__place_option_single_instruction",
            "mcp__claude_ai_Webull__place_option_strategy_instruction")

for ($i = 1; $i -le $MaxRuns; $i++) {
  $st = & (Join-Path $repo ".venv\Scripts\python.exe") -c "import json;from app.options.iv_backtest import status;s=status();print(s['planned']-s['harvested'])"
  if ([int]$st -le 0) { "=== all tasks harvested ===" | Add-Content $log; break }
  "=== $(Get-Date -Format 'HH:mm:ss') run $i, remaining $st ===" | Add-Content $log
  $out = & claude -p $prompt --model claude-sonnet-5 --allowedTools @allowed --disallowedTools @denied 2>&1
  $out | Add-Content $log
}
