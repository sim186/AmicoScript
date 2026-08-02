<#
.SYNOPSIS
  Stop and remove the AmicoScript meeting watcher scheduled task.
#>
param([string]$TaskName = "AmicoScript Meeting Watcher")

$ErrorActionPreference = "Stop"

# Kill any watcher process first. Unregistering the task alone leaves an
# already-running pythonw.exe alive until logoff — it would keep recording
# meetings after the user thinks they uninstalled it.
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*watcher.py*" } |
    ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    }

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Task '$TaskName' not found — nothing to do."
    return
}

try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
