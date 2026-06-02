# Meridian Trace — Alpha-29 Handoff
**Date:** 2026-06-02  
**Release:** `v0.2.0-alpha.29`  
**GitHub:** https://github.com/profixel660/meridian-trace/releases/tag/v0.2.0-alpha.29

---

## What shipped in alpha-29

Two issues from SME testing round 2026-05-11.

### Issue 1 — Backend dead-end escape (Priority 1)
- Persistent **"My projects"** link added to every setup wizard screen header (`SetupShell.tsx`). Always visible, no backend dependency. Mirrors the existing escape link in the tour chrome.
- Fixed the disabled **"Open my project"** button tooltip: when `completeError.kind === "generic"` (backend unreachable) it now reads *"Meridian stopped responding — use 'My projects' to navigate away, or try again"* instead of the misleading *"Finish setup before opening the project"*.

### Issue 2 — Chunk vs file count label (Priority 2)
- Documents summary tile on the You're Ready screen now reads **"N chunks from M documents"** instead of a bare number labelled "imported".
- Backend change: `_ImportJob.chunks` accumulates `result.chunk_count` in the non-dedup branch; `mark_documents_imported(state, count, chunks)` persists both to `gui_chunks_extracted` in wizard state; `SetupStateResponse.chunks_extracted` exposes it to the frontend.
- Upgrade fallback: existing installs that haven't re-run an import show *"N documents imported"* (file count only) — graceful, no broken UI.

### Installer fix (found during release)
- Em-dash (U+2014) in the `Stop-Port8000` `Say-Info` message caused PowerShell 5.1 to misparse `Install-Meridian.ps1` on ANSI-locale machines. Replaced with `--`.

---

## Files changed

| File | Change |
|---|---|
| `src/meridian/wizard/state.py` | `gui_chunks_extracted` field + persistence; extended `mark_documents_imported` |
| `src/meridian/wizard/models.py` | `chunks_extracted` on `SetupStateResponse` |
| `src/meridian/wizard/api.py` | `_ImportJob.chunks`; both `mark_documents_imported` call sites updated |
| `tests/e2e/test_wizard_api.py` | 3 new `test_alpha29_*` tests (70 passed total) |
| `apps/web/src/lib/setupClient.ts` | `chunks_extracted: number` on `SetupState` + default |
| `apps/web/src/app/setup/ready/page.tsx` | Three-branch tile value; fixed tooltip ternary |
| `apps/web/src/components/setup/SetupShell.tsx` | "My projects" `<Link>` in wizard header |
| `installer/Install-Meridian.ps1` | Em-dash → `--` in `Stop-Port8000` message |
| `pyproject.toml` | Version bumped to `0.2.0a29` |

---

## Current state

- All code on `main`, tag `v0.2.0-alpha.29` pushed.
- Release assets: `Meridian-alpha-29-installer.zip` (includes `Uninstall-Meridian.ps1/.bat`) + wheel.
- Test suite: 70 passed, 1 warning (pre-existing asyncio_mode warning, unrelated).
- Release gauntlet: steps 1–7i pass. Step 7j (LLM pipeline smoke) was blocked by a transient Anthropic API overload at release time — not a code defect; alpha-29 makes no changes to the LLM pipeline.
- SME review: proxy review conducted 2026-05-31 (SME was sick); approved.

---

## Install / update flow for the SME

Download `Meridian-alpha-29-installer.zip`, extract, run `Install-Meridian.bat`. No uninstall step needed — the installer detects the existing installation, kills any running backend, and updates in place.

---

## Deferred issues (not in alpha-29)

| Priority | Issue |
|---|---|
| 3 | Folder picker shows folder name only, not full path |
| 4 | Conflict flags show UUIDs instead of document filenames |
| 4 | Conflict accept/reject status lost on back-navigation |
| 4 | Basis of Design: 0 deliverables for OSE Requisition Forms (SYD2_Shells_C and D) |
| 5 | "Audit Rows" label unexplained |
| 5 | No way to return to the tour after dismissing it |
| 5 | "Extraction Path: excluded" / "bod_import" visible as internal labels |

---

## Build notes for next release

- Run gauntlet with `UV_LINK_MODE=copy` (repo is on OneDrive; uv hardlinks fail otherwise)
- Pass `ANTHROPIC_API_KEY` from `C:\Meridian\.env` for step 7j
- "Command contains script block" warning in PowerShell during gauntlet run is cosmetic — safe to ignore
- Order: `npm run build` in `apps/web` → `uv build --wheel` → gauntlet → zip → tag → release
