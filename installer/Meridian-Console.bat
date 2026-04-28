@echo off
REM Meridian-Console.bat -- alpha-12 wrapper around Meridian-Console.ps1
REM
REM The Desktop shortcut points here. ALPHA-12 fix: alpha-11 pointed the
REM shortcut at Meridian-Console.ps1 directly; that triggered the default
REM Windows ExecutionPolicy block ("running scripts is disabled on this
REM system") on a clean install. Wrapping with `powershell.exe
REM -ExecutionPolicy Bypass -File ...` bypasses the per-user policy
REM cleanly without changing it system-wide.

setlocal enableextensions

set "MERIDIAN_ROOT=C:\Meridian"
set "MERIDIAN_CONSOLE_PS1=%MERIDIAN_ROOT%\Meridian-Console.ps1"

if not exist "%MERIDIAN_CONSOLE_PS1%" (
    echo [ERROR] %MERIDIAN_CONSOLE_PS1% missing -- re-run Install-Meridian.bat.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MERIDIAN_CONSOLE_PS1%"
exit /b %errorlevel%
