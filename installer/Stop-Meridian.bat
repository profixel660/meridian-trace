@echo off
REM Stop-Meridian.bat -- alpha-12 launcher
REM
REM Reads the PID file and stops that process tree. Idempotent: safe to
REM run when the backend is already stopped (just reports "not running").
REM
REM IMPLEMENTATION NOTE: cmd.exe parse-time expansion makes
REM `if exist (set /p _pid=... && %_pid%)` a trap -- %_pid% gets the
REM EMPTY-string value it had when the block was parsed, not the value
REM after `set /p` runs. Fix: linear goto-based structure (no parenthesised
REM blocks reading freshly-set variables). The alpha-12 reviewer caught
REM this; the original code silently fell through to the port fallback
REM and never used the PID file.

setlocal enableextensions

set "MERIDIAN_ROOT=C:\Meridian"
set "MERIDIAN_RUNTIME=%MERIDIAN_ROOT%\runtime"
set "MERIDIAN_PID_FILE=%MERIDIAN_RUNTIME%\backend.pid"
set "MERIDIAN_HEALTH_URL=http://127.0.0.1:8000/health"

echo.
echo ================================================================
echo                          M E R I D I A N  --  S T O P
echo ================================================================
echo.

REM --- 1. Try the PID file first (most reliable).
if not exist "%MERIDIAN_PID_FILE%" goto no_pid
set "_pid="
set /p _pid=<"%MERIDIAN_PID_FILE%"
if "%_pid%"=="" goto no_pid
echo [...] Stopping backend PID %_pid% (per %MERIDIAN_PID_FILE%)...
REM /T = also terminate child processes; /F = force.
taskkill /PID %_pid% /T /F >nul 2>&1
if errorlevel 1 goto taskkill_failed
echo [OK] Stopped.
del "%MERIDIAN_PID_FILE%" >nul 2>&1
exit /b 0

:taskkill_failed
echo [WARN] taskkill failed for PID %_pid% (already gone?). Falling through to port-based fallback.

:no_pid

REM --- 2. Fallback: find whatever python.exe / pythonw.exe is bound to :8000
REM       and stop it. Useful when the PID file went stale.
echo [...] Looking for a process on :8000...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Where-Object State -eq 'Listen' | Select-Object -First 1; if ($p) { Write-Host ('       PID: ' + $p.OwningProcess); Stop-Process -Id $p.OwningProcess -Force -ErrorAction Stop; Write-Host '[OK] Stopped.' } else { Write-Host '[OK] Nothing listening on :8000 -- already stopped.' }"
if exist "%MERIDIAN_PID_FILE%" del "%MERIDIAN_PID_FILE%" >nul 2>&1
exit /b 0
