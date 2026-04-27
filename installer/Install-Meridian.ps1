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

# Backend / GUI wizard endpoints. The post-install step starts uvicorn on
# this port and opens the user's default browser to the wizard page.
$MERIDIAN_BACKEND_PORT  = 8000
$MERIDIAN_HEALTH_URL    = "http://localhost:$MERIDIAN_BACKEND_PORT/health"
$MERIDIAN_WIZARD_URL    = "http://localhost:$MERIDIAN_BACKEND_PORT/setup/welcome"

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
}

# -----------------------------------------------------------------------------
# 7. Venv creation
# -----------------------------------------------------------------------------
function Ensure-Venv {
    Say-Step "Creating Python virtual environment at $MERIDIAN_VENV..."
    Say-Why  "A virtual environment keeps Meridian's libraries isolated so they cannot break other Python tools on your machine."
    $venvPython = Join-Path $MERIDIAN_VENV "Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        Write-Host "   A virtual environment already exists at $MERIDIAN_VENV." -ForegroundColor Yellow
        $answer = Read-Host "   Recreate it from scratch? Type 'yes' to recreate, anything else to keep it"
        if ($answer -eq "yes") {
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
    Say-Info "Looking up latest Meridian release on GitHub..."
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
    Say-Why  "This is the script the Desktop shortcut runs. It loads your settings, activates the Python environment, and gives you a 'meridian' prompt."

    $launcher = @'
# Meridian-Console.ps1 -- launched by the Desktop shortcut.
# Generated by Install-Meridian.ps1. Safe to re-run; safe to edit.

$ErrorActionPreference = "Stop"
$MeridianRoot = "C:\Meridian"
$EnvFile      = Join-Path $MeridianRoot ".env"
$VenvActivate = Join-Path $MeridianRoot "venv\Scripts\Activate.ps1"
$OnboardState = Join-Path $MeridianRoot "projects\_meridian\onboarding_state.json"

function Show-Banner {
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host "                         M E R I D I A N                        " -ForegroundColor White
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host " Common commands:"                                                 -ForegroundColor White
    Write-Host "   meridian start                 -- launch backend + open GUI"  -ForegroundColor Gray
    Write-Host "   meridian init                  -- terminal onboarding wizard" -ForegroundColor Gray
    Write-Host "   meridian status <project>      -- show project state"         -ForegroundColor Gray
    Write-Host "   meridian review-status <proj>  -- review extraction quality"  -ForegroundColor Gray
    Write-Host "   meridian docs                  -- open documentation"         -ForegroundColor Gray
    Write-Host "   meridian --help                -- full command list"          -ForegroundColor Gray
    Write-Host ""
    Write-Host " Docs: https://github.com/profixel660/meridian-trace/tree/main/docs" -ForegroundColor DarkGray
    Write-Host "================================================================" -ForegroundColor Cyan
    Write-Host ""
}

# 1. Load .env into the process environment.
if (Test-Path -LiteralPath $EnvFile) {
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ($line -match '^\s*#') { continue }
        if ($line -match '^\s*([^=\s]+)\s*=\s*(.*)\s*$') {
            Set-Item -Path "Env:$($Matches[1])" -Value $Matches[2]
        }
    }
} else {
    Write-Host "Warning: $EnvFile not found. Run the installer again to set your API key." -ForegroundColor Yellow
}

# 2. Activate the venv.
if (-not (Test-Path -LiteralPath $VenvActivate)) {
    Write-Host "Meridian is not installed. Run Install-Meridian.bat to set it up." -ForegroundColor Red
    return
}
. $VenvActivate

Set-Location -LiteralPath $MeridianRoot
Show-Banner

# 3. Launch the Meridian backend + GUI setup wizard in the browser.
#    `meridian start` is idempotent: if the backend is already running on
#    :8000 it just opens the browser; otherwise it starts uvicorn in the
#    foreground (Ctrl-C to stop). The GUI wizard handles first-run setup;
#    once complete it routes to the main app.
Write-Host "Starting Meridian (this opens the GUI in your default browser)..." -ForegroundColor Cyan
Write-Host "Leave this window open while you use Meridian; press Ctrl-C to stop." -ForegroundColor Gray
Write-Host ""
meridian start
'@

    Set-Content -LiteralPath $MERIDIAN_LAUNCHER -Value $launcher -Encoding UTF8
    Say-OK "Wrote $MERIDIAN_LAUNCHER."
}

function New-DesktopShortcut {
    Say-Step "Putting a 'Meridian' shortcut on your Desktop..."
    Say-Why  "Double-clicking this is the only thing you need to remember to start Meridian."
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnkPath = Join-Path $desktop "Meridian.lnk"
    try {
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($lnkPath)
        $sc.TargetPath       = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
        $sc.Arguments        = "-NoExit -ExecutionPolicy Bypass -File `"$MERIDIAN_LAUNCHER`""
        $sc.WorkingDirectory = $MERIDIAN_ROOT
        $sc.IconLocation     = "$env:SystemRoot\system32\imageres.dll,76"
        $sc.Description      = "Launch Meridian -- construction deliverables register"
        $sc.Save()
        Say-OK "Shortcut created at $lnkPath."
    } catch {
        Say-Warn "Could not create Desktop shortcut: $($_.Exception.Message). You can still start Meridian by running 'powershell -File $MERIDIAN_LAUNCHER'."
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

        # alpha-3 -- backend runs in a visible cmd window during the debugging
        # phase so a crash on import is visible to the operator. Output is
        # ALSO tee'd to backend.log via cmd /c >> redirection so even if the
        # window closes (unhandled exception), we have a forensic trail. Once
        # the install flow is bedded down, swap -WindowStyle to Hidden.
        # See project_install_polish_deferred.md (memory).
        Say-Info "Launching the Meridian backend in a visible window (debug-phase)."
        Say-Info "Backend output is also being tee'd to $MERIDIAN_BACKEND_LOG."
        try {
            # Use cmd.exe as the actual spawn target so we can redirect both
            # stdout and stderr to a log file in one shell-level command.
            # Shape: cmd /c "<python> -m meridian.api.main >> <log> 2>&1"
            $cmdLine = "`"$venvPython`" -m meridian.api.main 1>>`"$MERIDIAN_BACKEND_LOG`" 2>&1"
            $proc = Start-Process `
                -FilePath "cmd.exe" `
                -ArgumentList @("/c", $cmdLine) `
                -WorkingDirectory $MERIDIAN_ROOT `
                -PassThru `
                -ErrorAction Stop
            try {
                Set-Content -LiteralPath $MERIDIAN_PID_FILE -Value $proc.Id -Encoding ASCII
                Say-OK "Backend started (PID $($proc.Id) -- cmd wrapper). Recorded to $MERIDIAN_PID_FILE."
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
