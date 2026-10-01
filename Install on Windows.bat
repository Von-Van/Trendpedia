@echo off
REM Double-click this file to set up Trendpedia and add shortcuts.
cd /d "%~dp0"

set PYTHON=
for %%P in (python.exe py.exe) do (
  if not defined PYTHON where %%P >nul 2>&1 && set PYTHON=%%P
)

if not defined PYTHON (
  echo.
  echo Python 3 is required but was not found.
  echo.
  echo Install it from https://www.python.org/downloads/
  echo Be sure to tick "Add Python to PATH" during installation,
  echo then double-click this file again.
  echo.
  pause
  exit /b 1
)

%PYTHON% scripts\install_desktop.py
echo.
if errorlevel 1 (
  echo Setup did not finish. The messages above explain why.
) else (
  echo Setup complete. You can close this window.
)
pause
