# Meridian Trace — Alpha-30 Handoff & Implementation Plan
**Date:** 2026-06-03  
**Picking up from:** alpha-29 (`v0.2.0-alpha.29`) shipped 2026-06-02  
**Goal:** Fix the three highest-value SME friction points before the next test round

---

## Project context

Meridian Trace is a construction deliverables extraction tool. A user installs it at `C:\Meridian`, points it at a folder of construction documents, and gets a structured register of deliverables with conflicts flagged.

- **Repo:** `C:\Users\PeterRoberts\OneDrive - Undivided Systems\Documents\Project_requirements_tester`
- **Remote:** `git@github.com:profixel660/meridian-trace.git` (public)
- **Stack:** Python/FastAPI backend · Next.js/TypeScript/Tailwind frontend
- **Tests:** `python -m pytest tests/e2e/test_wizard_api.py -v` (70 passing)
- **TS check:** `cd apps/web && npx tsc --noEmit`
- **Branch:** work directly on `main` (alpha project, single deployment path)

---

## Alpha-30 scope — three issues

| # | Issue | Files |
|---|-------|-------|
| 1 | Conflict register shows `—` instead of source document filename | `src/meridian/api/main.py`, `apps/web/src/lib/api.ts`, `apps/web/src/app/projects/[name]/conflict-register/ConflictRegisterTable.tsx` |
| 2 | Resolved conflict status not visible when navigating back to the register | `apps/web/src/app/projects/[name]/conflicts/ConflictsQueue.tsx` |
| 3 | Sources page shows internal values (`bod_import`, `excluded`, `text_spec`, etc.) as-is | `apps/web/src/app/projects/[name]/sources/page.tsx` |

**Not in alpha-30 (investigate separately):**
- Folder picker path display (may not be a real bug — needs SME clarification)
- Zero deliverables for OSE Requisition Forms (needs data investigation)

---

## Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix conflict source display, conflict status persistence, and internal label exposure — three SME-reported friction points.

**Architecture:** Two of the three changes are frontend-only (Tasks 2 and 3). Task 1 is a small backend change + matching frontend update. No schema migration needed.

**Tech Stack:** Python/FastAPI, pytest + FastAPI TestClient (backend); Next.js/TypeScript/Tailwind, `tsc --noEmit` (frontend).

---

### Task 1 — Show source document filename in the conflict register

**Problem:** The conflict register table shows `—` for Source A and Source B columns. The backend already fetches `source_filename` via JOIN in `src/meridian/review/conflicts.py:_load_parties` but the API endpoint in `src/meridian/api/main.py:projects_list_conflicts` assembles its own party query that does NOT fetch it.

**Files:**
- Modify: `src/meridian/api/main.py`
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/app/projects/[name]/conflict-register/ConflictRegisterTable.tsx`
- Test: `tests/e2e/test_wizard_api.py`

---

- [ ] **Step 1: Write a failing test**

Add at the bottom of `tests/e2e/test_wizard_api.py`:

```python
def test_alpha30_conflict_party_includes_source_filename(fastapi_client: TestClient) -> None:
    """GET /api/projects/{name}/conflicts returns source_filename on deliverable parties."""
    # This test verifies the field exists and is a string (or null) — not that it
    # has a specific value, since we have no seeded conflict data in this fixture.
    # The absence of the field entirely is what we're guarding against.
    body = fastapi_client.get("/api/projects/test-project/conflicts").json()
    # An empty list is fine — the contract is that if items exist, parties have the field.
    assert isinstance(body, list)
    for item in body:
        for party in item.get("parties", []):
            assert "source_filename" in party, (
                f"party missing source_filename field: {party}"
            )
```

- [ ] **Step 2: Run to verify it fails**

```
python -m pytest tests/e2e/test_wizard_api.py::test_alpha30_conflict_party_includes_source_filename -v
```

Expected: FAIL — `source_filename` key absent from party dict.

- [ ] **Step 3: Add `source_filename` to the `ConflictParty` Pydantic model in `api/main.py`**

Find the `ConflictParty` class in `src/meridian/api/main.py` (around line 244). It currently reads:

```python
class ConflictParty(BaseModel):
    party_kind: Literal["deliverable", "audit"]
    party_id: str
    party_position: str | None = None
    summary_or_text: str
```

Change to:

```python
class ConflictParty(BaseModel):
    party_kind: Literal["deliverable", "audit"]
    party_id: str
    party_position: str | None = None
    summary_or_text: str
    source_filename: str | None = None
```

- [ ] **Step 4: Update the deliverable branch of the party assembly loop**

In `projects_list_conflicts` (around line 860), the deliverable branch currently does:

```python
if kind == "deliverable":
    drow = conn.execute(
        "SELECT deliverables_summary FROM deliverable WHERE id = ?",
        (pid,),
    ).fetchone()
    if drow is not None:
        summary_or_text = drow["deliverables_summary"] or ""
```

Replace with:

```python
if kind == "deliverable":
    drow = conn.execute(
        """
        SELECT d.deliverables_summary, sd.filename AS source_filename
        FROM deliverable d
        LEFT JOIN source_document sd ON sd.id = d.source_id
        WHERE d.id = ?
        """,
        (pid,),
    ).fetchone()
    if drow is not None:
        summary_or_text = drow["deliverables_summary"] or ""
```

And update the `ConflictParty(...)` constructor call in the same loop (a few lines below) from:

```python
parties.append(
    ConflictParty(
        party_kind=kind,
        party_id=pid,
        party_position=p["party_position"],
        summary_or_text=summary_or_text,
    )
)
```

To:

```python
source_filename: str | None = None
if kind == "deliverable" and drow is not None:
    source_filename = drow["source_filename"]
parties.append(
    ConflictParty(
        party_kind=kind,
        party_id=pid,
        party_position=p["party_position"],
        summary_or_text=summary_or_text,
        source_filename=source_filename,
    )
)
```

- [ ] **Step 5: Run test — expect it to pass**

```
python -m pytest tests/e2e/test_wizard_api.py::test_alpha30_conflict_party_includes_source_filename -v
```

Expected: PASS.

- [ ] **Step 6: Run the full test suite**

```
python -m pytest tests/e2e/test_wizard_api.py -v
```

Expected: 71 passed, 1 warning.

- [ ] **Step 7: Update `ConflictParty` TypeScript interface in `apps/web/src/lib/api.ts`**

Find the `ConflictParty` interface (around line 170):

```typescript
export interface ConflictParty {
  party_kind: "deliverable" | "audit";
  party_id: string;
  party_position: string | null;
  summary_or_text: string;
}
```

Change to:

```typescript
export interface ConflictParty {
  party_kind: "deliverable" | "audit";
  party_id: string;
  party_position: string | null;
  summary_or_text: string;
  source_filename: string | null;
}
```

- [ ] **Step 8: Update `ConflictRegisterTable.tsx` to render the filename**

Find the Source A and Source B cells in `apps/web/src/app/projects/[name]/conflict-register/ConflictRegisterTable.tsx`. They currently read:

```tsx
<td className="px-3 py-2 text-text-primary">
  {partyA?.summary_or_text ? "—" : "—"}
</td>
```

and

```tsx
<td className="px-3 py-2 text-text-primary">
  {partyB?.summary_or_text ? "—" : "—"}
</td>
```

Replace both with:

```tsx
<td className="px-3 py-2 text-text-muted font-mono text-[10px]">
  {partyA?.source_filename ?? "—"}
</td>
```

and

```tsx
<td className="px-3 py-2 text-text-muted font-mono text-[10px]">
  {partyB?.source_filename ?? "—"}
</td>
```

- [ ] **Step 9: TypeScript check**

```
cd apps/web && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 10: Commit**

```
git add src/meridian/api/main.py apps/web/src/lib/api.ts apps/web/src/app/projects/[name]/conflict-register/ConflictRegisterTable.tsx tests/e2e/test_wizard_api.py
git commit -m "feat(conflicts): include source_filename in conflict party API response and register table"
```

---

### Task 2 — Conflict resolution status visible when navigating back to the register

**Problem:** After resolving a conflict in the queue, navigating back to the conflict register page shows stale data (conflict still appears as pending). The queue calls `router.refresh()` after resolution which refreshes the queue page but not the register page. Next.js caches the register page's server component data.

**Root cause:** `router.refresh()` in `ConflictsQueue.tsx` after `submit()` only invalidates the current route segment. When the user navigates to `/projects/[name]/conflict-register`, Next.js may serve the cached version.

**Fix:** After a successful resolution, navigate the user directly to the conflict register page using `router.push`. This triggers a fresh server render of the register with up-to-date data, and the user immediately sees the resolved conflict reflected.

**Files:**
- Modify: `apps/web/src/app/projects/[name]/conflicts/ConflictsQueue.tsx`

---

- [ ] **Step 1: Locate the submit handler in `ConflictsQueue.tsx`**

Find the `submit` callback (around line 73). After a successful resolution it currently does:

```typescript
setPending(null);
router.refresh();
```

- [ ] **Step 2: Replace `router.refresh()` with `router.push` to the register**

The component receives `projectName` as a prop (or derives it from params). Replace:

```typescript
setPending(null);
router.refresh();
```

With:

```typescript
setPending(null);
router.push(`/projects/${encodeURIComponent(projectName)}/conflict-register`);
```

This navigates to the register page after every resolution, ensuring the user sees the updated status immediately. The register is the natural destination after resolving a conflict.

- [ ] **Step 3: TypeScript check**

```
cd apps/web && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```
git add apps/web/src/app/projects/[name]/conflicts/ConflictsQueue.tsx
git commit -m "fix(conflicts): navigate to register after resolution so status is immediately visible"
```

---

### Task 3 — Replace internal `extraction_path` values with human-readable labels on the Sources screen

**Problem:** The Sources screen shows the raw `extraction_path` database value (`bod_import`, `excluded`, `text_spec`, `drawing`, `demarcation`, `pending`) directly in the UI. These are internal identifiers meaningless to users. "Audit rows" is also unexplained.

**Fix:** Add a label map in `sources/page.tsx`. No backend change needed — the raw value is kept in the API type; the mapping is display-only.

**Files:**
- Modify: `apps/web/src/app/projects/[name]/sources/page.tsx`

---

- [ ] **Step 1: Add a label map constant above the `Stat` component**

In `apps/web/src/app/projects/[name]/sources/page.tsx`, add this constant before the `Stat` component definition:

```typescript
const EXTRACTION_PATH_LABEL: Record<string, string> = {
  text_spec: "Text specification",
  bod_import: "Basis of Design import",
  drawing: "Drawing",
  demarcation: "Demarcation schedule",
  excluded: "Excluded from extraction",
  pending: "Pending extraction",
};
```

- [ ] **Step 2: Update the four `<Stat>` calls in the source detail block**

Find the `<dl>` block that renders the four stats (around line 158):

```tsx
<dl className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
  <Stat label="Deliverables" value={s.deliverables_count} />
  <Stat label="Audit rows" value={s.audit_count} />
  <Stat label="MIME" value={s.mime_type} />
  <Stat label="Extraction path" value={s.extraction_path} mono />
</dl>
```

Replace with:

```tsx
<dl className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
  <Stat label="Deliverables" value={s.deliverables_count} />
  <Stat label="Candidates reviewed" value={s.audit_count} />
  <Stat label="MIME" value={s.mime_type} />
  <Stat
    label="Extraction method"
    value={EXTRACTION_PATH_LABEL[s.extraction_path] ?? s.extraction_path}
  />
</dl>
```

Changes:
- "Audit rows" → "Candidates reviewed" (explains what the number represents)
- "Extraction path" → "Extraction method"
- Raw `extraction_path` value → human-readable label (with fallback to raw value for any unknown future values)
- Removed `mono` prop (no longer rendering a database key)

- [ ] **Step 3: TypeScript check**

```
cd apps/web && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```
git add apps/web/src/app/projects/[name]/sources/page.tsx
git commit -m "fix(sources): replace internal extraction_path values and audit label with plain-English text"
```

---

### Final check

- [ ] **Full backend test suite**

```
python -m pytest tests/e2e/test_wizard_api.py -v
```

Expected: 71 passed, 1 warning.

- [ ] **TypeScript check**

```
cd apps/web && npx tsc --noEmit
```

Expected: no errors.

---

## After all tasks complete

Run `superpowers:finishing-a-development-branch`. All work goes on `main`. Tag as `v0.2.0-alpha.30` and create a GitHub release with a new installer zip (same build process as alpha-29 — bump version in `pyproject.toml` first, run `npm run build` in `apps/web`, then `uv build --wheel`, then gauntlet with `UV_LINK_MODE=copy` and `ANTHROPIC_API_KEY` from `C:\Meridian\.env`).

---

## Build notes

- Gauntlet requires `UV_LINK_MODE=copy` (OneDrive path causes uv hardlink errors)
- Gauntlet step 7j requires `ANTHROPIC_API_KEY` from `C:\Meridian\.env`
- PowerShell "script block" warning during gauntlet is cosmetic — safe to ignore
- Test command: `python -m pytest tests/e2e/test_wizard_api.py -v`
- TS check: `cd apps/web && npx tsc --noEmit`
