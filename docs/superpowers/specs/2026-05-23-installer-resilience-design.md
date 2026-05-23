# Installer Resilience — Design Spec
**Date:** 2026-05-23
**Status:** Approved

## Goal

Make the Meridian installer production-quality for non-technical users. Both the developer and the SME run the same installer on the same installed product (`C:\Meridian`). There is no separate dev server. The installer is the single deployment path for all users.

**The standard:** if a user has to Google any word in an error message, we have failed.

**The rule:** if the software knows why it failed and knows how to fix it, it fixes it silently and continues. The user sees plain-English status, never OS internals.

## Scope

Five targeted fixes to `installer/Install-Meridian.ps1`. No new files. No changes to the application itself.

---

## Fix 1 — Pre-clean corrupted dist-info before installing

**Problem:** On reinstall, old `meridian*.dist-info` directories with a missing `RECORD` file cause uv to emit a warning and can leave the venv in an incomplete state. The user sees a raw pip/uv warning they cannot interpret.

**Fix:** Before calling pip install, scan `$MERIDIAN_VENV\Lib\site-packages` for any `meridian*.dist-info` directory that is missing its `RECORD` file. Delete those directories silently. Log the action at INFO level.

**User sees:** Nothing. Silent pre-clean.

---

## Fix 2 — Auto-kill port 8000 conflict before starting backend

**Problem:** If a previous Meridian backend (or any other process) is already listening on port 8000 when the installer tries to start the backend, the new process either fails to bind or the health probe hits the wrong backend. No clear error is surfaced.

**Fix:** At the start of `Start-BackendAndOpenBrowser`, before spawning pythonw, check if port 8000 is occupied. If yes, identify the owning process and stop it. Then continue with the spawn.

**User sees:** `Meridian was already running — restarting it now.`

---

## Fix 3 — Auto-recreate venv on version mismatch, no prompt

**Problem:** `Ensure-Venv` currently prompts "Recreate it from scratch? Type 'yes' to recreate, anything else to keep it" when a venv already exists. This asks a non-technical user to make a technical decision on every reinstall.

**Fix:** Remove the interactive prompt. Replace with version-aware logic:
- Read the currently installed meridian version from the venv (via `pip show meridian` or dist-info).
- Compare against the version in the wheel being installed.
- If versions differ: recreate the venv silently, log at INFO.
- If versions match: keep the venv, skip reinstall entirely.
- If meridian is not installed at all in the venv: proceed with install (no recreate needed).

**User sees:** Either nothing (happy path) or `Updating Meridian from vX to vY — this takes a moment.`

---

## Fix 4 — "Access is denied" on venv removal → auto-stop backend first

**Problem:** If the Meridian backend is running when the installer tries to remove the venv, Windows returns "Access is denied" (OS error 5). The raw error is surfaced to the user.

**Fix:** In the `Remove-Item` block inside `Ensure-Venv`, catch access-denied errors specifically. On that error: find and stop any process listening on port 8000 (same logic as Fix 2), wait 2 seconds, then retry the removal once. If the retry also fails, surface a plain-English message.

**User sees (auto-resolved):** `Meridian was running — stopped it to complete the update.`
**User sees (retry failed):** `Meridian couldn't be updated because a file is in use. Please restart your computer and run the installer again.`

---

## Fix 5 — Fast update path on reinstall

**Problem:** The installer runs the full setup sequence on every run: Python check, long-path registry write, venv creation, pip install, API key prompt, backend start. On an update (where `C:\Meridian` already exists with a healthy install), most of this is unnecessary and adds minutes of friction.

**Fix:** At the start of the main sequence, after `Ensure-InstallDirs`, detect whether this is a **first install** or an **update**:

- **First install:** `C:\Meridian\venv\Scripts\meridian.exe` does not exist → run full sequence as today.
- **Update:** `meridian.exe` exists → run abbreviated sequence:
  1. Pre-clean dist-info (Fix 1)
  2. Ensure-Venv (version-aware, Fix 3)
  3. Install-MeridianWheel
  4. Skip API key prompt (already saved in `.env`)
  5. Write-BatLaunchers (idempotent, cheap)
  6. Start-BackendAndOpenBrowser (with Fix 2)

**User sees on update:** A shorter, faster run. No Python install step. No API key prompt. Opens the browser when done.

---

## What is not changing

- Installer output style and colour scheme — unchanged.
- API key prompt — intentionally interactive; user must supply their key on first install.
- Python install logic — unchanged; only skipped on the update path.
- Long-path registry write — only runs on first install path.
- Desktop shortcut creation — only runs on first install path.
- Application code — no changes in this spec.

---

## Testing

Each fix has a deterministic trigger:

| Fix | How to trigger |
|-----|----------------|
| 1 | Delete `RECORD` from a meridian dist-info dir; run installer |
| 2 | Start `C:\Meridian\Start-Meridian.bat`; run installer |
| 3 | Run installer twice with same wheel; run installer with newer wheel |
| 4 | Start backend; run installer without stopping it first |
| 5 | Fresh install; note steps. Re-run installer; confirm abbreviated sequence |
