<#
.SYNOPSIS
  Register (or remove) the "predict-ngin-polymarket-lake" scheduled task that runs
  scripts\data\polymarket_lake_update.ps1 weekly (Sunday 03:00 local by default). atlas fetches the raw days
  daily; this only converts them and rebuilds the lake. If the PC was asleep or off, it runs at the next chance.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\data\install_polymarket_lake_update.ps1           # install / update
  powershell -ExecutionPolicy Bypass -File scripts\data\install_polymarket_lake_update.ps1 -Remove   # uninstall
#>
param([switch]$Remove, [string]$At = "03:00", [string]$Day = "Sunday")
$ErrorActionPreference = "Stop"
$Name = "predict-ngin-polymarket-lake"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if ($Remove) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "removed task $Name"
    exit 0
}

# The task's environment may not have the user's PATH, so pin uv's full path now.
$Uv = (Get-Command uv -ErrorAction Stop).Source
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Root\scripts\data\polymarket_lake_update.ps1`" -Uv `"$Uv`"" `
    -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $At
# Converting a synced day takes ~1 min and a lake month rebuild ~3; 4 h also covers fetching missed days here.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew

# S4U = runs whether or not the user is logged on, no stored password. Needs an elevated shell;
# falls back to "only when logged on" otherwise.
try {
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Force | Out-Null
    Write-Output "installed $Name ($Day $At, also when logged off)"
} catch {
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Force | Out-Null
    Write-Output "installed $Name ($Day $At while logged on; run elevated to also cover logged-off)"
}
