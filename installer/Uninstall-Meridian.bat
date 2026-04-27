@echo off
REM Meridian uninstaller entry point.
REM Double-click this file to remove Meridian. You will be asked to allow
REM Administrator access -- click Yes.

echo.
echo Starting Meridian Uninstall...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Uninstall-Meridian.ps1"

echo.
pause
