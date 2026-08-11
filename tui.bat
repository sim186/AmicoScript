@echo off
rem Convenience launcher: ensures TUI deps are installed, then runs the TUI.
rem Usage: tui.bat [--api-url http://host:port] [--no-server] [--debug]
setlocal
cd /d "%~dp0"

if "%PYTHON%"=="" set PYTHON=python

"%PYTHON%" -c "import textual, httpx" >nul 2>&1
if errorlevel 1 (
  echo Installing TUI dependencies...
  "%PYTHON%" -m pip install -q -r tui\requirements.txt
  if errorlevel 1 exit /b 1
)

"%PYTHON%" -m tui %*
