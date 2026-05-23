#Requires -Version 5.1
<#
.SYNOPSIS
    One-click installer for Meridian (the construction-deliverables extraction tool).

.DESCRIPTION
    Downloads Python (if missing), creates a venv at C:\Meridian\venv, installs
    the latest Meridian wheel from the GitHub release, prompts for the Anthropic
    API key, drops a Desktop shortcut, and runs `meridian init`.

    Designed for non-technical users -- every step prints WHAT it is doing and
    WHY. Idempotent: safe to re-run.

    Logs to C:\Meridian\install.log.

.PARAMETER SkipNetworkCheck
    Skip the up-front "can we reach github.com" probe. Useful on corporate
    networks where the probe is blocked but the actual install operations
    (which use the system proxy + default credentials) still succeed.

.PARAMETER Verbose
    Standard PowerShell flag. When set, network failures print the full
    underlying exception instead of just the friendly summary -- useful when
    debugging proxy or TLS issues.
#>
[CmdletBinding()]
param(
    [switch]$SkipNetworkCheck
)

# -----------------------------------------------------------------------------
# TLS + proxy setup -- run before ANY network call.
#   - Force TLS 1.2 (older Windows defaults to TLS 1.0/1.1 which GitHub rejects).
#   - Tell .NET WebRequest + Invoke-* cmdlets to use the system's default proxy
#     with the current user's credentials. On non-corporate machines the
#     default proxy is a no-op; on corporate machines this is what makes
#     subsequent downloads succeed.
# -----------------------------------------------------------------------------
try {
    [System.Net.ServicePointManager]::SecurityProtocol =
        [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.SecurityProtocolType]::Tls11 -bor [System.Net.SecurityProtocolType]::Tls
} catch {
    # If even setting the protocol fails, surface it later in the probe.
}
try {
    $defaultProxy = [System.Net.WebRequest]::GetSystemWebProxy()
    if ($defaultProxy) {
        $defaultProxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials
        [System.Net.WebRequest]::DefaultWebProxy = $defaultProxy
    }
} catch {
    # Same -- non-fatal here; the probe will surface details if it matters.
}

# -----------------------------------------------------------------------------
# Constants -- bump when a new Python 3.12 patch is released.
# Latest tested: Python 3.12.8 (Dec 2024). Update PYTHON_VERSION here and the
# installer will fetch that exact build from python.org.
# -----------------------------------------------------------------------------
$PYTHON_VERSION       = "3.12.8"
$PYTHON_INSTALLER_URL = "https://www.python.org/ftp/python/$PYTHON_VERSION/python-$PYTHON_VERSION-amd64.exe"

$MERIDIAN_ROOT        = "C:\Meridian"
$MERIDIAN_VENV        = "$MERIDIAN_ROOT\venv"
$MERIDIAN_PROJECTS    = "$MERIDIAN_ROOT\projects"
$MERIDIAN_ENV_FILE    = "$MERIDIAN_ROOT\.env"
$MERIDIAN_LAUNCHER    = "$MERIDIAN_ROOT\Meridian-Console.ps1"
$MERIDIAN_LOG         = "$MERIDIAN_ROOT\install.log"
$MERIDIAN_RUNTIME_DIR = "$MERIDIAN_ROOT\runtime"
$MERIDIAN_PID_FILE    = "$MERIDIAN_RUNTIME_DIR\backend.pid"
$MERIDIAN_BACKEND_LOG = "$MERIDIAN_RUNTIME_DIR\backend.log"
$MERIDIAN_LAUNCH_CMD  = "$MERIDIAN_RUNTIME_DIR\launch_backend.cmd"

# Alpha-12 .bat launcher trio dropped at install time. Use .bat (not .ps1)
# so default Windows ExecutionPolicy doesn't block them -- alpha-11's
# Meridian-Console.ps1 hit "running scripts is disabled on this system"
# on a clean install. .bat files have no equivalent restriction.
$MERIDIAN_START_BAT   = "$MERIDIAN_ROOT\Start-Meridian.bat"
$MERIDIAN_STOP_BAT    = "$MERIDIAN_ROOT\Stop-Meridian.bat"
$MERIDIAN_STATUS_BAT  = "$MERIDIAN_ROOT\Status-Meridian.bat"
$MERIDIAN_CONSOLE_BAT = "$MERIDIAN_ROOT\Meridian-Console.bat"

# Backend / GUI wizard endpoints. The post-install step starts uvicorn on
# this port and opens the user's default browser to the wizard page.
#
# Use 127.0.0.1, NOT localhost. On Windows, "localhost" resolves to ::1
# (IPv6 loopback) first; uvicorn binds to 127.0.0.1 (IPv4 loopback) only,
# so a localhost probe targets a port nothing is listening on. PowerShell's
# HttpWebRequest with a 1s timeout doesn't fall back to IPv4 fast enough,
# the probe fails forever, the installer hangs at "Waiting for the backend"
# even though the backend is fully healthy. Alpha-5 fix.
$MERIDIAN_BACKEND_PORT  = 8000
$MERIDIAN_HEALTH_URL    = "http://127.0.0.1:$MERIDIAN_BACKEND_PORT/health"
$MERIDIAN_WIZARD_URL    = "http://127.0.0.1:$MERIDIAN_BACKEND_PORT/setup/"

$GITHUB_OWNER         = "profixel660"
$GITHUB_REPO          = "meridian-trace"
$GITHUB_LATEST_API    = "https://api.github.com/repos/$GITHUB_OWNER/$GITHUB_REPO/releases/latest"
$GITHUB_RELEASES_URL  = "https://github.com/$GITHUB_OWNER/$GITHUB_REPO/releases/latest"
$DOCS_URL             = "https://github.com/$GITHUB_OWNER/$GITHUB_REPO/tree/main/docs"

# -----------------------------------------------------------------------------
# Output helpers -- coloured, prefixed, and mirrored to the install log.
# Log writes are best-effort: until C:\Meridian exists, we buffer.
# -----------------------------------------------------------------------------
$script:LogBuffer = New-Object System.Collections.Generic.List[string]

# Capture parent process id once so the locked-venv probe can exclude the
# powershell that launched us (otherwise re-running the installer from inside
# an elevated shell could try to Stop-Process its own caller).
$script:ParentPid = $null
try {
    $script:ParentPid = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop).ParentProcessId
} catch {
    # Best-effort; falling back to $null is fine -- we only use this as an
    # exclusion filter, not as a guarantee.
}

function Write-Log {
    param(
        [Parameter(Mandatory)] [string] $Message,
        [string] $Level = "INFO"
    )
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line  = "[$stamp] [$Level] $Message"
    $script:LogBuffer.Add($line) | Out-Null
    if (Test-Path -LiteralPath $MERIDIAN_ROOT) {
        try {
            Add-Content -LiteralPath $MERIDIAN_LOG -Value $line -ErrorAction Stop
            # Flush any buffered lines from before C:\Meridian existed.
            if ($script:LogBuffer.Count -gt 1) {
                $pending = $script:LogBuffer | Where-Object { $_ -ne $line }
                if ($pending) {
                    $pending | ForEach-Object { Add-Content -LiteralPath $MERIDIAN_LOG -Value $_ }
                }
                $script:LogBuffer.Clear()
            }
        } catch {
            # Swallow -- logging must never crash the installer.
        }
    }
}

function Say-Step    { param([string]$m) Write-Host ""; Write-Host ">> $m" -ForegroundColor Cyan;    Write-Log $m "STEP" }
function Say-Why     { param([string]$m) Write-Host "   $m" -ForegroundColor DarkGray;              Write-Log $m "WHY"  }
function Say-OK      { param([string]$m) Write-Host "   OK  $m" -ForegroundColor Green;             Write-Log $m "OK"   }
function Say-Warn    { param([string]$m) Write-Host "   !!  $m" -ForegroundColor Yellow;            Write-Log $m "WARN" }
function Say-Err     { param([string]$m) Write-Host "   XX  $m" -ForegroundColor Red;               Write-Log $m "ERR"  }
function Say-Info    { param([string]$m) Write-Host "   ..  $m" -ForegroundColor Gray;              Write-Log $m "INFO" }

function Stop-WithError {
    param([string]$Message, [int]$ExitCode = 1, [string]$NextStep = "")
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Red
    Write-Host " Meridian setup could not finish." -ForegroundColor Red
    Write-Host "================================================================" -ForegroundColor Red
    Write-Host " $Message" -ForegroundColor Red
    if ($NextStep) {
        Write-Host ""
        Write-Host " What to do next:" -ForegroundColor Yellow
        Write-Host " $NextStep" -ForegroundColor Yellow
    }
    if (Test-Path -LiteralPath $MERIDIAN_LOG) {
        Write-Host ""
        Write-Host " Full log: $MERIDIAN_LOG" -ForegroundColor Gray
        Write-Host " (Email this file to support if you need help.)" -ForegroundColor Gray
    }
    Write-Host ""
    Write-Log "Installer exited with code $ExitCode -- $Message" "FATAL"
    exit $ExitCode
}

# -----------------------------------------------------------------------------
# 1. Banner
# -----------------------------------------------------------------------------
function Show-Banner {
    Clear-Host
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "                    M E R I D I A N   S E T U P                 " -ForegroundColor White
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host " Meridian extracts per-trade construction deliverables from"     -ForegroundColor Gray
    Write-Host " your project documents into a single Excel register."           -ForegroundColor Gray
    Write-Host ""
    Write-Host " Project page: $GITHUB_RELEASES_URL"                             -ForegroundColor Gray
    Write-Host ""
    Write-Host " About to do (in order):"                                        -ForegroundColor White
    Write-Host "   1. Check for Python 3.12 -- install it if missing"            -ForegroundColor Gray
    Write-Host "   2. Turn on Windows long-path support"                         -ForegroundColor Gray
    Write-Host "   3. Create C:\Meridian\ and a Python virtual environment"     -ForegroundColor Gray
    Write-Host "   4. Download and install Meridian itself"                      -ForegroundColor Gray
    Write-Host "   5. Ask you for your Anthropic API key"                        -ForegroundColor Gray
    Write-Host "   6. Put a 'Meridian' shortcut on your Desktop"                 -ForegroundColor Gray
    Write-Host "   7. Open the Meridian setup wizard in your browser"           -ForegroundColor Gray
    Write-Host ""
    Write-Host " Expected time: 5 to 15 minutes (mostly downloading)."           -ForegroundColor DarkGray
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host ""
}

# -----------------------------------------------------------------------------
# 2. Admin check + self-elevate
# -----------------------------------------------------------------------------
function Test-IsAdmin {
    $id  = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $pri = New-Object System.Security.Principal.WindowsPrincipal($id)
    return $pri.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Ensure-Admin {
    if (Test-IsAdmin) {
        Say-OK "Running as Administrator."
        return
    }
    Say-Step "Asking Windows for Administrator permission..."
    Say-Why  "Meridian needs admin rights to install Python system-wide and to enable Windows long-path support. You will see a Windows User Account Control (UAC) prompt -- click 'Yes'."
    try {
        $scriptPath = $PSCommandPath
        if (-not $scriptPath) { $scriptPath = $MyInvocation.MyCommand.Path }
        $argList = @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", "`"$scriptPath`""
        )
        # Propagate user-supplied flags through the UAC re-launch so the
        # elevated session sees the same options the user chose.
        if ($SkipNetworkCheck) { $argList += "-SkipNetworkCheck" }
        if ($VerbosePreference -eq "Continue") { $argList += "-Verbose" }
        Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs -ErrorAction Stop
        Say-OK "Re-launched with Administrator rights. This window will now close."
        Start-Sleep -Seconds 2
        exit 0
    } catch {
        Stop-WithError -Message "Could not re-launch as Administrator: $($_.Exception.Message)" -ExitCode 3 `
            -NextStep "Right-click 'Install-Meridian.bat' in File Explorer and choose 'Run as administrator'."
    }
}

# -----------------------------------------------------------------------------
# 3. Network check
# -----------------------------------------------------------------------------
function Test-Internet {
    Say-Step "Checking internet connection..."
    Say-Why  "Meridian setup needs internet to download Python and the Meridian package."

    if ($SkipNetworkCheck) {
        Say-Warn "Skipping the up-front network check (-SkipNetworkCheck flag). If a download fails later, the underlying error will be shown."
        return
    }

    # Try several endpoints in order. The probe passes if ANY one succeeds --
    # corporate networks sometimes block the GitHub API specifically while
    # allowing the assets host, or vice versa.
    $probeTargets = @(
        "https://api.github.com",
        "https://github.com",
        "https://www.python.org"
    )

    $lastException = $null
    foreach ($url in $probeTargets) {
        try {
            $req = [System.Net.HttpWebRequest]::Create($url)
            $req.Method            = "HEAD"
            $req.Timeout           = 15000
            $req.ReadWriteTimeout  = 15000
            $req.UserAgent         = "Meridian-Installer/1.0 (PowerShell)"
            # Inherit the global proxy + credentials we configured at script-top.
            $req.Proxy             = [System.Net.WebRequest]::DefaultWebProxy
            if ($req.Proxy) { $req.Proxy.Credentials = [System.Net.CredentialCache]::DefaultNetworkCredentials }

            $resp = $req.GetResponse()
            $status = [int]$resp.StatusCode
            $resp.Close()

            Say-OK "Reached $url (HTTP $status)."
            return
        } catch {
            $lastException = $_
            Say-Info "Could not reach $url -- $($_.Exception.Message)"
            continue
        }
    }

    # All probes failed. Surface the actual exception details -- these are
    # the only signal that distinguishes a TLS pin / proxy / DNS / firewall
    # block. Without this, support gets blind reports of "didn't work".
    $detail = ""
    if ($lastException) {
        $ex = $lastException.Exception
        $detail = "$($ex.GetType().Name): $($ex.Message)"
        if ($ex.InnerException) {
            $detail += " (inner: $($ex.InnerException.GetType().Name): $($ex.InnerException.Message))"
        }
    }

    Stop-WithError -Message "Could not reach any of github.com / api.github.com / python.org. $detail" -ExitCode 4 `
        -NextStep ("Most-common causes:`n" +
                   "  - Corporate proxy needs auth or blocks api.github.com -- ask IT to whitelist github.com and www.python.org.`n" +
                   "  - Older Windows defaults to TLS 1.0/1.1 (we already set TLS 1.2; if this still failed, your machine may have TLS 1.2 disabled).`n" +
                   "  - You can re-run the installer with the -SkipNetworkCheck flag to bypass this probe and let the actual download attempts surface their own errors:`n" +
                   "      powershell -ExecutionPolicy Bypass -File Install-Meridian.ps1 -SkipNetworkCheck")
}

# -----------------------------------------------------------------------------
# 4. Python check + install
# -----------------------------------------------------------------------------
function Refresh-Path {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user    = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Get-PythonVersion {
    param([string]$PythonExe = "python")
    try {
        $raw = & $PythonExe --version 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        if ($raw -match "Python\s+(\d+)\.(\d+)\.(\d+)") {
            return [Version]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3])
        }
    } catch { }
    return $null
}

function Ensure-Python {
    Say-Step "Checking for Python 3.12 or newer..."
    Say-Why  "Meridian is written in Python. Without Python it cannot run."
    Refresh-Path
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($py) {
        # Defend against the Microsoft Store stub (a 0-byte launcher under WindowsApps).
        if ($py.Path -match "WindowsApps\\python") {
            Say-Warn "Found the Microsoft Store Python stub at $($py.Path). That version causes problems -- installing the real Python.org build instead."
            $py = $null
        }
    }
    if ($py) {
        $ver = Get-PythonVersion -PythonExe $py.Path
        if ($ver -and $ver -ge [Version]"3.12.0") {
            Say-OK "Found Python $ver at $($py.Path)."
            return
        } elseif ($ver) {
            Say-Warn "Found Python $ver, but Meridian needs 3.12 or newer. Will install Python $PYTHON_VERSION."
        }
    } else {
        Say-Info "No Python found on PATH. Will install Python $PYTHON_VERSION from python.org."
    }

    $tmpDir = Join-Path $env:TEMP "meridian-install"
    if (-not (Test-Path -LiteralPath $tmpDir)) { New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null }
    $installerPath = Join-Path $tmpDir "python-$PYTHON_VERSION-amd64.exe"

    Say-Info "Downloading Python $PYTHON_VERSION (about 25 MB) from python.org..."
    try {
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -Uri $PYTHON_INSTALLER_URL -OutFile $installerPath -UseBasicParsing -TimeoutSec 300
    } catch {
        Stop-WithError -Message "Failed to download Python: $($_.Exception.Message)" -ExitCode 5 `
            -NextStep "Check your internet connection and run the installer again. If your network blocks python.org, ask IT to whitelist www.python.org."
    }
    Say-OK "Downloaded Python installer."

    Say-Info "Installing Python silently (this takes 1-2 minutes -- the screen may look frozen, that's normal)..."
    $args = @(
        "/quiet",
        "InstallAllUsers=1",
        "PrependPath=1",
        "Include_test=0",
        "Include_doc=0",
        "Include_dev=0"
    )
    try {
        $proc = Start-Process -FilePath $installerPath -ArgumentList $args -Wait -PassThru -ErrorAction Stop
        if ($proc.ExitCode -ne 0) {
            Stop-WithError -Message "Python installer exited with code $($proc.ExitCode)." -ExitCode 6 `
                -NextStep "Try installing Python manually from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then re-run this installer."
        }
    } catch {
        Stop-WithError -Message "Could not launch Python installer: $($_.Exception.Message)" -ExitCode 6
    }

    Refresh-Path
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $py) {
        Stop-WithError -Message "Python installed but is not visible on PATH. A reboot may be required." -ExitCode 7 `
            -NextStep "Restart your computer and run this installer again."
    }
    $ver = Get-PythonVersion -PythonExe $py.Path
    Say-OK "Python $ver installed at $($py.Path)."
}

# -----------------------------------------------------------------------------
# 5. Long-path support (HKLM)
# -----------------------------------------------------------------------------
function Enable-LongPaths {
    Say-Step "Enabling Windows long-path support..."
    Say-Why  "Some of Meridian's dependencies have very deep folder structures. Without this setting, installation can fail with 'path too long' errors."
    $key  = "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem"
    $name = "LongPathsEnabled"
    try {
        $current = Get-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue
        if ($current -and $current.$name -eq 1) {
            Say-OK "Long-path support already enabled."
            return
        }
        New-ItemProperty -Path $key -Name $name -Value 1 -PropertyType DWORD -Force | Out-Null
        Say-OK "Long-path support enabled (registry: $key\$name = 1)."
    } catch {
        Say-Warn "Could not enable long-path support: $($_.Exception.Message). Installation may still succeed."
    }
}

# -----------------------------------------------------------------------------
# 6. Install location
# -----------------------------------------------------------------------------
function Ensure-InstallDirs {
    Say-Step "Preparing install folder at $MERIDIAN_ROOT..."
    Say-Why  "We use a short path (C:\Meridian) instead of your Documents folder so deep package paths don't cause errors."
    foreach ($d in @($MERIDIAN_ROOT, $MERIDIAN_PROJECTS, $MERIDIAN_RUNTIME_DIR)) {
        if (-not (Test-Path -LiteralPath $d)) {
            New-Item -ItemType Directory -Path $d -Force | Out-Null
            Say-OK "Created $d"
        } else {
            Say-Info "Already exists: $d"
        }
    }

    # Alpha-12: detect alpha-10 leftover venv at runtime\.venv\ and remove it.
    # Alpha-10 put the venv at C:\Meridian\runtime\.venv\; alpha-11 moved it
    # to C:\Meridian\venv\ but didn't clean up the old location. Anyone with
    # a custom integration referencing the old path silently broke. Remove
    # the cruft so a) disk space isn't wasted and b) future triage does not
    # see two python.exe candidates and pick the wrong one.
    $oldVenv = Join-Path $MERIDIAN_RUNTIME_DIR ".venv"
    if (Test-Path -LiteralPath $oldVenv) {
        Say-Info "Detected alpha-10 leftover venv at $oldVenv. Removing (alpha-12 moved the venv to $MERIDIAN_VENV)."
        try {
            Remove-Item -LiteralPath $oldVenv -Recurse -Force -ErrorAction Stop
            Say-OK "Removed $oldVenv."
        } catch {
            # Don't fail install for this -- it's housekeeping. A locked
            # file in the old venv (rare) just means the cruft stays
            # until the user reboots and re-runs.
            Say-Warn "Could not remove $oldVenv (likely a locked file): $($_.Exception.Message). Reboot and re-run if disk space matters."
        }
    }
}

# -----------------------------------------------------------------------------
# 7. Venv creation
# -----------------------------------------------------------------------------
function Stop-LockingPythonProcesses {
    param([string]$VenvPath)
    # Find python/pythonw processes whose main module path is under the venv.
    # alpha-2 tripped on this: a leftover backend held mask.cp312-win_amd64.pyd
    # open and Remove-Item failed with "Access denied". Accessing
    # MainModule.FileName on a process you don't own raises -- swallow that.
    Say-Info "Checking for stray Meridian backend processes that may be holding $VenvPath open..."
    $candidates = Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -in @('python', 'pythonw') }
    $lockers = @()
    foreach ($candidate in $candidates) {
        if ($candidate.Id -eq $PID) { continue }
        if ($candidate.Id -eq $script:ParentPid) { continue }
        $modulePath = $null
        try {
            $modulePath = $candidate.MainModule.FileName
        } catch {
            # Either we don't have rights to inspect the process or it exited.
            continue
        }
        if ($modulePath -and $modulePath.StartsWith($VenvPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            $lockers += $candidate
        }
    }
    if (-not $lockers -or $lockers.Count -eq 0) {
        Say-OK "No stray python processes found under $VenvPath."
        return
    }
    foreach ($lockingProc in $lockers) {
        Say-Warn "Found python process PID $($lockingProc.Id) running from $($lockingProc.MainModule.FileName) -- stopping it."
        try {
            Stop-Process -Id $lockingProc.Id -Force -ErrorAction Stop
        } catch {
            Say-Warn "Stop-Process failed for PID $($lockingProc.Id): $($_.Exception.Message)"
        }
    }
    Start-Sleep -Seconds 2
    # Re-probe.
    $stillAlive = @()
    foreach ($lockingProc in $lockers) {
        $still = Get-Process -Id $lockingProc.Id -ErrorAction SilentlyContinue
        if ($still) { $stillAlive += $still }
    }
    if ($stillAlive.Count -gt 0) {
        $ids = ($stillAlive | ForEach-Object { $_.Id }) -join ', '
        Stop-WithError -Message "A Meridian backend appears to be running and we couldn't stop it (PIDs: $ids)." -ExitCode 8 `
            -NextStep "Reboot Windows and re-run the installer. (A non-admin shell cannot kill an admin-spawned python process.)"
    }
    Say-OK "Stray python processes stopped."
}

function Ensure-Venv {
    Say-Step "Creating Python virtual environment at $MERIDIAN_VENV..."
    Say-Why  "A virtual environment keeps Meridian's libraries isolated so they cannot break other Python tools on your machine."
    $venvPython = Join-Path $MERIDIAN_VENV "Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        Write-Host "   A virtual environment already exists at $MERIDIAN_VENV." -ForegroundColor Yellow
        $answer = Read-Host "   Recreate it from scratch? Type 'yes' to recreate, anything else to keep it"
        if ($answer -eq "yes") {
            Stop-LockingPythonProcesses -VenvPath $MERIDIAN_VENV
            Say-Info "Removing old virtual environment..."
            try {
                Remove-Item -LiteralPath $MERIDIAN_VENV -Recurse -Force -ErrorAction Stop
            } catch {
                Stop-WithError -Message "Could not remove old venv: $($_.Exception.Message). Close any PowerShell windows that have it activated, then try again." -ExitCode 8
            }
        } else {
            Say-OK "Keeping the existing virtual environment."
            return
        }
    }
    try {
        & python -m venv $MERIDIAN_VENV
        if ($LASTEXITCODE -ne 0) {
            Stop-WithError -Message "python -m venv failed (exit code $LASTEXITCODE)." -ExitCode 9 `
                -NextStep "Open https://www.python.org/downloads/ and reinstall Python 3.12, then run this installer again."
        }
    } catch {
        Stop-WithError -Message "Could not create venv: $($_.Exception.Message)" -ExitCode 9
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Stop-WithError -Message "Venv was created but $venvPython is missing." -ExitCode 9
    }
    Say-OK "Virtual environment ready at $MERIDIAN_VENV."
}

# -----------------------------------------------------------------------------
# 8. Pip install Meridian wheel from latest release
# -----------------------------------------------------------------------------
function Get-LatestWheelUrl {
    # Check for a bundled wheel in the same directory as this script first.
    # When the installer is distributed as a zip alongside the wheel, no
    # network call is needed at all.
    if ($PSScriptRoot) {
        $local = Get-ChildItem -LiteralPath $PSScriptRoot -Filter "*.whl" -ErrorAction SilentlyContinue |
                 Sort-Object Name -Descending | Select-Object -First 1
        if ($local) {
            Say-OK "Found bundled wheel: $($local.Name) -- skipping GitHub download."
            return @{ LocalPath = $local.FullName; Name = $local.Name; Tag = "bundled" }
        }
    }

    Say-Info "No bundled wheel found -- looking up latest Meridian release on GitHub..."
    try {
        $headers = @{ "User-Agent" = "Meridian-Installer"; "Accept" = "application/vnd.github+json" }
        $resp = Invoke-RestMethod -Uri $GITHUB_LATEST_API -Headers $headers -TimeoutSec 30 -ErrorAction Stop
    } catch {
        $code = ""
        if ($_.Exception.Response) { $code = " (HTTP $([int]$_.Exception.Response.StatusCode))" }
        Stop-WithError -Message "Could not fetch the latest release info from GitHub$code." -ExitCode 10 `
            -NextStep "Open $GITHUB_RELEASES_URL in a browser to confirm a release exists, then re-run this installer."
    }
    if (-not $resp.assets) {
        Stop-WithError -Message "GitHub returned a release with no downloadable files attached." -ExitCode 11 `
            -NextStep "Visit $GITHUB_RELEASES_URL to check the release manually."
    }
    $wheel = $resp.assets | Where-Object { $_.name -like "*.whl" } | Select-Object -First 1
    if (-not $wheel) {
        Stop-WithError -Message "The latest release has no .whl file attached." -ExitCode 11 `
            -NextStep "Visit $GITHUB_RELEASES_URL -- if the release is missing the wheel, contact support."
    }
    Say-OK "Found wheel: $($wheel.name) (release tag: $($resp.tag_name))"
    return @{ Url = $wheel.browser_download_url; Name = $wheel.name; Tag = $resp.tag_name }
}

function Install-MeridianWheel {
    Say-Step "Downloading and installing Meridian..."
    Say-Why  "This grabs the official Meridian release from GitHub and installs it (along with its libraries) into the virtual environment."
    $wheel = Get-LatestWheelUrl
    $tmpDir = Join-Path $env:TEMP "meridian-install"
    if (-not (Test-Path -LiteralPath $tmpDir)) { New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null }

    if ($wheel.LocalPath) {
        $wheelPath = $wheel.LocalPath
        Say-Info "Using bundled wheel at $wheelPath ($([math]::Round((Get-Item $wheelPath).Length / 1MB, 1)) MB)."
    } else {
        $wheelPath = Join-Path $tmpDir $wheel.Name
        Say-Info "Downloading $($wheel.Name)..."
        try {
            $ProgressPreference = "SilentlyContinue"
            Invoke-WebRequest -Uri $wheel.Url -OutFile $wheelPath -UseBasicParsing -TimeoutSec 300
        } catch {
            Stop-WithError -Message "Failed to download Meridian wheel: $($_.Exception.Message)" -ExitCode 12 `
                -NextStep "Check your internet connection. If your network blocks github.com, ask IT to whitelist it."
        }
        Say-OK "Downloaded $([math]::Round((Get-Item $wheelPath).Length / 1MB, 1)) MB."
    }

    $venvPython = Join-Path $MERIDIAN_VENV "Scripts\python.exe"
    $pipLog = Join-Path $tmpDir "pip-install.log"

    Say-Info "Upgrading pip (the Python package manager)..."
    try {
        & $venvPython -m pip install --upgrade pip --disable-pip-version-check --quiet 2>&1 | Tee-Object -FilePath $pipLog | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit $LASTEXITCODE)" }
    } catch {
        Show-PipTail $pipLog
        Stop-WithError -Message "Could not upgrade pip: $($_.Exception.Message)" -ExitCode 2
    }

    Say-Info "Installing Meridian and its libraries (this is the slow part -- 3 to 10 minutes)..."
    Say-Info "You will see dots tick across the screen so you know it has not frozen."

    # Run pip in background; print dots until it finishes.
    $pipJob = Start-Job -ScriptBlock {
        param($py, $whl, $log)
        & $py -m pip install --disable-pip-version-check --no-input $whl 2>&1 | Out-File -FilePath $log -Encoding utf8
        return $LASTEXITCODE
    } -ArgumentList $venvPython, $wheelPath, $pipLog

    while ($pipJob.State -eq "Running") {
        Write-Host "." -NoNewline -ForegroundColor DarkGray
        Start-Sleep -Seconds 3
    }
    Write-Host ""
    $pipExit = Receive-Job -Job $pipJob
    Remove-Job -Job $pipJob -Force | Out-Null

    if ($pipExit -ne 0) {
        Show-PipTail $pipLog
        Stop-WithError -Message "pip install failed (exit code $pipExit)." -ExitCode 2 `
            -NextStep "See the last lines of pip output above. Most installs that fail here are network blips -- run the installer again. If it keeps failing, send $MERIDIAN_LOG to support."
    }

    # Sanity check: meridian script should exist in venv.
    $meridianExe = Join-Path $MERIDIAN_VENV "Scripts\meridian.exe"
    if (-not (Test-Path -LiteralPath $meridianExe)) {
        Show-PipTail $pipLog
        Stop-WithError -Message "pip reported success but $meridianExe is missing." -ExitCode 13
    }
    Say-OK "Meridian $($wheel.Tag) installed."
}

function Show-PipTail {
    param([string]$LogPath)
    if (-not (Test-Path -LiteralPath $LogPath)) { return }
    Write-Host ""
    Write-Host "   Last 20 lines of pip output:" -ForegroundColor Yellow
    Write-Host "   ---------------------------------------------------------------" -ForegroundColor DarkGray
    Get-Content -LiteralPath $LogPath -Tail 20 | ForEach-Object {
        Write-Host "   $_" -ForegroundColor DarkGray
        Write-Log $_ "PIP"
    }
    Write-Host "   ---------------------------------------------------------------" -ForegroundColor DarkGray
}

# -----------------------------------------------------------------------------
# 9. API key prompt
# -----------------------------------------------------------------------------
function Read-EnvFile {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $map }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*#') { continue }
        if ($line -match '^\s*([^=\s]+)\s*=\s*(.*)\s*$') {
            $map[$Matches[1]] = $Matches[2]
        }
    }
    return $map
}

function Write-EnvFile {
    param([string]$Path, [hashtable]$Map)
    $lines = @(
        "# Meridian environment variables.",
        "# Edit by hand only if you know what you are doing.",
        "# Generated by Install-Meridian.ps1 on $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))",
        ""
    )
    foreach ($k in $Map.Keys) { $lines += "$k=$($Map[$k])" }
    Set-Content -LiteralPath $Path -Value $lines -Encoding ASCII
}

function Lock-EnvFileAcl {
    param([string]$Path)
    try {
        $user = "$env:USERDOMAIN\$env:USERNAME"
        # Disable inheritance and remove inherited ACEs.
        & icacls "$Path" /inheritance:r 2>&1 | Out-Null
        # Grant full control to the installing user only.
        & icacls "$Path" /grant:r "$($user):F" 2>&1 | Out-Null
        # Also allow SYSTEM and Administrators (so uninstall etc still works).
        & icacls "$Path" /grant:r "SYSTEM:F" 2>&1 | Out-Null
        & icacls "$Path" /grant:r "Administrators:F" 2>&1 | Out-Null
        Say-OK "Locked $Path so only $user can read it."
    } catch {
        Say-Warn "Could not tighten file permissions on $Path : $($_.Exception.Message). The key is still saved -- just not as private as we would like."
    }
}

function Prompt-ApiKey {
    Say-Step "Setting your Anthropic API key..."
    Say-Why  "Meridian uses Anthropic's Claude model to read your project documents. The API key is your account's password for that service. Get one at https://console.anthropic.com."

    $envMap = Read-EnvFile -Path $MERIDIAN_ENV_FILE
    $existing = $envMap["ANTHROPIC_API_KEY"]

    if ($existing -and $existing.StartsWith("sk-ant-")) {
        $masked = $existing.Substring(0, 10) + "..." + $existing.Substring($existing.Length - 4)
        Write-Host "   An API key is already saved: $masked" -ForegroundColor Gray
        $ans = Read-Host "   Keep the existing key? Type 'yes' to keep, 'no' to replace"
        if ($ans -ne "no") {
            Say-OK "Keeping existing API key."
            # Make sure the projects path is also recorded.
            if ($envMap["MERIDIAN_PROJECTS_DIR"] -ne $MERIDIAN_PROJECTS) {
                $envMap["MERIDIAN_PROJECTS_DIR"] = $MERIDIAN_PROJECTS
                Write-EnvFile -Path $MERIDIAN_ENV_FILE -Map $envMap
                Lock-EnvFileAcl -Path $MERIDIAN_ENV_FILE
            }
            return
        }
    }

    $newKey = $null
    for ($i = 1; $i -le 3; $i++) {
        $secure = Read-Host -AsSecureString "   Paste your Anthropic API key (begins with sk-ant-)"
        $bstr   = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try {
            $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        } finally {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        $plain = $plain.Trim()
        if ([string]::IsNullOrWhiteSpace($plain)) {
            Say-Warn "Empty input. Try again (attempt $i of 3)."
            continue
        }
        if ($plain.StartsWith("sk-ant-")) {
            $newKey = $plain
            break
        }
        Say-Warn "That does not look like an Anthropic API key (should start with 'sk-ant-'). Try again (attempt $i of 3)."
    }

    if (-not $newKey) {
        Say-Warn "Three bad entries -- saving the last attempt anyway. You can fix it later by editing $MERIDIAN_ENV_FILE in Notepad."
        $newKey = $plain
    }

    $envMap["ANTHROPIC_API_KEY"]    = $newKey
    $envMap["MERIDIAN_PROJECTS_DIR"] = $MERIDIAN_PROJECTS
    Write-EnvFile -Path $MERIDIAN_ENV_FILE -Map $envMap
    Say-OK "API key saved to $MERIDIAN_ENV_FILE."
    Lock-EnvFileAcl -Path $MERIDIAN_ENV_FILE
}

# -----------------------------------------------------------------------------
# 10. Desktop shortcut + console launcher
# -----------------------------------------------------------------------------
function Write-ConsoleLauncher {
    Say-Step "Writing the Meridian console launcher..."
    Say-Why  "This is the script the Desktop shortcut runs. It just calls Start-Meridian.bat (which spawns the detached backend) so closing the terminal can never kill Meridian."

    # Alpha-12: Meridian-Console.ps1 now delegates to Start-Meridian.bat
    # rather than running `meridian start` in the foreground. The old
    # foreground path tied uvicorn to the console, which let an
    # accidental close / Quick-Edit-mode Ctrl-C kill the backend mid-import
    # (alpha-11 SME live failure).
    $launcher = @'
# Meridian-Console.ps1 -- delegates to Start-Meridian.bat (alpha-12).
# Generated by Install-Meridian.ps1. Safe to re-run; safe to edit.

$ErrorActionPreference = "Stop"
$MeridianRoot = "C:\Meridian"
$StartBat     = Join-Path $MeridianRoot "Start-Meridian.bat"

if (-not (Test-Path -LiteralPath $StartBat)) {
    Write-Host "Meridian is not installed (Start-Meridian.bat missing). Run Install-Meridian.bat to set it up." -ForegroundColor Red
    return
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "                         M E R I D I A N                        " -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host " Backend runs detached -- closing this window does NOT stop it." -ForegroundColor Gray
Write-Host " Use Stop-Meridian.bat to stop the backend cleanly."             -ForegroundColor Gray
Write-Host " Use Status-Meridian.bat to see PID / uptime / last import job." -ForegroundColor Gray
Write-Host ""

& $StartBat
'@

    Set-Content -LiteralPath $MERIDIAN_LAUNCHER -Value $launcher -Encoding UTF8
    Say-OK "Wrote $MERIDIAN_LAUNCHER."
}

# -----------------------------------------------------------------------------
# 10b. Alpha-12 .bat launcher trio + Console wrapper.
# -----------------------------------------------------------------------------
function Write-BatLaunchers {
    Say-Step "Writing the Start / Stop / Status / Console launcher .bat files..."
    Say-Why  "These are the alpha-12 backend lifecycle launchers. .bat (not .ps1) so default ExecutionPolicy never blocks them. Backend spawns detached via pythonw.exe so closing any terminal cannot kill it."

    # Each here-string is the verbatim source from installer/<name>.bat in
    # the meridian-trace repo (the files of record). Re-generate this block
    # from those source files when they change. Keeping them inline means
    # a partial release-asset upload can't break install.

    $startBat = @'
@echo off
REM Start-Meridian.bat -- alpha-12 launcher
REM Idempotent + detached spawn via pythonw.exe.
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

if not exist "%MERIDIAN_PYTHONW%" if not exist "%MERIDIAN_PYTHON%" (
    echo [ERROR] Meridian is not installed at %MERIDIAN_ROOT%.
    echo         Run Install-Meridian.bat first.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '%MERIDIAN_HEALTH_URL%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Meridian is already running.
    echo      Opening %MERIDIAN_WIZARD_URL% in your default browser...
    start "" "%MERIDIAN_WIZARD_URL%"
    exit /b 0
)

if not exist "%MERIDIAN_RUNTIME%" mkdir "%MERIDIAN_RUNTIME%"

set "MERIDIAN_PYBIN=%MERIDIAN_PYTHONW%"
if not exist "%MERIDIAN_PYBIN%" (
    echo [WARN] pythonw.exe not found; falling back to python.exe (visible console).
    set "MERIDIAN_PYBIN=%MERIDIAN_PYTHON%"
)

echo [...] Starting Meridian backend (this can take ~30 seconds on first run).
echo       Log file: %MERIDIAN_BACKEND_LOG%
echo.

REM Alpha-15: reverted to alpha-12 simplicity (no .env loader, no `^` continuation).
set "MERIDIAN_BACKEND_LOG=%MERIDIAN_BACKEND_LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:MERIDIAN_BACKEND_LOG = '%MERIDIAN_BACKEND_LOG%'; $p = Start-Process -FilePath '%MERIDIAN_PYBIN%' -ArgumentList @('-m','meridian.api.main') -WorkingDirectory '%MERIDIAN_ROOT%' -WindowStyle Hidden -PassThru; Set-Content -LiteralPath '%MERIDIAN_PID_FILE%' -Value $p.Id -Encoding ASCII; Write-Host ('       PID: ' + $p.Id)"
if errorlevel 1 (
    echo [ERROR] Could not spawn the backend. See %MERIDIAN_BACKEND_LOG% for details.
    pause
    exit /b 1
)

echo [...] Waiting for the backend to come up at %MERIDIAN_HEALTH_URL%...
set /a _attempts=0
:wait_loop
set /a _attempts+=1
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '%MERIDIAN_HEALTH_URL%' -UseBasicParsing -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 goto healthy
if %_attempts% geq 240 goto unhealthy
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
'@

    $stopBat = @'
@echo off
REM Stop-Meridian.bat -- alpha-12 launcher.
REM Linear goto-based structure (no parenthesised blocks reading freshly-
REM set variables) -- avoids the cmd.exe parse-time expansion trap that
REM the alpha-12 reviewer caught.
setlocal enableextensions

set "MERIDIAN_ROOT=C:\Meridian"
set "MERIDIAN_RUNTIME=%MERIDIAN_ROOT%\runtime"
set "MERIDIAN_PID_FILE=%MERIDIAN_RUNTIME%\backend.pid"

echo.
echo ================================================================
echo                          M E R I D I A N  --  S T O P
echo ================================================================
echo.

if not exist "%MERIDIAN_PID_FILE%" goto no_pid
set "_pid="
set /p _pid=<"%MERIDIAN_PID_FILE%"
if "%_pid%"=="" goto no_pid
echo [...] Stopping backend PID %_pid% (per %MERIDIAN_PID_FILE%)...
taskkill /PID %_pid% /T /F >nul 2>&1
if errorlevel 1 goto taskkill_failed
echo [OK] Stopped.
del "%MERIDIAN_PID_FILE%" >nul 2>&1
exit /b 0

:taskkill_failed
echo [WARN] taskkill failed for PID %_pid% (already gone?). Falling through to port-based fallback.

:no_pid
echo [...] Looking for a process on :8000...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Where-Object State -eq 'Listen' | Select-Object -First 1; if ($p) { Write-Host ('       PID: ' + $p.OwningProcess); Stop-Process -Id $p.OwningProcess -Force -ErrorAction Stop; Write-Host '[OK] Stopped.' } else { Write-Host '[OK] Nothing listening on :8000 -- already stopped.' }"
if exist "%MERIDIAN_PID_FILE%" del "%MERIDIAN_PID_FILE%" >nul 2>&1
exit /b 0
'@

    $statusBat = @'
@echo off
REM Status-Meridian.bat -- alpha-12 launcher
setlocal enableextensions

set "MERIDIAN_HEALTH_URL=http://127.0.0.1:8000/health"
set "MERIDIAN_RUNTIME_URL=http://127.0.0.1:8000/setup/runtime"
set "MERIDIAN_PID_FILE=C:\Meridian\runtime\backend.pid"

echo.
echo ================================================================
echo                          M E R I D I A N  --  S T A T U S
echo ================================================================
echo.

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

echo [RUNTIME]
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '%MERIDIAN_RUNTIME_URL%' -UseBasicParsing -TimeoutSec 2; $j = $r.Content | ConvertFrom-Json; Write-Host ('         pid             : ' + $j.pid); Write-Host ('         version         : ' + $j.version); Write-Host ('         python_version  : ' + $j.python_version); Write-Host ('         platform        : ' + $j.platform); Write-Host ('         started_at      : ' + $j.started_at); Write-Host ('         uptime_seconds  : ' + [math]::Round($j.uptime_seconds, 1)); Write-Host ('         backend_log     : ' + $j.backend_log_path); Write-Host ('         structlog_dir   : ' + $j.structlog_dir); if ($j.last_import_job) { Write-Host ''; Write-Host '         last import job:'; Write-Host ('           job_id       : ' + $j.last_import_job.job_id); Write-Host ('           status       : ' + $j.last_import_job.status); Write-Host ('           progress     : ' + $j.last_import_job.completed + '/' + $j.last_import_job.total); Write-Host ('           imported     : ' + $j.last_import_job.imported); Write-Host ('           deduped      : ' + $j.last_import_job.deduped); Write-Host ('           failed_count : ' + $j.last_import_job.failed_count); if ($j.last_import_job.current_file) { Write-Host ('           current_file : ' + $j.last_import_job.current_file) } } else { Write-Host ''; Write-Host '         last import job: <none this process>' } } catch { Write-Host ('         <runtime endpoint unavailable -- ' + $_.Exception.Message + '>') }"
echo.

REM --- 3. [AUTH] -- surface auth_disabled flag from /setup/runtime (alpha-13)
echo [AUTH]
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '%MERIDIAN_RUNTIME_URL%' -UseBasicParsing -TimeoutSec 2; $j = $r.Content | ConvertFrom-Json; if ($j.PSObject.Properties.Name -contains 'auth_disabled') { if ($j.auth_disabled) { Write-Host '         auth_disabled=true   --> AUTH BYPASS ACTIVE (debug only)' } else { Write-Host '         auth_disabled=false  --> normal TOTP enforcement' } } else { Write-Host '         <auth_disabled field not present -- backend pre-alpha-13?>' } } catch { Write-Host '         <runtime endpoint unavailable -- cannot read auth_disabled>' }"
echo.

REM --- 4. [ENVIRONMENT] -- show MERIDIAN_* env vars from .env (alpha-14)
REM Windows does not expose another process's environment block via WMI;
REM reporting the source of truth the launcher reads at startup. Restart
REM the backend after editing .env to refresh.
echo [ENVIRONMENT]
echo          (source: C:\Meridian\.env -- restart backend if you edited it)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$envFile = 'C:\Meridian\.env'; if (Test-Path $envFile) { $found = $false; foreach ($line in Get-Content $envFile) { $t = $line.Trim(); if (-not $t) { continue }; if ($t.StartsWith('#')) { continue }; if ($t -match '^(MERIDIAN_[A-Z0-9_]+)\s*=\s*(.*)$') { $name = $matches[1]; $val = $matches[2].Trim().Trim([char]34).Trim([char]39); $upper = $name.ToUpper(); $sensitive = $false; foreach ($needle in @('SECRET','TOKEN','PASSWORD','APIKEY')) { if ($upper.Contains($needle)) { $sensitive = $true } }; if ($sensitive -and $val.Length -gt 0) { $val = '<redacted ' + $val.Length + ' chars>' }; Write-Host ('         ' + $name + '=' + $val); $found = $true } }; if (-not $found) { Write-Host '         (no MERIDIAN_* entries found in .env)' } } else { Write-Host ('         (.env not found at ' + $envFile + ')') }"
echo.

REM --- 5. [PROCESS] -- listening PID on :8000 (informational)
echo [PROCESS]
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $c = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop | Select-Object -First 1; if ($c) { $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; if ($p) { Write-Host ('         listener_pid    : ' + $p.Id); Write-Host ('         process_name    : ' + $p.ProcessName); Write-Host ('         path            : ' + $p.Path) } else { Write-Host ('         listener_pid    : ' + $c.OwningProcess + ' (process info unavailable)') } } else { Write-Host '         (nothing listening on :8000)' } } catch { Write-Host '         (could not enumerate :8000 listener)' }"
echo.
exit /b 0
'@

    $consoleBat = @'
@echo off
REM Meridian-Console.bat -- alpha-12 wrapper around Meridian-Console.ps1
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
'@

    # Alpha-15: normalise line endings to CRLF before writing. PS5's
    # Set-Content writes the string verbatim with whatever line endings
    # are in the variable; if the .ps1 source itself has LF-only
    # endings (e.g. edited in a Unix-style editor or written by a tool
    # that doesn't auto-convert), the .bat files inherit LF-only
    # endings and cmd.exe parser misbehaviour follows. Force CRLF.
    $crlf = "`r`n"
    $startBat   = ($startBat   -replace "`r?`n", $crlf)
    $stopBat    = ($stopBat    -replace "`r?`n", $crlf)
    $statusBat  = ($statusBat  -replace "`r?`n", $crlf)
    $consoleBat = ($consoleBat -replace "`r?`n", $crlf)

    Set-Content -LiteralPath $MERIDIAN_START_BAT   -Value $startBat   -Encoding ASCII -NoNewline
    Set-Content -LiteralPath $MERIDIAN_STOP_BAT    -Value $stopBat    -Encoding ASCII -NoNewline
    Set-Content -LiteralPath $MERIDIAN_STATUS_BAT  -Value $statusBat  -Encoding ASCII -NoNewline
    Set-Content -LiteralPath $MERIDIAN_CONSOLE_BAT -Value $consoleBat -Encoding ASCII -NoNewline

    Say-OK "Wrote Start-Meridian.bat / Stop-Meridian.bat / Status-Meridian.bat / Meridian-Console.bat in $MERIDIAN_ROOT."
}

function New-DesktopShortcut {
    Say-Step "Putting a 'Meridian' shortcut on your Desktop..."
    Say-Why  "Double-clicking this is the only thing you need to remember to start Meridian."
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnkPath = Join-Path $desktop "Meridian.lnk"
    try {
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($lnkPath)
        # Alpha-12: target the .bat wrapper, NOT powershell.exe with the
        # .ps1 as an argument. Default ExecutionPolicy on a clean Windows
        # install blocks unsigned .ps1 scripts; .bat has no equivalent
        # restriction. Alpha-11 SME hit "running scripts is disabled on
        # this system" when the shortcut launched -- the .bat wrapper
        # invokes the .ps1 internally with `-ExecutionPolicy Bypass`.
        $sc.TargetPath       = $MERIDIAN_CONSOLE_BAT
        $sc.Arguments        = ""
        $sc.WorkingDirectory = $MERIDIAN_ROOT
        $sc.IconLocation     = "$env:SystemRoot\system32\imageres.dll,76"
        $sc.Description      = "Launch Meridian -- construction deliverables register"
        $sc.Save()
        Say-OK "Shortcut created at $lnkPath."
    } catch {
        Say-Warn "Could not create Desktop shortcut: $($_.Exception.Message). You can still start Meridian by running $MERIDIAN_CONSOLE_BAT directly."
    }
}

# -----------------------------------------------------------------------------
# 11. Start FastAPI backend + open the GUI setup wizard in the user's browser
# -----------------------------------------------------------------------------
function Test-BackendHealth {
    param([string]$Url, [int]$TimeoutMs = 1500)
    try {
        $req = [System.Net.HttpWebRequest]::Create($Url)
        $req.Method            = "GET"
        $req.Timeout           = $TimeoutMs
        $req.ReadWriteTimeout  = $TimeoutMs
        $req.UserAgent         = "Meridian-Installer/1.0 (PowerShell)"
        $resp = $req.GetResponse()
        $status = [int]$resp.StatusCode
        $resp.Close()
        return ($status -eq 200)
    } catch {
        return $false
    }
}

function Start-BackendAndOpenBrowser {
    Say-Step "Starting the Meridian setup wizard in your browser..."
    Say-Why  ("Round-17 swaps the terminal-only setup for a guided web wizard. The installer launches " +
              "the Meridian backend in the background, waits until it answers, then opens your default " +
              "browser at the welcome page.")

    $venvPython  = Join-Path $MERIDIAN_VENV "Scripts\python.exe"
    $meridianExe = Join-Path $MERIDIAN_VENV "Scripts\meridian.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Say-Warn "Python interpreter not found at $venvPython -- skipping backend launch. You can start setup later by running 'meridian start' from the Desktop shortcut."
        return
    }

    # Load .env into the current process so the spawned backend sees the API key.
    $envMap = Read-EnvFile -Path $MERIDIAN_ENV_FILE
    foreach ($k in $envMap.Keys) { Set-Item -Path "Env:$k" -Value $envMap[$k] }

    # alpha-3 -- explicitly tell the spawned Python where Meridian lives. The
    # installer runs as Administrator, which means cwd defaults to System32;
    # without these env vars, meridian.config._project_root() falls back to
    # cwd and tries to write logs/projects under C:\Windows\System32 (the
    # alpha-2 elevated-cwd PermissionError). Setting MERIDIAN_HOME +
    # MERIDIAN_PROJECTS_DIR + -WorkingDirectory makes the bug impossible.
    $env:MERIDIAN_HOME         = $MERIDIAN_ROOT
    $env:MERIDIAN_PROJECTS_DIR = $MERIDIAN_PROJECTS

    # If something else already responds on /health, don't double-spawn.
    if (Test-BackendHealth -Url $MERIDIAN_HEALTH_URL -TimeoutMs 1000) {
        Say-Info "Backend already responding at $MERIDIAN_HEALTH_URL -- reusing it."
    } else {
        # Ensure the runtime directory exists for the PID + log files.
        if (-not (Test-Path -LiteralPath $MERIDIAN_RUNTIME_DIR)) {
            New-Item -ItemType Directory -Path $MERIDIAN_RUNTIME_DIR -Force | Out-Null
        }

        # ALPHA-12 LAUNCH MODEL -- detached pythonw.exe.
        #
        # alpha-4-through-11 launched the backend via `Start-Process` on a
        # generated .cmd helper. That helper invoked python.exe with cmd.exe
        # I/O redirection. cmd.exe spawned a console -- visible by default,
        # which alpha-11's "[debug-phase]" comment intended to make hidden
        # later, but the deeper problem was console-attachment: closing the
        # parent terminal sent CTRL_CLOSE_EVENT down the console session and
        # uvicorn shut down gracefully. The user lived this end-of-alpha-11.
        #
        # Fix: spawn pythonw.exe (Windows GUI subsystem, NO console at all)
        # via Start-Process -WindowStyle Hidden. No console = no
        # CTRL_CLOSE_EVENT path = backend immune to terminal close, accidental
        # Quick Edit selection, Ctrl-C from a different shell, etc.
        # MERIDIAN_BACKEND_LOG env var tells meridian.api.main to redirect
        # sys.stdout / sys.stderr to backend.log at process startup (since
        # pythonw.exe has no inherited stderr). The .cmd helper still gets
        # written for diagnostic re-launch but is no longer the install-time
        # primary path -- it's a fallback the .bat launchers use when
        # pythonw is unavailable for some reason.
        $venvPythonW = $venvPython -replace 'python\.exe$','pythonw.exe'
        if (-not (Test-Path -LiteralPath $venvPythonW)) {
            Say-Warn "pythonw.exe not found at $venvPythonW; falling back to python.exe (visible console)."
            $venvPythonW = $venvPython
        }

        Say-Info "Launching the Meridian backend (detached pythonw.exe + hidden window)."
        Say-Info "Backend stdout/stderr -> $MERIDIAN_BACKEND_LOG (see also: $MERIDIAN_PID_FILE)."
        try {
            # Helper .cmd retained as a diagnostic re-launch path (the new
            # Start-Meridian.bat invokes it when pythonw fails). Keep the
            # original visible-cmd shape so the operator can run it
            # manually if pythonw refuses to start.
            $launchCmdLines = @(
                "@echo off",
                "set `"MERIDIAN_BACKEND_LOG=$MERIDIAN_BACKEND_LOG`"",
                "`"$venvPython`" -m meridian.api.main 1>>`"$MERIDIAN_BACKEND_LOG`" 2>&1"
            )
            Set-Content -LiteralPath $MERIDIAN_LAUNCH_CMD -Value $launchCmdLines -Encoding ASCII
            Say-Info "Wrote diagnostic launcher: $MERIDIAN_LAUNCH_CMD"

            # Set env var on the parent shell so the child pythonw inherits it.
            $env:MERIDIAN_BACKEND_LOG = $MERIDIAN_BACKEND_LOG
            $proc = Start-Process `
                -FilePath $venvPythonW `
                -ArgumentList @("-m","meridian.api.main") `
                -WorkingDirectory $MERIDIAN_ROOT `
                -WindowStyle Hidden `
                -PassThru `
                -ErrorAction Stop
            try {
                Set-Content -LiteralPath $MERIDIAN_PID_FILE -Value $proc.Id -Encoding ASCII
                Say-OK "Backend started (PID $($proc.Id) -- detached pythonw). Recorded to $MERIDIAN_PID_FILE."
            } catch {
                Say-Warn "Backend started (PID $($proc.Id)) but could not write the PID file: $($_.Exception.Message)"
            }
        } catch {
            Say-Warn "Could not launch the backend: $($_.Exception.Message). Falling back to terminal setup."
            Run-CliInitFallback -MeridianExe $meridianExe -Reason "Start-Process raised: $($_.Exception.Message)"
            return
        }

        # Poll /health for up to 60 seconds, 250 ms intervals (240 attempts).
        Say-Info "Waiting for the backend to come up at $MERIDIAN_HEALTH_URL..."
        $maxAttempts = 240
        $healthy = $false
        for ($i = 1; $i -le $maxAttempts; $i++) {
            if (Test-BackendHealth -Url $MERIDIAN_HEALTH_URL -TimeoutMs 1000) {
                $healthy = $true
                Say-OK "Backend is healthy after $([math]::Round($i * 0.25, 1))s."
                break
            }
            Start-Sleep -Milliseconds 250
        }

        if (-not $healthy) {
            Say-Warn "Backend did not come up in 60 seconds. Falling back to terminal setup."
            # alpha-3 -- show the last 30 lines of backend.log inline so the
            # operator doesn't have to hunt for the failure cause.
            if (Test-Path -LiteralPath $MERIDIAN_BACKEND_LOG) {
                Write-Host ""
                Write-Host " Last 30 lines of $MERIDIAN_BACKEND_LOG :" -ForegroundColor Yellow
                Write-Host " -----------------------------------------" -ForegroundColor Gray
                try {
                    Get-Content -LiteralPath $MERIDIAN_BACKEND_LOG -Tail 30 -ErrorAction Stop | ForEach-Object {
                        Write-Host "   $_" -ForegroundColor Gray
                    }
                } catch {
                    Write-Host "   (could not read backend.log: $($_.Exception.Message))" -ForegroundColor Red
                }
                Write-Host " -----------------------------------------" -ForegroundColor Gray
                Write-Host ""
            }
            Run-CliInitFallback -MeridianExe $meridianExe -Reason "Backend health probe at $MERIDIAN_HEALTH_URL never returned 200 within 60s."
            return
        }
    }

    # Open the user's default browser. Start-Process on a URL resolves to the
    # default-handler app for "http" -- which is the user's chosen browser.
    Say-Info "Opening $MERIDIAN_WIZARD_URL in your default browser..."
    try {
        Start-Process $MERIDIAN_WIZARD_URL -ErrorAction Stop
        Say-OK "Browser launched."
    } catch {
        Say-Warn "Could not open the browser automatically: $($_.Exception.Message). Paste this URL into your browser yourself: $MERIDIAN_WIZARD_URL"
    }
}

function Run-CliInitFallback {
    param([string]$MeridianExe, [string]$Reason)
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Yellow
    Write-Host " Couldn't start the backend, falling back to terminal setup."     -ForegroundColor Yellow
    Write-Host "================================================================" -ForegroundColor Yellow
    Write-Host " Reason: $Reason"                                                 -ForegroundColor Gray
    Write-Host ""
    if (-not (Test-Path -LiteralPath $MeridianExe)) {
        Say-Warn "meridian.exe not found at $MeridianExe -- cannot fall back. Re-run the installer once the backend issue is resolved, or run 'meridian init' manually."
        return
    }
    try {
        & $MeridianExe init | Out-Null
    } catch {
        Say-Warn "meridian init raised an error: $($_.Exception.Message). Run it manually from the Desktop shortcut once you've sorted the underlying issue."
    }
}

# -----------------------------------------------------------------------------
# 12. Final banner
# -----------------------------------------------------------------------------
function Show-FinalBanner {
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "          M E R I D I A N   I S   I N S T A L L E D             " -ForegroundColor White
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host " Meridian is starting up. Setup will open in your browser."       -ForegroundColor White
    Write-Host " If a browser doesn't open in a few seconds, paste this address"  -ForegroundColor Gray
    Write-Host " into one:"                                                       -ForegroundColor Gray
    Write-Host "     $MERIDIAN_WIZARD_URL"                                        -ForegroundColor Cyan
    Write-Host ""
    Write-Host " Installed at:    $MERIDIAN_ROOT"                                  -ForegroundColor Gray
    Write-Host " Your projects:   $MERIDIAN_PROJECTS"                              -ForegroundColor Gray
    Write-Host " Setup log:       $MERIDIAN_LOG"                                   -ForegroundColor Gray
    Write-Host ""
    Write-Host " To re-launch Meridian later:"                                    -ForegroundColor White
    Write-Host "   * Double-click 'Meridian' on your Desktop, OR"                 -ForegroundColor Gray
    Write-Host "   * Open PowerShell and run 'meridian start'"                    -ForegroundColor Gray
    Write-Host ""
    Write-Host " Documentation:   $DOCS_URL"                                       -ForegroundColor Gray
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host ""
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
try {
    Show-Banner
    Ensure-Admin
    # The pre-admin run never reaches here; the elevated re-launch starts fresh.
    Test-Internet
    Ensure-Python
    Enable-LongPaths
    Ensure-InstallDirs
    Write-Log "Installer started; user=$env:USERNAME; host=$env:COMPUTERNAME" "BOOT"
    Ensure-Venv
    Install-MeridianWheel
    Prompt-ApiKey
    Write-ConsoleLauncher
    Write-BatLaunchers
    New-DesktopShortcut
    Start-BackendAndOpenBrowser
    Show-FinalBanner
    Write-Log "Installer finished successfully." "DONE"
    Write-Host "Press Enter to close this window..." -ForegroundColor DarkGray
    Read-Host | Out-Null
} catch {
    Stop-WithError -Message "Unexpected error: $($_.Exception.Message)" -ExitCode 99 `
        -NextStep "Send $MERIDIAN_LOG to support, or open an issue at $GITHUB_RELEASES_URL."
}
