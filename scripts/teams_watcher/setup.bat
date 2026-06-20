@echo off
REM ==========================================================================
REM  AmicoScript meeting watcher — one-click setup (double-click this file).
REM
REM  Installs the watcher's Python dependencies and registers it to start
REM  silently at every logon. No admin rights required. After this runs once,
REM  recording is controlled entirely from the AmicoScript web UI toggle.
REM
REM  Works two ways:
REM   - run from the project (scripts\teams_watcher\) -> uses the files here.
REM   - downloaded on its own           -> fetches the rest from the running app
REM                                         (set AMICO_URL to override the URL).
REM ==========================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

if "%AMICO_URL%"=="" set "AMICO_URL=http://localhost:8002"
set "SRC=%~dp0"

echo === AmicoScript meeting watcher setup ===
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python not found on PATH.
  echo Install Python 3 first: https://www.python.org/downloads/
  echo Tick "Add Python to PATH" in the installer, then re-run this file.
  echo.
  pause
  exit /b 1
)

REM If the watcher files aren't next to this script, download them from the app.
if not exist "%SRC%watcher.py" (
  set "SRC=%LOCALAPPDATA%\AmicoScript\watcher\"
  echo Fetching watcher files from %AMICO_URL% ...
  if not exist "!SRC!" mkdir "!SRC!"
  for %%F in (watcher.py requirements.txt install-windows.ps1 uninstall-windows.ps1 diag.py logo.ico) do (
    powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri '%AMICO_URL%/scripts/teams_watcher/%%F' -OutFile '!SRC!%%F' } catch { exit 1 }"
    if errorlevel 1 (
      echo ERROR: could not download %%F from %AMICO_URL%.
      echo Make sure AmicoScript is running, then re-run this file.
      pause
      exit /b 1
    )
  )
)

echo [1/2] Installing dependencies...
python -m pip install --quiet --disable-pip-version-check -r "!SRC!requirements.txt"
if errorlevel 1 (
  echo.
  echo ERROR: dependency install failed ^(see messages above^).
  pause
  exit /b 1
)

echo [2/2] Registering autostart ^(runs at logon, no admin^)...
powershell -ExecutionPolicy Bypass -NoProfile -File "!SRC!install-windows.ps1"
if errorlevel 1 (
  echo.
  echo ERROR: autostart registration failed.
  pause
  exit /b 1
)

echo.
echo Done. The watcher is running now and will auto-start at every logon.
echo Turn recording on/off from the AmicoScript web UI ^(Meeting auto-capture^).
echo You can close this window.
echo.
pause
