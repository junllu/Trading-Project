# Operations health — checks every job's OUTPUT, alerts on any failure.
#
# Runs after the last job of the day (daily bars, 14:00). A Windows toast is
# raised when anything failed, because every stall so far was silent: tasks
# exited 0 while producing nothing. Details: data/ops_health.json and the log.

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"

$logDir = Join-Path $repo "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("ops_health_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  ops health ===" | Add-Content $log
$out = & (Join-Path $repo ".venv\Scripts\python.exe") -m app.agent.ops_health 2>&1
$code = $LASTEXITCODE
$out | ForEach-Object { "$_" } | Add-Content $log
"=== exit $code ===" | Add-Content $log

# Daily summary for remote reading — built after the health check so it
# includes today's verdicts. A report failure never changes the exit code.
& (Join-Path $repo ".venv\Scripts\python.exe") -m app.reports.daily_summary 2>&1 | Out-Null

if ($code -ne 0) {
  $failed = ($out | Where-Object { "$_" -match "^\s+FAIL" } | ForEach-Object { ("$_" -replace "^\s+FAIL\s+", "").Trim() }) -join "`n"
  try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
      [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $t = $xml.GetElementsByTagName("text")
    $t.Item(0).AppendChild($xml.CreateTextNode("Trading Portal: job(s) failed")) | Out-Null
    $t.Item(1).AppendChild($xml.CreateTextNode($failed)) | Out-Null
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show(
      [Windows.UI.Notifications.ToastNotification]::new($xml))
  } catch {
    "toast failed: $($_.Exception.Message)" | Add-Content $log
  }
}
exit $code
