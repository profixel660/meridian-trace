#Requires -Version 5.1
<#
.SYNOPSIS
    Wipe an existing Meridian install so the next installer run starts clean.

.DESCRIPTION
    Reset-Meridian audits then removes the artefacts a Meridian install
    leaves on a Windows box:
      - Running python / pythonw processes whose MainModule lives under
        C:\Meridian\venv (the Meridian backend that holds files open and
        otherwise blocks Remove-Item with "Access denied")
      - The C:\Meridian directory itself (venv, projects, runtime, .env,
        install.log, Meridian-Console.ps1, etc.)
      - The Meridian shortcut on the user's Desktop
      - Optionally, the Install-Meridian.* / Uninstall-Meridian.* files
        in the current user's Downloads folder

    Self-elevates if not started as Administrator. Safe to re-run when
    there is nothing to do (exits with "Already clean").

    NOT a substitute for the proper Uninstall-Meridian.ps1 which the
    Programs and Features entry points to (when one exists). This script
    is the "I am iterating on alphas, give me a clean state fast" tool.

.PARAMETER KeepDownloads
    Leave any Install-Meridian.* and Uninstall-Meridian.* files in
    %USERPROFILE%\Downloads alone. Default is to remove them so the next
    install starts from a freshly-downloaded .ps1 (avoids running an
    older cached installer against a newer wheel -- the alpha-4 -> alpha-5
    "wheel updated but .ps1 didn't" gotcha).

.PARAMETER Yes
    Skip the interactive confirm prompt. Use sparingly -- this script is
    destructive.

.EXAMPLE
    PS> .\Reset-Meridian.ps1
    Audits, asks for confirmation, wipes everything.

.EXAMPLE
    PS> .\Reset-Meridian.ps1 -KeepDownloads -Yes
    Wipes C:\Meridian, kills processes, removes the shortcut. Leaves the
    installer files in Downloads so you can re-run them without
    re-downloading.

.NOTES
    Lives in installer/ alongside Install-Meridian.ps1 and
    Uninstall-Meridian.ps1. Shipped as a release asset on every tag so
    users can download it the same way they download the installer.
#>

[CmdletBinding()]
param(
    [switch]$KeepDownloads,
    [switch]$Yes
)

# ---- Self-elevate -----------------------------------------------------------
$running = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $running.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Re-launching elevated..." -ForegroundColor Yellow
    $arglist = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath)
    if ($KeepDownloads) { $arglist += "-KeepDownloads" }
    if ($Yes)           { $arglist += "-Yes" }
    Start-Process powershell -Verb RunAs -ArgumentList $arglist
    exit 0
}

# ---- Audit ------------------------------------------------------------------
Write-Host ""
Write-Host "=== Audit ===" -ForegroundColor Cyan

$procs = Get-Process python, pythonw -ErrorAction SilentlyContinue |
    Where-Object {
        try { $_.MainModule.FileName -like "C:\Meridian\*" } catch { $false }
    }
if ($procs) {
    Write-Host "  Meridian python processes:" -ForegroundColor Yellow
    $procs | Select-Object Id, ProcessName,
        @{N='Path';E={try{$_.MainModule.FileName}catch{'<inacc>'}}} |
        Format-Table -AutoSize
} else {
    Write-Host "  No leftover python processes." -ForegroundColor Green
}

$instDir = "C:\Meridian"
if (Test-Path $instDir) {
    $size = (Get-ChildItem $instDir -Recurse -Force -ErrorAction SilentlyContinue |
             Measure-Object -Property Length -Sum).Sum
    Write-Host ("  C:\Meridian present ({0:N1} MB)" -f ($size/1MB)) -ForegroundColor Yellow
} else {
    Write-Host "  C:\Meridian: clean." -ForegroundColor Green
}

$shortcut = "$env:USERPROFILE\Desktop\Meridian.lnk"
if (Test-Path $shortcut) {
    Write-Host "  Desktop shortcut present." -ForegroundColor Yellow
} else {
    Write-Host "  Desktop shortcut: clean." -ForegroundColor Green
}

$dlMatches = Get-ChildItem "$env:USERPROFILE\Downloads" -Filter "*Meridian*" -ErrorAction SilentlyContinue
if ($dlMatches) {
    Write-Host "  Downloads has $($dlMatches.Count) Meridian file(s):" -ForegroundColor Yellow
    $dlMatches | Select-Object Name, LastWriteTime | Format-Table -AutoSize
} else {
    Write-Host "  Downloads: clean." -ForegroundColor Green
}

# ---- Confirm ----------------------------------------------------------------
$nothingToDo = (-not $procs) -and (-not (Test-Path $instDir)) -and `
               (-not (Test-Path $shortcut)) -and (-not $dlMatches)
if ($nothingToDo) {
    Write-Host ""
    Write-Host "Already clean. Nothing to do." -ForegroundColor Green
    Read-Host "Press Enter to exit"
    exit 0
}

if (-not $Yes) {
    Write-Host ""
    $answer = Read-Host "Wipe everything above? [y/N]"
    if ($answer -ne "y" -and $answer -ne "Y") {
        Write-Host "Aborted. Nothing changed." -ForegroundColor Yellow
        exit 0
    }
}

# ---- Wipe -------------------------------------------------------------------
Write-Host ""
Write-Host "=== Wiping ===" -ForegroundColor Cyan

if ($procs) {
    Write-Host "  Stopping python processes..."
    $procs | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    Write-Host "  Done."
}

if (Test-Path $instDir) {
    Write-Host "  Removing $instDir..."
    Remove-Item $instDir -Recurse -Force -ErrorAction Stop
    Write-Host "  Done."
}

if (Test-Path $shortcut) {
    Write-Host "  Removing desktop shortcut..."
    Remove-Item $shortcut -Force
    Write-Host "  Done."
}

if ($dlMatches -and -not $KeepDownloads) {
    Write-Host "  Removing $($dlMatches.Count) Meridian file(s) from Downloads..."
    $dlMatches | Remove-Item -Force
    Write-Host "  Done. (Use -KeepDownloads to retain them next time.)"
}

# ---- Verify -----------------------------------------------------------------
Write-Host ""
Write-Host "=== Verify ===" -ForegroundColor Cyan
$finalProcs = Get-Process python, pythonw -ErrorAction SilentlyContinue |
    Where-Object {
        try { $_.MainModule.FileName -like "C:\Meridian\*" } catch { $false }
    }
"  C:\Meridian present?     $((Test-Path $instDir))"
"  Desktop shortcut?        $((Test-Path $shortcut))"
"  Leftover python procs?   $($finalProcs.Count)"
Write-Host ""
Write-Host "Done." -ForegroundColor Green
Read-Host "Press Enter to exit"
