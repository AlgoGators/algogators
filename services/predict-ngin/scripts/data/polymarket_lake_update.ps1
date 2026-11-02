<#
.SYNOPSIS
  Weekly Polymarket lake refresh on the PC: convert the raw OrderFilled days that atlas fetched (pn-pmtail,
  copied here by atlas_sync) into fills, then rebuild the affected lake months. Same as
  `uv run predict-ngin data update polymarket`. A day atlas missed is fetched here from public Polygon RPC.
.DESCRIPTION
  Run by the "predict-ngin-polymarket-lake" scheduled task; install / remove it with
  scripts\data\install_polymarket_lake_update.ps1. Skipped while data\polymarket_clean\STOP exists.
  Log: data\logs\polymarket_lake_update.log
#>
param([string]$Uv = "uv")
$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root
$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "polymarket_lake_update.log"
function Write-Log($msg) { Add-Content -Path $Log -Value ("{0:u} {1}" -f (Get-Date).ToUniversalTime(), $msg) }

if (Test-Path (Join-Path $Root "data\polymarket_clean\STOP")) { Write-Log "STOP file present; skipped"; exit 0 }
Write-Log "start"
# Out-File, not *>>: Windows PowerShell redirection writes UTF-16
& $Uv run predict-ngin data update polymarket 2>&1 | ForEach-Object { "$_" } | Out-File -FilePath $Log -Append -Encoding utf8
$rc = $LASTEXITCODE
Write-Log "done exit $rc"
exit $rc
