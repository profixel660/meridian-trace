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

REM --- 3. [AUTH] -- alpha-15 stripped TOTP wholesale; the auth_disabled
REM     env var is now a no-op. The runtime endpoint may still report it for
REM     legacy callers, but the runtime has no Depends(require_session) gates.
echo [AUTH]
echo          TOTP enforcement: removed in alpha-15 (no auth on the runtime)
echo          auth_disabled env var: no-op (kept for backward-compat only)
echo.

REM --- 4. [ENVIRONMENT] -- show MERIDIAN_* env vars seen by the backend
REM
REM Note: Windows does not expose another process's environment block via
REM Win32_Process / WMI (the Environment property is not populated). Reading
REM a remote process's PEB requires NtQueryInformationProcess, which is not
REM available from PowerShell without a P/Invoke shim. Rather than ship a
REM fragile Add-Type C# blob, we report the source of truth the launcher
REM reads at startup: C:\Meridian\.env. If the operator suspects drift
REM between .env and what the running process actually loaded, they should
REM restart the backend (Stop-Meridian.bat then Start-Meridian.bat) and
REM re-check; the launcher logs the resolved env at startup.
echo [ENVIRONMENT]
echo          (source: C:\Meridian\.env -- restart backend if you edited it)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$envFile = 'C:\Meridian\.env'; if (Test-Path $envFile) { $found = $false; foreach ($line in Get-Content $envFile) { $t = $line.Trim(); if (-not $t) { continue }; if ($t.StartsWith('#')) { continue }; if ($t -match '^(MERIDIAN_[A-Z0-9_]+)\s*=\s*(.*)$') { $name = $matches[1]; $val = $matches[2].Trim().Trim([char]34).Trim([char]39); $upper = $name.ToUpper(); $sensitive = $false; foreach ($needle in @('SECRET','TOKEN','PASSWORD','APIKEY')) { if ($upper.Contains($needle)) { $sensitive = $true } }; if ($sensitive -and $val.Length -gt 0) { $val = '<redacted ' + $val.Length + ' chars>' }; Write-Host ('         ' + $name + '=' + $val); $found = $true } }; if (-not $found) { Write-Host '         (no MERIDIAN_* entries found in .env)' } } else { Write-Host ('         (.env not found at ' + $envFile + ')') }"
echo.

REM --- 5. listening PID on :8000 (informational; cannot read its env block)
echo [PROCESS]
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop | Select-Object -First 1; if ($c) { $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; if ($p) { Write-Host ('         listener_pid    : ' + $p.Id); Write-Host ('         process_name    : ' + $p.ProcessName); Write-Host ('         path            : ' + $p.Path) } else { Write-Host ('         listener_pid    : ' + $c.OwningProcess + ' (process info unavailable)') } } else { Write-Host '         (nothing listening on :8000)' } } catch { Write-Host '         (could not enumerate :8000 listener)' }"
echo.
exit /b 0
