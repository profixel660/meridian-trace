# Alpha-29 Design — Backend dead-end escape + chunk/file count label

**Date:** 2026-05-24
**Status:** Approved
**Issues addressed:** Priority 1 (backend dead mid-session) and Priority 2 (X documents imported = chunks not files) from SME testing round 2026-05-11.

---

## Context

SME tested alpha-26 and alpha-27 on 2026-05-11. Two blockers surfaced:

1. **Backend dead mid-session — no escape route.** SME clicked "Take a Tour" from the You're Ready screen, got ERR_CONNECTION_REFUSED, then found "Try again" did nothing and "Open my project" was blocked with an unhelpful tooltip. The SetupShell ✕ and ← Back buttons exist but were not obvious enough as escape routes.

2. **"X documents imported" shows chunk count, not file count.** SME has 4 files; saw 84/88/96 depending on the test. She diagnosed it correctly and asked for "96 chunks imported from 4 documents". The `mark_documents_imported` state accumulates across wizard runs, and the label conflates chunks with files.

---

## Issue 1 — Backend dead-end escape route

### Scope

Minimal escape: a persistent "My projects" link visible at all times in the setup wizard chrome.

### Changes

#### `apps/web/src/components/setup/SetupShell.tsx`

Add a `<Link href="/">My projects</Link>` text link to the header row, positioned between the brand label and the ✕ close button. Always rendered — no conditional logic, no backend dependency. Mirrors the existing pattern in `OnboardingShell.tsx` line 44 ("Projects" link top-right).

Approximate placement in the header `<div>`:

```
[Meridian - Trace setup]   [My projects]   [step indicator]   [✕]
```

#### `apps/web/src/app/setup/ready/page.tsx`

The disabled "Open my project" `<button>` has a single `title` prop. Change it to distinguish two error kinds:

- `completeError.kind === "incomplete"` → keep existing: `"Finish setup before opening the project"`
- `completeError.kind === "generic"` (backend unreachable) → change to: `"Meridian is unreachable — use 'My projects' to navigate away, or try again"`

One ternary on the existing `title` attribute. No other changes to the ready page.

### What is NOT changing

- No polling, no health banner, no changes to `setupApi` or the backend.
- "Try again" behaviour is unchanged (it already re-runs `/complete` on nonce bump; if the backend restarts the user can click it).

---

## Issue 2 — Chunk vs file count label

### Scope

Track chunk count through the import job and persist it alongside file count in wizard state. Expose both via `SetupStateResponse`. Update the ready-page summary tile to read "N chunks from M documents".

### Backend changes

#### `src/meridian/wizard/api.py` — `_ImportJob`

Add `chunks: int = 0` field to the `_ImportJob` dataclass, alongside the existing `imported: int`.

In `_run_import_job`, in the non-dedup branch where `job.imported += 1` is called, also add:

```python
job.chunks += result.chunk_count
```

Both status poll handlers (`setup_import_status` and `setup_import_folder_status`) call `mark_documents_imported(state, count=job.imported)` on the first `succeeded` observation. Update both call sites to also pass `chunks=job.chunks`.

#### `src/meridian/wizard/state.py` — `WizardState`

Add `gui_chunks_extracted: int = 0` to the `WizardState` class, initialised to `0`.

Update the JSON persistence key constant: add `_GUI_KEY_CHUNKS_EXTRACTED = "gui_chunks_extracted"`.

Update `load_wizard_state` to read `gui_chunks_extracted=_int_or_zero(raw.get(_GUI_KEY_CHUNKS_EXTRACTED))`.

Update `save_wizard_state` to write `payload[_GUI_KEY_CHUNKS_EXTRACTED] = state.gui_chunks_extracted`.

Extend `mark_documents_imported` to accept `chunks: int = 0` and accumulate:

```python
def mark_documents_imported(state: WizardState, *, count: int, chunks: int = 0) -> None:
    state.gui_documents_imported = max(0, state.gui_documents_imported) + count
    state.gui_chunks_extracted = max(0, state.gui_chunks_extracted) + chunks
    ...
```

Add a `chunks_extracted` property on `WizardState` returning `self.gui_chunks_extracted`.

#### `src/meridian/wizard/models.py` — `SetupStateResponse`

Add `chunks_extracted: int = Field(default=0, description="Total chunks extracted across all imported documents.")` to the Pydantic model.

#### `src/meridian/wizard/api.py` — `_state_to_response`

Add `chunks_extracted=state.chunks_extracted` to the `SetupStateResponse(...)` constructor call.

### Frontend changes

#### `apps/web/src/lib/setupClient.ts` — `SetupState`

Add `chunks_extracted: number` to the interface. Add `chunks_extracted: 0` to `DEFAULT_SETUP_STATE`.

#### `apps/web/src/app/setup/ready/page.tsx`

Read `state.chunks_extracted` alongside `state.documents_imported`. Pass both to the summary tile value expression. Rename `docCount` to `fileCount` for clarity (local variable only).

#### `apps/web/src/components/setup/copy.ts` — `READY_COPY`

Update the docs summary tile value builder. Current:

```
docCount === 0 ? "Queued" : `${docCount} imported`
```

New (approximate):

```
chunkCount === 0 && fileCount === 0
  ? "Queued"
  : `${chunkCount} chunks from ${fileCount} ${fileCount === 1 ? "document" : "documents"}`
```

Update `READY_COPY.hero` to use `fileCount` (it already says "first N documents are being processed" — wording is fine, just ensure variable is file count not chunk count).

### Schema migration

None required. The wizard state is a JSON sidecar (`~/.meridian/onboarding_state.json`). Missing `gui_chunks_extracted` key on read defaults to `0`. Existing installs that haven't re-run an import show "0 chunks from N documents" after upgrade — acceptable for alpha. The counts are only cosmetic on the ready page.

---

## Files changed (summary)

| File | Change |
|---|---|
| `apps/web/src/components/setup/SetupShell.tsx` | Add "My projects" link to header |
| `apps/web/src/app/setup/ready/page.tsx` | Fix disabled-button tooltip; read `chunks_extracted` |
| `apps/web/src/components/setup/copy.ts` | Update docs tile label to "N chunks from M documents" |
| `apps/web/src/lib/setupClient.ts` | Add `chunks_extracted` to `SetupState` + default |
| `src/meridian/wizard/api.py` | Add `_ImportJob.chunks`; increment in job loop; pass to `mark_documents_imported`; wire `_state_to_response` |
| `src/meridian/wizard/state.py` | Add `gui_chunks_extracted`; extend `mark_documents_imported`; persist/load |
| `src/meridian/wizard/models.py` | Add `chunks_extracted` to `SetupStateResponse` |

---

## Out of scope for alpha-29

- Fixing the `mark_documents_imported` accumulation across wizard reinstalls (the 84/88/96 accumulation). Separate issue; the label fix is valuable regardless.
- Health polling / backend health banner on the ready page (deferred per design decision).
- "Try again" polling behaviour.
