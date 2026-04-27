#Requires -Version 5.1
<#
.SYNOPSIS
    Uninstalls Meridian. Always preserves your project data and your API key
    unless you explicitly say otherwise.
#>

$MERIDIAN_ROOT     = "C:\Meridian"
$MERIDIAN_VENV     = "$MERIDIAN_ROOT\venv"
$MERIDIAN_LAUNCHER = "$MERIDIAN_ROOT\Meridian-Console.ps1"
$MERIDIAN_LOG      = "$MERIDIAN_ROOT\install.log"
$MERIDIAN_PROJECTS = "$MERIDIAN_ROOT\projects"
$MERIDIAN_ENV_FILE = "$MERIDIAN_ROOT\.env"

function Say-Step { param([string]$m) Write-Host ""; Write-Host ">> $m" -ForegroundColor Cyan }
function Say-OK   { param([string]$m) Write-Host "   OK  $m" -ForegroundColor Green }
function Say-Warn { param([string]$m) Write-Host "   !!  $m" -ForegroundColor Yellow }
function Say-Skip { param([string]$m) Write-Host "   --  $m" -ForegroundColor DarkGray }
function Say-Why  { param([string]$m) Write-Host "   $m" -ForegroundColor DarkGray }

function Ask-YesNo {
    param([string]$Question, [string]$Default = "no")
    $hint = if ($Default -eq "yes") { "[Y/n]" } else { "[y/N]" }
    $ans = Read-Host "   $Question $hint"
    if ([string]::IsNullOrWhiteSpace($ans)) { $ans = $Default }
    return ($ans -match '^(y|yes)$')
}

function Test-IsAdmin {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object System.Security.Principal.WindowsPrincipal($id)).IsInRole(
        [System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Ensure-Admin {
    if (Test-IsAdmin) { return }
    Write-Host "Re-launching with Administrator rights..." -ForegroundColor Cyan
    $scriptPath = $PSCommandPath
    if (-not $scriptPath) { $scriptPath = $MyInvocation.MyCommand.Path }
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$scriptPath`"") `
        -Verb RunAs
    exit 0
}

# -----------------------------------------------------------------------------
function Show-Banner {
    Clear-Host
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Yellow
    Write-Host "                M E R I D I A N   U N I N S T A L L             " -ForegroundColor White
    Write-Host "================================================================" -ForegroundColor Yellow
    Write-Host " This will remove the Meridian application but KEEP your"        -ForegroundColor Gray
    Write-Host " project data and your API key by default."                       -ForegroundColor Gray
    Write-Host ""
    Write-Host " You will be asked separately about:"                             -ForegroundColor Gray
    Write-Host "   - your projects ($MERIDIAN_PROJECTS)"                          -ForegroundColor Gray
    Write-Host "   - your API key ($MERIDIAN_ENV_FILE)"                           -ForegroundColor Gray
    Write-Host "   - the Windows two-factor secret (Credential Manager)"          -ForegroundColor Gray
    Write-Host "   - Windows long-path support"                                   -ForegroundColor Gray
    Write-Host "   - Python itself"                                               -ForegroundColor Gray
    Write-Host "================================================================" -ForegroundColor Yellow
    Write-Host ""
    if (-not (Ask-YesNo -Question "Continue with uninstall?" -Default "no")) {
        Write-Host "Cancelled. Nothing was removed." -ForegroundColor Green
        Read-Host "Press Enter to close" | Out-Null
        exit 0
    }
}

function Remove-Path {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path)) {
        Say-Skip "$Label not present (nothing to do)."
        return
    }
    try {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        Say-OK "Removed $Label ($Path)."
    } catch {
        Say-Warn "Could not remove $Label : $($_.Exception.Message). Close any open windows or Explorer tabs in $Path and try again."
    }
}

# -----------------------------------------------------------------------------
function Remove-Core {
    Say-Step "Removing the Meridian application files..."
    Say-Why  "This deletes the virtual environment, the launcher, and the install log."
    Remove-Path -Path $MERIDIAN_VENV     -Label "virtual environment"
    Remove-Path -Path $MERIDIAN_LAUNCHER -Label "console launcher"
    Remove-Path -Path $MERIDIAN_LOG      -Label "install log"
}

function Remove-DesktopShortcut {
    Say-Step "Removing the Desktop shortcut..."
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnk = Join-Path $desktop "Meridian.lnk"
    Remove-Path -Path $lnk -Label "Desktop shortcut"
}

function Maybe-RemoveProjects {
    Say-Step "Your project data..."
    Say-Why  "$MERIDIAN_PROJECTS contains every project SQLite database you have created. Deleting it cannot be undone."
    if (-not (Test-Path -LiteralPath $MERIDIAN_PROJECTS)) {
        Say-Skip "No projects folder found."
        return
    }
    if (Ask-YesNo -Question "DELETE all your project data at $MERIDIAN_PROJECTS ?" -Default "no") {
        Remove-Path -Path $MERIDIAN_PROJECTS -Label "projects folder"
    } else {
        Say-OK "Kept your projects at $MERIDIAN_PROJECTS."
    }
}

function Maybe-RemoveEnv {
    Say-Step "Your API key file..."
    Say-Why  "$MERIDIAN_ENV_FILE stores your Anthropic API key. If you plan to reinstall later, keeping this saves you re-entering it."
    if (-not (Test-Path -LiteralPath $MERIDIAN_ENV_FILE)) {
        Say-Skip "No .env file found."
        return
    }
    if (Ask-YesNo -Question "Delete the API key file?" -Default "no") {
        Remove-Path -Path $MERIDIAN_ENV_FILE -Label ".env file"
    } else {
        Say-OK "Kept your API key file."
    }
}

function Maybe-RemoveTotp {
    Say-Step "Two-factor (TOTP) secret in Windows Credential Manager..."
    Say-Why  "Meridian stores its two-factor secret as 'meridian.totp'. Removing it means you must re-enrol next time you set Meridian up."
    if (Ask-YesNo -Question "Delete the 'meridian.totp' Credential Manager entry?" -Default "no") {
        try {
            $output = & cmdkey /delete:meridian.totp 2>&1
            Write-Host "      $output" -ForegroundColor DarkGray
            Say-OK "Asked Credential Manager to delete meridian.totp."
        } catch {
            Say-Warn "cmdkey failed: $($_.Exception.Message)"
        }
    } else {
        Say-OK "Kept Credential Manager entry."
    }
}

function Maybe-RemoveLongPaths {
    Say-Step "Windows long-path support (system-wide setting)..."
    Say-Why  "Other applications on your computer may rely on this being enabled. Default is to LEAVE IT ON."
    if (Ask-YesNo -Question "Turn long-path support OFF (LongPathsEnabled = 0)?" -Default "no") {
        try {
            Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -Value 0 -Type DWord -Force
            Say-OK "Long-path support disabled."
        } catch {
            Say-Warn "Could not change registry: $($_.Exception.Message)"
        }
    } else {
        Say-OK "Left long-path support enabled."
    }
}

function Maybe-RemovePython {
    Say-Step "Python (system-wide install)..."
    Say-Why  "Other applications on your computer may rely on Python. Default is to LEAVE IT INSTALLED. If you do choose to remove it, this opens 'Apps & features' so you can do it from there -- we will not silently uninstall a system-wide tool."
    if (Ask-YesNo -Question "Open Windows 'Apps & features' so you can uninstall Python?" -Default "no") {
        try {
            Start-Process "ms-settings:appsfeatures"
            Say-OK "Opened Apps & features. Find 'Python 3.12' in the list and click Uninstall."
        } catch {
            Say-Warn "Could not open Settings: $($_.Exception.Message). Open Start menu, type 'Add or remove programs', and uninstall Python from there."
        }
    } else {
        Say-OK "Left Python installed."
    }
}

function Cleanup-Root {
    Say-Step "Cleaning up the C:\Meridian folder..."
    if (-not (Test-Path -LiteralPath $MERIDIAN_ROOT)) {
        Say-Skip "C:\Meridian already gone."
        return
    }
    $remaining = Get-ChildItem -LiteralPath $MERIDIAN_ROOT -Force -ErrorAction SilentlyContinue
    if (-not $remaining -or $remaining.Count -eq 0) {
        try {
            Remove-Item -LiteralPath $MERIDIAN_ROOT -Force -Recurse -ErrorAction Stop
            Say-OK "Removed empty $MERIDIAN_ROOT folder."
        } catch {
            Say-Warn "Could not remove $MERIDIAN_ROOT : $($_.Exception.Message)"
        }
    } else {
        Say-OK "Left $MERIDIAN_ROOT in place (still contains items you chose to keep)."
    }
}

function Show-Goodbye {
    Write-Host ""
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host "                 Uninstall finished. Take care.                 " -ForegroundColor White
    Write-Host "================================================================" -ForegroundColor Green
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
}

# -----------------------------------------------------------------------------
try {
    Ensure-Admin
    Show-Banner
    Remove-Core
    Remove-DesktopShortcut
    Maybe-RemoveProjects
    Maybe-RemoveEnv
    Maybe-RemoveTotp
    Maybe-RemoveLongPaths
    Maybe-RemovePython
    Cleanup-Root
    Show-Goodbye
} catch {
    Write-Host ""
    Write-Host "Unexpected error: $($_.Exception.Message)" -ForegroundColor Red
    Read-Host "Press Enter to close" | Out-Null
    exit 1
}
