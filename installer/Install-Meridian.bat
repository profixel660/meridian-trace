@echo off
REM Meridian one-click installer entry point.
REM Double-click this file to install Meridian. You will be asked to allow
REM Administrator access -- click Yes.

echo.
echo Starting Meridian Setup...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Meridian.ps1"

echo.
echo Setup window closed. You can close this window now.
pause
