<#
.SYNOPSIS
  Install the AmicoScript meeting watcher as a per-user scheduled task that
  starts silently at logon. No admin rights required.

.DESCRIPTION
  Registers a Scheduled Task ("AmicoScript Meeting Watcher") that runs
  watcher.py with pythonw.exe (no console window) from this folder every time
  you log in, and starts it immediately. The watcher only records while the
  "Meeting auto-capture" toggle in the AmicoScript web UI is ON, so it is safe
  to leave running.

  Persistent per-user environment variables (set with `setx`, e.g.
  `setx AMICOSCRIPT_MODEL medium`) ARE inherited by the task. Transient
  variables set in a shell session are not — use setx for overrides.

.PARAMETER PythonW
  Path to pythonw.exe. Defaults to the pythonw.exe on PATH (or next to python.exe).

.PARAMETER TaskName
  Scheduled task name. Default "AmicoScript Meeting Watcher".

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
#>
param(
    [string]$PythonW = "",
    [string]$TaskName = "AmicoScript Meeting Watcher"
)

$ErrorActionPreference = "Stop"
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Definition }
$watcher = Join-Path $scriptDir "watcher.py"

if (-not (Test-Path $watcher)) {
    throw "watcher.py not found next to this installer ($watcher)."
}

# Locate pythonw.exe (windowless interpreter) so the watcher runs with no console.
if (-not $PythonW) {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        $PythonW = $cmd.Source
    } else {
        $py = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($py) { $PythonW = Join-Path (Split-Path $py.Source) "pythonw.exe" }
    }
}
if (-not $PythonW -or -not (Test-Path $PythonW)) {
    throw "pythonw.exe not found. Re-run with -PythonW 'C:\path\to\pythonw.exe'."
}

Write-Host "Interpreter : $PythonW"
Write-Host "Watcher     : $watcher"
Write-Host "Working dir : $scriptDir"

$action  = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$watcher`"" -WorkingDirectory $scriptDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Daemon: never auto-killed, survives battery transitions, starts if a logon was missed.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Installed and started scheduled task '$TaskName' (auto-starts at logon)." -ForegroundColor Green
Write-Host "Manage it:"
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host "  Stop-ScheduledTask  -TaskName `"$TaskName`""
Write-Host "  .\uninstall-windows.ps1"
Write-Host "Logs: $scriptDir\meetings\watcher.log (or `$env:AMICOSCRIPT_WATCHER_LOG)"
