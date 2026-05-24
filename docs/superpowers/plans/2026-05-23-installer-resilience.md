# Installer Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Install-Meridian.ps1` self-healing — fix every recoverable failure silently, never surface OS internals to the user, and make reinstalls fast.

**Architecture:** Four targeted additions to a single file (`installer/Install-Meridian.ps1`): two new helper functions (`Remove-CorruptedDistInfo`, `Stop-Port8000`), removal of an interactive prompt in `Ensure-Venv`, and a first-install vs. update branch in the main sequence. No new files.

**Tech Stack:** PowerShell 5.1, existing installer helpers (`Say-Info`, `Say-OK`, `Say-Warn`, `Stop-WithError`)

---

## Files

- Modify: `installer/Install-Meridian.ps1`
  - Add `Remove-CorruptedDistInfo` after line 502 (end of `Stop-LockingPythonProcesses`)
  - Add `Stop-Port8000` after `Remove-CorruptedDistInfo`
  - Add `Test-IsUpdateInstall` after `Stop-Port8000`
  - Modify `Ensure-Venv` lines 508–522 (remove interactive prompt)
  - Modify `Install-MeridianWheel` lines 578–581 (add pre-clean + port-kill before pip)
  - Modify `Start-BackendAndOpenBrowser` lines 1111–1114 (replace "reusing it" with Stop-Port8000)
  - Modify main sequence lines 1277–1296 (branch on first-install vs. update)

---

## Task 1: Add Remove-CorruptedDistInfo + wire into Install-MeridianWheel

**Files:**
- Modify: `installer/Install-Meridian.ps1`

- [ ] **Step 1: Add the function after line 502**

Insert after the closing `}` of `Stop-LockingPythonProcesses` (after line 502):

```powershell
function Remove-CorruptedDistInfo {
    # Deletes any meridian*.dist-info directory missing its RECORD file.
    # uv/pip warns and can leave the venv broken when RECORD is absent.
    # Called before every pip install -- silent, no user action needed.
    param([string]$SitePackages)
    if (-not (Test-Path -LiteralPath $SitePackages)) { return }
    $dirs = Get-ChildItem -LiteralPath $SitePackages -Filter "meridian*.dist-info" -Directory -ErrorAction SilentlyContinue
    foreach ($dir in $dirs) {
        if (-not (Test-Path -LiteralPath (Join-Path $dir.FullName "RECORD"))) {
            Say-Info "Removing incomplete package record: $($dir.Name)"
            try {
                Remove-Item -LiteralPath $dir.FullName -Recurse -Force -ErrorAction Stop
            } catch {
                Say-Warn "Could not remove $($dir.FullName): $($_.Exception.Message)"
            }
        }
    }
}
```

- [ ] **Step 2: Wire into Install-MeridianWheel**

In `Install-MeridianWheel`, replace lines 578–581:
```powershell
function Install-MeridianWheel {
    Say-Step "Downloading and installing Meridian..."
    Say-Why  "This grabs the official Meridian release from GitHub and installs it (along with its libraries) into the virtual environment."
    $wheel = Get-LatestWheelUrl
```

With:
```powershell
function Install-MeridianWheel {
    Say-Step "Downloading and installing Meridian..."
    Say-Why  "This grabs the official Meridian release from GitHub and installs it (along with its libraries) into the virtual environment."
    $sitePackages = Join-Path $MERIDIAN_VENV "Lib\site-packages"
    Remove-CorruptedDistInfo -SitePackages $sitePackages
    $wheel = Get-LatestWheelUrl
```

- [ ] **Step 3: Verify**

Trigger: manually delete the `RECORD` file from `.venv\Lib\site-packages\meridian-0.2.0a27.dist-info\`, then run `Install-Meridian.bat`.

Expected: installer prints `Removing incomplete package record: meridian-0.2.0a27.dist-info` and continues without error. No warning about missing RECORD file appears.

- [ ] **Step 4: Commit**

```
git add installer/Install-Meridian.ps1
git commit -m "fix(installer): pre-clean corrupted dist-info before pip install"
```

---

## Task 2: Add Stop-Port8000 + wire into Install-MeridianWheel and Start-BackendAndOpenBrowser

**Files:**
- Modify: `installer/Install-Meridian.ps1`

- [ ] **Step 1: Add the function after Remove-CorruptedDistInfo**

Insert after the closing `}` of `Remove-CorruptedDistInfo`:

```powershell
function Stop-Port8000 {
    # Stops whatever process is listening on port 8000 before we try to bind it.
    # Called before pip install (frees venv file locks) and before backend start.
    # Silent: user sees one plain-English line, not PIDs or port numbers.
    $conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $conn) { return }
    Say-Info "Meridian was already running — restarting it now."
    Write-Log "Stopping process PID $($conn.OwningProcess) on port 8000." "INFO"
    try {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction Stop
        Start-Sleep -Seconds 1
    } catch {
        Say-Warn "Could not stop the existing Meridian process: $($_.Exception.Message)"
    }
}
```

- [ ] **Step 2: Wire into Install-MeridianWheel (after Remove-CorruptedDistInfo call)**

In `Install-MeridianWheel`, the block now reads:
```powershell
    $sitePackages = Join-Path $MERIDIAN_VENV "Lib\site-packages"
    Remove-CorruptedDistInfo -SitePackages $sitePackages
    $wheel = Get-LatestWheelUrl
```

Add `Stop-Port8000` between the pre-clean and the wheel fetch:
```powershell
    $sitePackages = Join-Path $MERIDIAN_VENV "Lib\site-packages"
    Remove-CorruptedDistInfo -SitePackages $sitePackages
    Stop-Port8000
    $wheel = Get-LatestWheelUrl
```

- [ ] **Step 3: Wire into Start-BackendAndOpenBrowser (replace "reusing it" block)**

In `Start-BackendAndOpenBrowser`, replace lines 1111–1114:
```powershell
    # If something else already responds on /health, don't double-spawn.
    if (Test-BackendHealth -Url $MERIDIAN_HEALTH_URL -TimeoutMs 1000) {
        Say-Info "Backend already responding at $MERIDIAN_HEALTH_URL -- reusing it."
    } else {
```

With (remove the entire `if/else` wrapper; the spawn block runs unconditionally):
```powershell
    # Always stop any existing backend before spawning the updated one.
    # Stop-Port8000 is a no-op if nothing is running.
    Stop-Port8000
```

Then find the closing `}` that ends the `else` block — it sits just before the browser-open lines near the end of `Start-BackendAndOpenBrowser`. Remove that closing `}` so the spawn block is no longer wrapped in a conditional. The browser-open lines that follow are unchanged.

- [ ] **Step 4: Verify**

Trigger: start `C:\Meridian\Start-Meridian.bat`, then immediately run `Install-Meridian.bat`.

Expected: installer prints `Meridian was already running — restarting it now.` at the pip step and again at the backend-start step. Install completes. New backend starts cleanly. No "access is denied" error.

- [ ] **Step 5: Commit**

```
git add installer/Install-Meridian.ps1
git commit -m "fix(installer): auto-kill port 8000 conflicts before pip and backend start"
```

---

## Task 3: Remove interactive prompt from Ensure-Venv

**Files:**
- Modify: `installer/Install-Meridian.ps1`

- [ ] **Step 1: Replace the interactive block in Ensure-Venv**

In `Ensure-Venv`, replace lines 508–522:
```powershell
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
```

With:
```powershell
    if (Test-Path -LiteralPath $venvPython) {
        Say-OK "Virtual environment already exists at $MERIDIAN_VENV."
        return
    }
```

- [ ] **Step 2: Verify**

Trigger: run `Install-Meridian.bat` on a machine where `C:\Meridian\venv` already exists.

Expected: installer prints `Virtual environment already exists at C:\Meridian\venv.` and moves on immediately. No prompt appears. Total install time is shorter.

- [ ] **Step 3: Commit**

```
git add installer/Install-Meridian.ps1
git commit -m "fix(installer): remove interactive venv prompt on reinstall"
```

---

## Task 4: Add Test-IsUpdateInstall + fast update path in main sequence

**Files:**
- Modify: `installer/Install-Meridian.ps1`

- [ ] **Step 1: Add Test-IsUpdateInstall after Stop-Port8000**

Insert after the closing `}` of `Stop-Port8000`:

```powershell
function Test-IsUpdateInstall {
    # Returns $true if Meridian is already installed at C:\Meridian.
    # Used to skip the full setup sequence (Python install, long-path,
    # API key prompt, shortcut) on subsequent runs.
    $meridianExe = Join-Path $MERIDIAN_VENV "Scripts\meridian.exe"
    return (Test-Path -LiteralPath $meridianExe)
}
```

- [ ] **Step 2: Replace the main sequence (lines 1277–1296)**

Replace the entire `try` block in the main sequence:

```powershell
# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
try {
    Show-Banner
    Ensure-Admin
    # The pre-admin run never reaches here; the elevated re-launch starts fresh.

    if (Test-IsUpdateInstall) {
        # Update path: Meridian already installed -- skip Python, long-path,
        # API key prompt, and shortcut. Just update the wheel and restart.
        Say-Step "Updating Meridian..."
        Say-Why  "An existing Meridian installation was found -- running the faster update sequence."
        Write-Log "Update install; user=$env:USERNAME; host=$env:COMPUTERNAME" "BOOT"
        Ensure-InstallDirs
        Ensure-Venv
        Install-MeridianWheel
        Write-ConsoleLauncher
        Write-BatLaunchers
        Start-BackendAndOpenBrowser
    } else {
        # First install: full sequence.
        Write-Log "Fresh install; user=$env:USERNAME; host=$env:COMPUTERNAME" "BOOT"
        Test-Internet
        Ensure-Python
        Enable-LongPaths
        Ensure-InstallDirs
        Ensure-Venv
        Install-MeridianWheel
        Prompt-ApiKey
        Write-ConsoleLauncher
        Write-BatLaunchers
        New-DesktopShortcut
        Start-BackendAndOpenBrowser
    }

    Show-FinalBanner
    Write-Log "Installer finished successfully." "DONE"
    Write-Host "Press Enter to close this window..." -ForegroundColor DarkGray
    Read-Host | Out-Null
} catch {
    Stop-WithError -Message "Unexpected error: $($_.Exception.Message)" -ExitCode 99 `
        -NextStep "Send $MERIDIAN_LOG to support, or open an issue at $GITHUB_RELEASES_URL."
}
```

- [ ] **Step 3: Verify — update path**

Trigger: run `Install-Meridian.bat` on a machine with an existing `C:\Meridian` install.

Expected:
- Banner shows, then immediately `Updating Meridian...`
- No Python install step
- No API key prompt
- No "Putting a Meridian shortcut on your Desktop" step
- Wheel installs, backend restarts, browser opens
- Total time under 2 minutes (vs. 5–15 for a fresh install)

- [ ] **Step 4: Verify — fresh install path**

Trigger: delete `C:\Meridian` entirely, then run `Install-Meridian.bat`.

Expected: full sequence runs — Python check, long-path, venv, wheel, API key prompt, shortcut, browser.

- [ ] **Step 5: Commit**

```
git add installer/Install-Meridian.ps1
git commit -m "fix(installer): fast update path -- skip Python/key/shortcut on reinstall"
```

---

## Task 5: Build alpha-28 wheel + zip + publish release

**Files:**
- Modify: `pyproject.toml` (version bump)

- [ ] **Step 1: Bump version in pyproject.toml**

Change:
```toml
version = "0.2.0a27"
```
To:
```toml
version = "0.2.0a28"
```

- [ ] **Step 2: Build frontend**

```
cd apps\web
npm run build
cd ..\..
```

Expected: `✓ Generating static pages (29/29)` — no errors.

- [ ] **Step 3: Build wheel**

```
uv build --wheel
```

Expected: `Successfully built dist\meridian-0.2.0a28-py3-none-any.whl`

- [ ] **Step 4: Build installer zip**

```powershell
$repo  = "C:\Users\PeterRoberts\OneDrive - Undivided Systems\Documents\Project_requirements_tester"
$wheel = "$repo\dist\meridian-0.2.0a28-py3-none-any.whl"
$zip   = "$repo\dist\Meridian-alpha-28-installer.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path @(
    "$repo\installer\Install-Meridian.bat",
    "$repo\installer\Install-Meridian.ps1",
    "$repo\installer\Uninstall-Meridian.bat",
    "$repo\installer\Uninstall-Meridian.ps1",
    "$repo\installer\Reset-Meridian.ps1",
    $wheel
) -DestinationPath $zip
Write-Host "Built: $zip ($([math]::Round((Get-Item $zip).Length/1MB,1)) MB)"
```

- [ ] **Step 5: Commit, tag, and publish**

```
git add pyproject.toml
git commit -m "release: alpha-28 -- installer resilience (5 fixes)"
git tag v0.2.0-alpha.28
git push origin main --tags
gh release create v0.2.0-alpha.28 "dist\Meridian-alpha-28-installer.zip#Meridian-alpha-28-installer.zip" --title "v0.2.0-alpha.28 -- resilient installer" --notes "Silent self-healing on reinstall: pre-cleans corrupted dist-info, auto-kills port conflicts, removes the venv prompt, and uses a fast update path that skips Python install and API key on subsequent runs."
```

Expected: release URL printed by `gh`.
