<#
.SYNOPSIS
  Stop and remove the AmicoScript meeting watcher scheduled task.
#>
param([string]$TaskName = "AmicoScript Meeting Watcher")

$ErrorActionPreference = "Stop"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Task '$TaskName' not found — nothing to do."
    return
}

try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
