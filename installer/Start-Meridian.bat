@echo off
REM Start-Meridian.bat -- alpha-12 launcher
REM
REM Idempotent: if /health already returns 200 we just open the browser and
REM exit. Otherwise spawn pythonw.exe detached (no console = immune to
REM terminal-close events) and poll /health up to 60 seconds.
REM
REM Why .bat (not .ps1): default Windows ExecutionPolicy blocks unsigned
REM .ps1 scripts ("running scripts is disabled on this system"). .bat has
REM no equivalent restriction. The user double-clicks this from Explorer
REM or invokes it from any shell with no setup.

setlocal enableextensions

set "MERIDIAN_ROOT=C:\Meridian"
set "MERIDIAN_VENV=%MERIDIAN_ROOT%\venv"
set "MERIDIAN_RUNTIME=%MERIDIAN_ROOT%\runtime"
set "MERIDIAN_BACKEND_LOG=%MERIDIAN_RUNTIME%\backend.log"
set "MERIDIAN_PID_FILE=%MERIDIAN_RUNTIME%\backend.pid"
set "MERIDIAN_PYTHONW=%MERIDIAN_VENV%\Scripts\pythonw.exe"
set "MERIDIAN_PYTHON=%MERIDIAN_VENV%\Scripts\python.exe"
set "MERIDIAN_HEALTH_URL=http://127.0.0.1:8000/health"
set "MERIDIAN_WIZARD_URL=http://127.0.0.1:8000/setup/"

echo.
echo ================================================================
echo                          M E R I D I A N
echo ================================================================
echo.

REM --- 0. Sanity: install present?
if not exist "%MERIDIAN_PYTHONW%" if not exist "%MERIDIAN_PYTHON%" (
    echo [ERROR] Meridian is not installed at %MERIDIAN_ROOT%.
    echo         Run Install-Meridian.bat first.
    pause
    exit /b 1
)

REM --- 1. Already running? Probe /health silently.
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '%MERIDIAN_HEALTH_URL%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Meridian is already running.
    echo      Opening %MERIDIAN_WIZARD_URL% in your default browser...
    start "" "%MERIDIAN_WIZARD_URL%"
    exit /b 0
)

REM --- 2. Ensure runtime dir exists.
if not exist "%MERIDIAN_RUNTIME%" mkdir "%MERIDIAN_RUNTIME%"

REM --- 3. Spawn pythonw.exe detached. Fall back to python.exe (visible
REM       console) if pythonw is unavailable.
set "MERIDIAN_PYBIN=%MERIDIAN_PYTHONW%"
if not exist "%MERIDIAN_PYBIN%" (
    echo [WARN] pythonw.exe not found; falling back to python.exe (visible console).
    set "MERIDIAN_PYBIN=%MERIDIAN_PYTHON%"
)

echo [...] Starting Meridian backend (this can take ~30 seconds on first run).
echo       Log file: %MERIDIAN_BACKEND_LOG%
echo.

REM Alpha-15: reverted to alpha-12 simplicity. The alpha-14 .env-loader was
REM only needed for the MERIDIAN_AUTH_DISABLED bypass, which alpha-15 retired
REM (TOTP enforcement removed by default; product validation comes first).
REM No more multi-line PowerShell argument inside cmd.exe; no more `^` line
REM continuation; no more parser-vs-quoting minefield.
set "MERIDIAN_BACKEND_LOG=%MERIDIAN_BACKEND_LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:MERIDIAN_BACKEND_LOG = '%MERIDIAN_BACKEND_LOG%'; $p = Start-Process -FilePath '%MERIDIAN_PYBIN%' -ArgumentList @('-m','meridian.api.main') -WorkingDirectory '%MERIDIAN_ROOT%' -WindowStyle Hidden -PassThru; Set-Content -LiteralPath '%MERIDIAN_PID_FILE%' -Value $p.Id -Encoding ASCII; Write-Host ('       PID: ' + $p.Id)"
if errorlevel 1 (
    echo [ERROR] Could not spawn the backend. See %MERIDIAN_BACKEND_LOG% for details.
    pause
    exit /b 1
)

REM --- 4. Poll /health for up to 60 seconds.
echo [...] Waiting for the backend to come up at %MERIDIAN_HEALTH_URL%...
set /a _attempts=0
:wait_loop
set /a _attempts+=1
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '%MERIDIAN_HEALTH_URL%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 goto healthy
if %_attempts% geq 240 goto unhealthy
REM Print a dot every 4 attempts (~1s) so the user knows we're alive.
set /a _mod=%_attempts% %% 4
if %_mod% equ 0 <nul set /p ".=."
ping -n 1 -w 250 127.0.0.1 >nul
goto wait_loop

:healthy
echo.
echo [OK] Backend is healthy.
echo      Opening %MERIDIAN_WIZARD_URL% in your default browser...
start "" "%MERIDIAN_WIZARD_URL%"
exit /b 0

:unhealthy
echo.
echo [ERROR] Backend did not become healthy within 60 seconds.
echo         Last 20 lines of %MERIDIAN_BACKEND_LOG%:
echo         ----------------------------------------------
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path '%MERIDIAN_BACKEND_LOG%') { Get-Content '%MERIDIAN_BACKEND_LOG%' -Tail 20 } else { Write-Host '         (no log file at %MERIDIAN_BACKEND_LOG%)' }"
echo         ----------------------------------------------
echo         You can also try running the backend in the foreground for
echo         live error output:
echo           "%MERIDIAN_PYTHON%" -m meridian.api.main
pause
exit /b 1
