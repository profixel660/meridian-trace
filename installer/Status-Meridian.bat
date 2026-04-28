@echo off
REM Status-Meridian.bat -- alpha-12 launcher
REM
REM One-shot diagnostic: probes /health and /setup/runtime, surfaces the
REM PID, uptime, version, log paths, and the most-recent import job
REM snapshot. Replaces the manual `Get-Process python` + `Get-Content
REM backend.log` triage sequence we walked manually during the alpha-11
REM SME run.

setlocal enableextensions

set "MERIDIAN_HEALTH_URL=http://127.0.0.1:8000/health"
set "MERIDIAN_RUNTIME_URL=http://127.0.0.1:8000/setup/runtime"
set "MERIDIAN_PID_FILE=C:\Meridian\runtime\backend.pid"

echo.
echo ================================================================
echo                          M E R I D I A N  --  S T A T U S
echo ================================================================
echo.

REM --- 1. /health
echo [HEALTH]
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '%MERIDIAN_HEALTH_URL%' -UseBasicParsing -TimeoutSec 2; Write-Host ('         status_code: ' + $r.StatusCode); Write-Host ('         body       : ' + $r.Content) } catch { Write-Host '         <no response>  -- backend not running.'; exit 1 }"
if errorlevel 1 (
    echo.
    echo [PID FILE]
    if exist "%MERIDIAN_PID_FILE%" (
        type "%MERIDIAN_PID_FILE%"
        echo          ^(stale PID file -- backend isn't actually responding^)
    ) else (
        echo          ^(no PID file at %MERIDIAN_PID_FILE%^)
    )
    echo.
    echo Run Start-Meridian.bat to start the backend.
    exit /b 1
)
echo.

REM --- 2. /setup/runtime (alpha-12 endpoint)
echo [RUNTIME]
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '%MERIDIAN_RUNTIME_URL%' -UseBasicParsing -TimeoutSec 2; $j = $r.Content | ConvertFrom-Json; Write-Host ('         pid             : ' + $j.pid); Write-Host ('         version         : ' + $j.version); Write-Host ('         python_version  : ' + $j.python_version); Write-Host ('         platform        : ' + $j.platform); Write-Host ('         started_at      : ' + $j.started_at); Write-Host ('         uptime_seconds  : ' + [math]::Round($j.uptime_seconds, 1)); Write-Host ('         backend_log     : ' + $j.backend_log_path); Write-Host ('         structlog_dir   : ' + $j.structlog_dir); if ($j.last_import_job) { Write-Host ''; Write-Host '         last import job:'; Write-Host ('           job_id       : ' + $j.last_import_job.job_id); Write-Host ('           status       : ' + $j.last_import_job.status); Write-Host ('           progress     : ' + $j.last_import_job.completed + '/' + $j.last_import_job.total); Write-Host ('           imported     : ' + $j.last_import_job.imported); Write-Host ('           deduped      : ' + $j.last_import_job.deduped); Write-Host ('           failed_count : ' + $j.last_import_job.failed_count); if ($j.last_import_job.current_file) { Write-Host ('           current_file : ' + $j.last_import_job.current_file) } } else { Write-Host ''; Write-Host '         last import job: <none this process>' } } catch { Write-Host ('         <runtime endpoint unavailable -- ' + $_.Exception.Message + '>') }"
echo.
exit /b 0
