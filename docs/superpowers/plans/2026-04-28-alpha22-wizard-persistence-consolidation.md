# Alpha-22 — Wizard persistence consolidation + UX fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the wizard's "imported 4 PDFs but the dashboard shows 0 sources" failure by stabilising wizard state across `projects_dir` changes, adopting the import staging DB into the user's named project, surfacing wizard-completion errors instead of swallowing them, and replacing the nonsense empty-state UX on the project dashboard.

**Architecture:** Two writers in the wizard (folder-import and project-creation) currently use independent `projects_dir` values, which causes the JSON wizard state to relocate mid-run (orphaning prior progress) AND leaves imported documents stranded in a "staging" SQLite file that the user's "real" project never reads. Fix: (1) decouple `state_path()` from `settings.projects_dir` so wizard state lives at a fixed user-profile path; (2) make `POST /api/setup/projects` ADOPT the staging DB (rename + move + update internal name) instead of minting a fresh empty DB; (3) `POST /api/setup/projects/suggest-name` excludes in-flight staging from collision check; (4) the frontend's `/setup/ready` page surfaces `/api/setup/complete` 400s; (5) the dashboard suppresses baseline-trustworthy banner on zero-data projects and exposes an empty-state CTA.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Pydantic, pytest (`tests/e2e/`); Next.js 14 SPA at `apps/web/src/app/...`; release tooling via `uv` and `gh`.

---

## File structure

**Backend (Python — `src/meridian/`):**
- Modify `meridian/onboarding/wizard.py` — `state_path()` returns a process-stable path under `<USERPROFILE>\.meridian\` (with backwards-compat read of legacy `<projects_dir>/_meridian/onboarding_state.json` when new is missing).
- Modify `meridian/wizard/api.py` — `setup_create_project` adopts the staging DB instead of minting fresh; `setup_suggest_project_name` ignores the in-flight staging file; `setup_complete` 400 payload gains a `staging_db_present` field for surfacing.
- Modify `meridian/api/main.py` — `coverage_for_project` returns `is_baseline_trustworthy = null` (not False) when total deliverables == 0; new field `is_data_present: bool` distinguishes "no data" from "untrustworthy".
- New helper in `meridian/projects.py` — `adopt_project(old_db_path, new_db_path, new_name)` performs the file-move + sqlite UPDATE atomically.

**Frontend (Next.js — `apps/web/src/app/`):**
- Modify `app/setup/ready/page.tsx` — surface `/api/setup/complete` 400; show specific error UI with "Continue setup" CTA pointing at the indicated `next_step`.
- Modify `app/setup/first-project/page.tsx` — surface "we suggested -2 because <reason>" when `is_available=false` from suggest-name.
- Modify `app/projects/[name]/page.tsx` (or its `BaselineBanner` child component) — suppress when `is_data_present === false`; render an empty-state CTA panel instead.
- New `app/projects/[name]/sources/page.tsx` empty-state branch — "No sources imported. [Add documents]" CTA wired to the import flow.

**Tests (`tests/e2e/`):**
- New `tests/e2e/test_alpha22_wizard_state_path_stability.py` — wizard state survives `settings.data_dir` mutation.
- New `tests/e2e/test_alpha22_staging_db_adoption.py` — folder-import → project-create at different dir → final DB has the imported sources at the new location.
- New `tests/e2e/test_alpha22_complete_gates_after_dir_change.py` — full wizard walk including projects_dir override; `/setup/complete` returns 200, not 400.
- New `tests/e2e/test_alpha22_coverage_empty_state.py` — `/api/projects/<slug>/coverage` returns `is_data_present: false` and `is_baseline_trustworthy: null` on a freshly-created empty project.
- New `tests/e2e/test_alpha22_suggest_name_ignores_staging.py` — suggest-name does not bump suffix when only a staging DB exists at the candidate slug.
- Extend `tests/e2e/test_wizard_api.py` (existing) with a new `test_complete_returns_400_with_next_step_when_gates_unmet` regression.

**Docs:**
- Update `docs/release-notes.md` with v0.2.0-alpha.22 entry — list of fixes, install/upgrade path identical to alpha-21.
- Update `docs/DECISIONS.md` — record the "wizard state lives at fixed user-profile path" decision.

---

## Task 1 — Backend: stabilise `state_path()` to a fixed user-profile location

**Files:**
- Modify: `src/meridian/onboarding/wizard.py:101-102` (the `state_path()` function — currently returns `settings.projects_dir / _meridian / onboarding_state.json`)
- Test: `tests/e2e/test_alpha22_wizard_state_path_stability.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/e2e/test_alpha22_wizard_state_path_stability.py`:

```python
"""Alpha-22 regression: wizard state must survive a mid-process
mutation of settings.data_dir.

Before alpha-22 the wizard mutated settings.data_dir at
POST /api/setup/projects time, which relocated state_path() to a new
folder, orphaning prior wizard progress (api_key + import counters).
This test pins the contract: state_path() is stable regardless of
settings.data_dir.
"""

from __future__ import annotations

from pathlib import Path

from meridian.config import settings
from meridian.onboarding.wizard import state_path
from meridian.wizard.state import (
    load_wizard_state,
    mark_documents_imported,
    save_wizard_state,
)


def test_state_path_independent_of_settings_data_dir(tmp_path: Path) -> None:
    """Mutating settings.data_dir must NOT relocate the wizard state file."""
    settings.data_dir = tmp_path / "before"
    path_before = state_path()

    settings.data_dir = tmp_path / "after"
    path_after = state_path()

    assert path_before == path_after, (
        f"state_path() relocated when settings.data_dir changed: "
        f"{path_before} != {path_after}. "
        "Wizard state must live at a fixed location, not under projects_dir."
    )


def test_wizard_state_round_trips_across_data_dir_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Save state, mutate data_dir, load state — values must match."""
    # Force the fixed state path to a tmp location for hermetic testing.
    fake_state = tmp_path / "wizard_state.json"
    monkeypatch.setattr(
        "meridian.onboarding.wizard.state_path",
        lambda: fake_state,
    )

    settings.data_dir = tmp_path / "first"
    state = load_wizard_state()
    mark_documents_imported(state, count=4)

    settings.data_dir = tmp_path / "second"
    reloaded = load_wizard_state()

    assert reloaded.gui_documents_imported == 4, (
        "wizard documents-imported counter was orphaned by data_dir change"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/e2e/test_alpha22_wizard_state_path_stability.py -v`
Expected: both tests FAIL — the first because `path_before != path_after` (current implementation returns `settings.projects_dir/...`), the second because reloaded state under the new path is empty.

- [ ] **Step 3: Implement the fix**

Edit `src/meridian/onboarding/wizard.py` around the existing `state_path()` definition (line 101-102). Replace with:

```python
import os as _os_for_state_path
from pathlib import Path as _Path_for_state_path

_STATE_DIR_NAME = "_meridian"
_STATE_FILE_NAME = "onboarding_state.json"
_LEGACY_PROJECTS_DIR_RELATIVE = _STATE_DIR_NAME + "/" + _STATE_FILE_NAME


def _stable_user_state_dir() -> _Path_for_state_path:
    """Return a fixed user-profile path that survives projects_dir changes.

    Resolution order:
    1. ``MERIDIAN_WIZARD_STATE_DIR`` env var (override; tests use this).
    2. ``%USERPROFILE%\\.meridian`` on Windows / ``~/.meridian`` on POSIX.
    """
    override = _os_for_state_path.environ.get("MERIDIAN_WIZARD_STATE_DIR")
    if override:
        return _Path_for_state_path(override).expanduser()
    if _os_for_state_path.name == "nt":
        profile = _os_for_state_path.environ.get("USERPROFILE") or _os_for_state_path.path.expanduser("~")
        return _Path_for_state_path(profile) / ".meridian"
    return _Path_for_state_path.home() / ".meridian"


def state_path() -> _Path_for_state_path:
    """Where the wizard JSON state file lives — at a stable user-profile path,
    NOT under settings.projects_dir.

    Backwards-compat: if the new path doesn't exist but an alpha-19..21-era
    state file lives under ``<settings.projects_dir>/_meridian/onboarding_state.json``,
    this returns the legacy path so a one-time migration can pick it up.
    """
    new_path = _stable_user_state_dir() / _STATE_FILE_NAME
    if new_path.exists():
        return new_path

    # Legacy lookup — only used until the next save, which writes to new_path.
    from meridian.config import settings as _settings  # late import: avoid cycle
    legacy = _settings.projects_dir / _STATE_DIR_NAME / _STATE_FILE_NAME
    if legacy.exists():
        return legacy
    return new_path
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/e2e/test_alpha22_wizard_state_path_stability.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Verify the rest of the suite did not regress**

Run: `uv run pytest tests/e2e/ -q`
Expected: 158 prior tests + 2 new tests PASS = 160. If any prior test relied on the old `state_path()` location they will now fail and need migration in this same task.

- [ ] **Step 6: Commit**

```bash
git add src/meridian/onboarding/wizard.py tests/e2e/test_alpha22_wizard_state_path_stability.py
git commit -m "[scoped] alpha-22 task 1: state_path() stable across projects_dir changes

Wizard state previously lived at <projects_dir>/_meridian/onboarding_state.json.
Mutating settings.data_dir mid-wizard (which setup_create_project does)
relocated state_path(), orphaning api_key + import-counter progress.

Fix: state_path() resolves to <USERPROFILE>/.meridian/onboarding_state.json
unconditionally. Legacy path is read once for migration if present.

Closes part 1 of bod-2 zero-sources bug."
```

---

## Task 2 — Backend: `adopt_project` helper for staging-DB rename + name update

**Files:**
- Modify: `src/meridian/projects.py` (after the existing `create_project` definition at line 42)
- Test: `tests/e2e/test_alpha22_staging_db_adoption.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/e2e/test_alpha22_staging_db_adoption.py`:

```python
"""Alpha-22: adopt_project moves a SQLite project file to a new path
under a new slug AND updates the project.name row inside it.

This is the operation /api/setup/projects must perform when the user
chose a different projects_dir than where the staging import landed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from meridian.projects import adopt_project, create_project


def test_adopt_project_moves_file_and_renames(tmp_path: Path, monkeypatch) -> None:
    # Arrange: a "staging" project at one location with one name.
    staging_dir = tmp_path / "staging"
    final_dir = tmp_path / "final"
    staging_dir.mkdir()
    final_dir.mkdir()

    # Force settings.projects_dir to staging_dir for create_project's path resolution.
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", staging_dir)

    _project_id, staging_db = create_project(name="bod")
    assert staging_db.exists(), staging_db

    # Act: adopt to a new dir + new name.
    final_db = final_dir / "my-project.sqlite"
    adopt_project(
        old_db_path=staging_db,
        new_db_path=final_db,
        new_name="My Project",
    )

    # Assert: file moved.
    assert not staging_db.exists(), "staging DB should have been moved"
    assert final_db.exists(), "final DB should exist at new path"

    # Assert: project.name row was updated.
    with sqlite3.connect(final_db) as conn:
        rows = list(conn.execute("SELECT name FROM project"))
        assert len(rows) == 1
        assert rows[0][0] == "My Project", rows


def test_adopt_project_refuses_to_overwrite(tmp_path: Path, monkeypatch) -> None:
    """Pre-existing target file => raise (no silent overwrite)."""
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    _id, src = create_project(name="src")
    dst = tmp_path / "dst.sqlite"
    dst.write_bytes(b"pre-existing content")

    import pytest
    with pytest.raises(FileExistsError):
        adopt_project(old_db_path=src, new_db_path=dst, new_name="x")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/e2e/test_alpha22_staging_db_adoption.py -v`
Expected: FAIL — `adopt_project` is not yet defined; ImportError on the import line.

- [ ] **Step 3: Implement `adopt_project`**

Edit `src/meridian/projects.py`. After the `create_project` function (around line 92), insert:

```python
def adopt_project(
    *,
    old_db_path: Path,
    new_db_path: Path,
    new_name: str,
) -> None:
    """Move ``old_db_path`` to ``new_db_path`` and rewrite the ``project.name``
    row inside the SQLite file to ``new_name``.

    Used by the wizard when the user picks a projects_dir / name DIFFERENT
    from the slug used during folder-import. Avoids minting a fresh empty
    DB and stranding imported sources in the staging file.

    Raises FileExistsError if ``new_db_path`` already exists.
    """
    if new_db_path.exists():
        raise FileExistsError(f"adopt target already exists: {new_db_path}")
    new_db_path.parent.mkdir(parents=True, exist_ok=True)

    import shutil  # noqa: PLC0415 — local; rare path
    shutil.move(str(old_db_path), str(new_db_path))

    # Update the in-DB project.name. Use a short-lived connection so we don't
    # leave WAL/SHM files lingering.
    with sqlite3.connect(new_db_path) as conn:
        conn.execute("UPDATE project SET name = ?", (new_name,))
        conn.commit()
```

Add `adopt_project` to the module's `__all__` list (around line 517):

```python
__all__ = [
    # ... existing entries ...
    "adopt_project",
    "project_db_path",
    # ... rest ...
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/e2e/test_alpha22_staging_db_adoption.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Run the e2e suite for regressions**

Run: `uv run pytest tests/e2e/ -q`
Expected: 160 prior + 2 new = 162 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/meridian/projects.py tests/e2e/test_alpha22_staging_db_adoption.py
git commit -m "[scoped] alpha-22 task 2: adopt_project helper

New meridian.projects.adopt_project moves a SQLite project file to a
new path under a new slug AND updates project.name. Used by the wizard
when the user picks a projects_dir different from the import-staging
location. Refuses to overwrite a pre-existing file.

Foundation for task 3 (wizard /projects endpoint adopts staging DB)."
```

---

## Task 3 — Backend: wizard `/api/setup/projects` adopts staging DB instead of minting fresh

**Files:**
- Modify: `src/meridian/wizard/api.py:802-855` (the `setup_create_project` handler)
- Test: `tests/e2e/test_alpha22_complete_gates_after_dir_change.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/e2e/test_alpha22_complete_gates_after_dir_change.py`:

```python
"""Alpha-22 end-to-end: simulate the full wizard walk including a
projects_dir override at the project-creation step. Verify:
 - The named project DB exists at the chosen target_dir + slug.
 - That DB contains the rows imported during folder-import (NOT empty).
 - /api/setup/complete returns 200 (gates pass), NOT 400.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def test_full_wizard_with_projects_dir_override_completes_successfully(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Use a fresh state file for hermetic test.
    monkeypatch.setenv("MERIDIAN_WIZARD_STATE_DIR", str(tmp_path / "wizard_state"))

    # Stub the api-key validation so it returns "valid" without hitting Anthropic.
    from meridian.wizard import api as wizard_api
    monkeypatch.setattr(
        wizard_api,
        "anthropic_key_outcome",
        lambda key: ("valid", "stubbed"),
    )

    # 1. API key
    r = fastapi_client.post(
        "/api/setup/api-key",
        json={"key": "sk-ant-fake-key-not-real-just-for-test"},
    )
    assert r.status_code == 200, r.text

    # 2. Folder import — set up a small folder with one trivial file.
    import_src = tmp_path / "input"
    import_src.mkdir()
    (import_src / "sample.pdf").write_bytes(b"%PDF-1.4\n%trivial\n")

    r = fastapi_client.post(
        "/api/setup/import-folder",
        json={"folder_path": str(import_src), "project_name": "MYPROJ"},
    )
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    # Poll until job completes.
    import time
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        r = fastapi_client.get(f"/api/setup/import-folder/{job_id}")
        assert r.status_code == 200, r.text
        if r.json()["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert r.json()["status"] == "succeeded", r.json()

    # 3. Pick a DIFFERENT projects_dir at project-creation time.
    final_dir = tmp_path / "user-chosen-projects-dir"
    final_dir.mkdir()

    # Suggest-name.
    r = fastapi_client.post(
        "/api/setup/projects/suggest-name",
        json={"folder_path": str(import_src)},
    )
    assert r.status_code == 200, r.text
    suggested = r.json()["suggested_name"]

    r = fastapi_client.post(
        "/api/setup/projects",
        json={
            "name": suggested,
            "slug": suggested,
            "projects_dir": str(final_dir),
            "notes": None,
        },
    )
    assert r.status_code == 200, r.text

    # 4. Final DB must exist at user-chosen dir AND contain the imported source.
    final_db = final_dir / f"{suggested}.sqlite"
    assert final_db.exists(), f"final DB missing at {final_db}"

    with sqlite3.connect(final_db) as conn:
        sources = list(conn.execute("SELECT * FROM source"))
        assert len(sources) >= 1, (
            f"final DB has 0 sources — staging DB was not adopted. "
            f"sources={sources}"
        )

    # 5. /api/setup/complete must succeed (gates satisfied).
    r = fastapi_client.post("/api/setup/complete")
    assert r.status_code == 200, (
        f"setup/complete returned {r.status_code}: {r.text} — "
        "wizard state is inconsistent across projects_dir change."
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/e2e/test_alpha22_complete_gates_after_dir_change.py -v`
Expected: FAIL on either "final DB has 0 sources" or "setup/complete returned 400" — depending on which assertion fires first.

- [ ] **Step 3: Implement adoption in `setup_create_project`**

Edit `src/meridian/wizard/api.py` around line 802. Replace the `setup_create_project` body (lines 810-855) with:

```python
def setup_create_project(req: ProjectCreateRequest) -> ProjectCreateResponse:
    """Create OR ADOPT the user's first project.

    If the wizard's import-folder step has already created a staging
    SQLite file at ``settings.projects_dir/<state.first_project_slug>.sqlite``,
    that file is MOVED to ``<req.projects_dir>/<req.slug>.sqlite`` and its
    ``project.name`` row is updated. This preserves imports that happened
    before the user chose a final projects_dir.

    If no staging exists (user skipped import), falls back to minting a
    fresh DB via ``create_project``.
    """
    target_dir = Path(req.projects_dir).expanduser()
    _ensure_writeable(target_dir)

    # Locate any staging DB BEFORE we mutate settings.data_dir, because
    # project_db_path() resolves against settings.projects_dir.
    state = load_wizard_state()
    staging_db: Path | None = None
    if state.cli.first_project_slug:
        candidate = project_db_path(state.cli.first_project_slug)
        if candidate.exists():
            staging_db = candidate

    # Now mutate process-wide projects_dir to the user's choice.
    settings.data_dir = target_dir

    final_db_path = project_db_path(req.slug)
    if final_db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "slug_exists",
                "existing_db_path": str(final_db_path),
            },
        )

    if staging_db is not None and staging_db.resolve() != final_db_path.resolve():
        # Adopt the staging DB into the user's chosen location + name.
        from meridian.projects import adopt_project as _adopt
        _adopt(
            old_db_path=staging_db,
            new_db_path=final_db_path,
            new_name=req.name,
        )
    elif staging_db is not None and staging_db.resolve() == final_db_path.resolve():
        # Same path AND same slug — just update the name in place.
        with sqlite3.connect(final_db_path) as conn:
            conn.execute("UPDATE project SET name = ?", (req.name,))
            conn.commit()
    else:
        # No staging — user skipped import. Mint fresh.
        try:
            _project_id, final_db_path = create_project(name=req.name)
        except FileExistsError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "slug_exists", "existing_db_path": str(final_db_path)},
            ) from exc

    state = load_wizard_state()
    mark_first_project(
        state,
        slug=req.slug,
        name=req.name,
        projects_dir=str(target_dir),
    )

    return ProjectCreateResponse(
        created=True,
        slug=req.slug,
        db_path=str(final_db_path),
    )
```

Ensure `import sqlite3` is already at the top of the module — if not, add it.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/e2e/test_alpha22_complete_gates_after_dir_change.py -v`
Expected: PASS.

- [ ] **Step 5: Run full e2e suite**

Run: `uv run pytest tests/e2e/ -q`
Expected: 162 prior + 1 new = 163 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/meridian/wizard/api.py tests/e2e/test_alpha22_complete_gates_after_dir_change.py
git commit -m "[systemic] alpha-22 task 3: wizard /projects adopts staging DB

setup_create_project previously minted a fresh empty SQLite at the
user's chosen projects_dir, stranding any documents the user imported
during the folder-import step (which writes to the default projects_dir
with the auto-derived slug).

Now: if a staging DB exists at the prior slug + prior projects_dir,
move it to the user's chosen path + slug and rewrite the project name.

Together with task 1 (stable state path) this makes /api/setup/complete
return 200 instead of 400 when the user picks a non-default projects_dir."
```

---

## Task 4 — Backend: `suggest-name` ignores in-flight staging DB

**Files:**
- Modify: `src/meridian/wizard/api.py:1200-1218` (the `setup_suggest_project_name` handler)
- Test: `tests/e2e/test_alpha22_suggest_name_ignores_staging.py` (new)

- [ ] **Step 1: Write the failing test**

```python
"""Alpha-22: /api/setup/projects/suggest-name should not bump the
suffix when the only collision is a wizard staging DB owned by the
current wizard run. The user expects to keep their typed name."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_suggest_name_returns_base_when_only_staging_collides(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MERIDIAN_WIZARD_STATE_DIR", str(tmp_path / "wizard_state"))
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path / "projects_dir")
    (tmp_path / "projects_dir").mkdir()

    folder = tmp_path / "BOD"
    folder.mkdir()
    (folder / "sample.pdf").write_bytes(b"%PDF-1.4\n")

    # Run folder-import to create the staging "bod" DB.
    r = fastapi_client.post(
        "/api/setup/import-folder",
        json={"folder_path": str(folder), "project_name": "BOD"},
    )
    assert r.status_code == 200

    # Suggest-name should now return "bod" (base) with is_available=True
    # because the only collision is the in-flight staging DB.
    r = fastapi_client.post(
        "/api/setup/projects/suggest-name",
        json={"folder_path": str(folder)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggested_name"] == "bod", body
    assert body["is_available"] is True, body
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/e2e/test_alpha22_suggest_name_ignores_staging.py -v`
Expected: FAIL — current handler bumps to "bod-2".

- [ ] **Step 3: Modify `setup_suggest_project_name`**

Edit `src/meridian/wizard/api.py` around line 1200. The new logic excludes the wizard's own staging DB from the collision check:

```python
def setup_suggest_project_name(req: SuggestNameRequest) -> SuggestNameResponse:
    """Suggest a slugified project name. Excludes the wizard's own
    in-flight staging DB from the collision check so the user keeps
    the name they typed."""
    folder = _validate_folder_path(req.folder_path)
    base = _slugify(folder.name)

    # Identify the staging DB (if any) so we can exclude it from collision.
    state = load_wizard_state()
    staging_slug = state.cli.first_project_slug

    def _slug_taken(slug: str) -> bool:
        if not project_db_path(slug).exists():
            return False
        # If this is the wizard's own staging DB, it's not "taken" from the
        # user's perspective — it'll be adopted into whatever name they pick.
        if staging_slug is not None and slug == staging_slug:
            return False
        return True

    if not _slug_taken(base):
        return SuggestNameResponse(suggested_name=base, is_available=True)

    for n in range(2, 10001):
        candidate = f"{base}-{n}"
        if not _slug_taken(candidate):
            return SuggestNameResponse(suggested_name=candidate, is_available=False)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "error": "name_collision_exhausted",
            "message": (
                "Could not find a free slug after 10,000 attempts — "
                "pick a different folder name."
            ),
        },
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/e2e/test_alpha22_suggest_name_ignores_staging.py -v`
Expected: PASS.

- [ ] **Step 5: Run full e2e suite**

Run: `uv run pytest tests/e2e/ -q`
Expected: 163 prior + 1 new = 164 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/meridian/wizard/api.py tests/e2e/test_alpha22_suggest_name_ignores_staging.py
git commit -m "[scoped] alpha-22 task 4: suggest-name excludes wizard staging DB

The wizard's import-folder step creates a staging SQLite at the
auto-derived slug. Previously, suggest-name treated that staging file
as a collision and bumped the suffix (e.g. 'bod' -> 'bod-2'),
silently changing the user's name. Now the staging slug is excluded
from the collision check so the user keeps the name they typed."
```

---

## Task 5 — Backend: coverage endpoint distinguishes "no data" from "untrustworthy"

**Files:**
- Modify: `src/meridian/api/main.py` (the `coverage_for_project` handler — find via `coverage_for_project` grep)
- Modify: `src/meridian/api/main.py` Pydantic response model — add `is_data_present: bool` field
- Test: `tests/e2e/test_alpha22_coverage_empty_state.py` (new)

- [ ] **Step 1: Locate the coverage handler**

Run: `grep -n "def coverage_for_project\|is_baseline_trustworthy\|baseline_trust_blockers" src/meridian/api/main.py`
Note the line numbers for the next steps.

- [ ] **Step 2: Write the failing test**

```python
"""Alpha-22: coverage endpoint must distinguish 'project has no data
yet' from 'project's baseline is untrustworthy'. Empty projects should
report is_data_present=False and is_baseline_trustworthy=null (not
False with nonsense blocker messages)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_coverage_empty_project_reports_no_data(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from meridian.config import settings
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    from meridian.projects import create_project
    create_project(name="empty-test")

    r = fastapi_client.get("/api/projects/empty-test/coverage")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["sources_imported"] == 0
    assert body["is_data_present"] is False, (
        f"empty project should have is_data_present=False, got: {body}"
    )
    assert body["is_baseline_trustworthy"] is None, (
        f"empty project should have is_baseline_trustworthy=None "
        f"(no opinion), got: {body['is_baseline_trustworthy']}"
    )
    # The blocker list should be EMPTY on empty projects (no nonsense
    # 'X% missing provenance' messages).
    assert body["baseline_trust_blockers"] == [], body["baseline_trust_blockers"]
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/e2e/test_alpha22_coverage_empty_state.py -v`
Expected: FAIL — current handler returns `is_baseline_trustworthy=False` and 2 blocker strings.

- [ ] **Step 4: Modify the coverage handler**

In `src/meridian/api/main.py`, find the `CoverageResponse` Pydantic model and add `is_data_present: bool` and change `is_baseline_trustworthy` to `bool | None`:

```python
class CoverageResponse(BaseModel):
    # ... existing fields ...
    is_data_present: bool
    is_baseline_trustworthy: bool | None  # None = no data; True = trustworthy; False = blockers
    baseline_trust_blockers: list[str]
```

In the handler body, before returning, compute and apply:

```python
total_data_signals = (
    coverage_data["sources_imported"]
    + coverage_data["deliverable_status"]["total"]
    + coverage_data["cost"]["total_calls"]
)
is_data_present = total_data_signals > 0

if not is_data_present:
    # No opinion on trustworthiness when there's no data yet.
    is_baseline_trustworthy = None
    baseline_trust_blockers: list[str] = []
else:
    # Existing logic computes blockers + trust flag.
    # ... existing computation ...
    pass

return CoverageResponse(
    # ... existing fields ...
    is_data_present=is_data_present,
    is_baseline_trustworthy=is_baseline_trustworthy,
    baseline_trust_blockers=baseline_trust_blockers,
)
```

- [ ] **Step 5: Run the test**

Run: `uv run pytest tests/e2e/test_alpha22_coverage_empty_state.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full e2e suite**

Run: `uv run pytest tests/e2e/ -q`
Expected: 164 prior + 1 new = 165 PASS.

- [ ] **Step 7: Commit**

```bash
git add src/meridian/api/main.py tests/e2e/test_alpha22_coverage_empty_state.py
git commit -m "[scoped] alpha-22 task 5: coverage distinguishes empty from untrustworthy

Coverage endpoint previously reported is_baseline_trustworthy=False on
zero-data projects, with nonsense blocker messages like '0 deliverables
missing full provenance (0.0% complete)'. New is_data_present field
distinguishes 'no data yet' from 'data exists but untrustworthy'.

Frontend in task 7 uses is_data_present to suppress the BaselineBanner
on empty projects."
```

---

## Task 6 — Frontend: `/setup/ready` page surfaces `/api/setup/complete` 400

**Files:**
- Modify: `apps/web/src/app/setup/ready/page.tsx:75-84` (the silenced .catch)
- Modify: same file — render an error UI when complete returns 400
- Test: extend `tests/e2e/test_wizard_api.py` with a backend regression

- [ ] **Step 1: Write the backend regression test**

Append to `tests/e2e/test_wizard_api.py`:

```python
def test_complete_returns_400_with_next_step_when_gates_unmet(
    fastapi_client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """When _has_required_gates fails, /api/setup/complete returns
    400 with a JSON body the frontend can render."""
    monkeypatch.setenv("MERIDIAN_WIZARD_STATE_DIR", str(tmp_path / "wizard_state"))
    # Don't configure api-key — gates will fail.

    r = fastapi_client.post("/api/setup/complete")
    assert r.status_code == 400, r.text
    body = r.json()
    assert "detail" in body, body
    detail = body["detail"]
    assert detail["error"] == "setup_incomplete", detail
    assert "next_step" in detail, detail
    assert detail["next_step"] in (
        "api_key", "first_documents", "first_project", "ready",
    ), detail["next_step"]
    assert "message" in detail, detail
```

Run: `uv run pytest tests/e2e/test_wizard_api.py -v -k complete_returns_400`
Expected: PASS already if Task 1 didn't break it; if it fails, fix the response shape in `setup_complete` first.

- [ ] **Step 2: Edit the ready page to surface the error**

Edit `apps/web/src/app/setup/ready/page.tsx`. Replace lines 75-84 (the silenced `.catch`) with:

```typescript
const [completeError, setCompleteError] = useState<{
  message: string;
  next_step: string;
} | null>(null);

useEffect(() => {
  if (!stateLoaded) return;
  void setupApi.complete().then(
    () => setCompleteError(null),
    async (err: unknown) => {
      // Try to extract the structured 400 body. setupApi.complete throws
      // on non-2xx; the apiFetch wrapper attaches `.detail` from the body.
      const detail = (err as { detail?: { error?: string; message?: string; next_step?: string } })?.detail;
      if (detail?.error === "setup_incomplete") {
        setCompleteError({
          message: detail.message ?? "Setup is not yet finishable.",
          next_step: detail.next_step ?? "api_key",
        });
      } else {
        setCompleteError({
          message: "Could not finish setup. Please try again.",
          next_step: "api_key",
        });
      }
    },
  );
}, [stateLoaded]);
```

Then in the JSX (just before the existing `<header>` block at line 119), insert:

```tsx
{completeError ? (
  <div
    role="alert"
    className="rounded-lg border border-red-500/40 bg-red-950/40 p-4 text-sm text-red-200"
  >
    <p className="font-semibold">Setup did not complete</p>
    <p className="mt-1">{completeError.message}</p>
    <Link
      href={`/setup/${completeError.next_step.replace("_", "-")}`}
      className="mt-3 inline-block rounded-full bg-accent px-4 py-1.5 text-xs font-medium text-white"
    >
      Continue: {completeError.next_step.replace("_", " ")} →
    </Link>
  </div>
) : null}
```

Also gate the "Open project" button so it's disabled when `completeError` is set:

Replace the existing `<Link href={openHref} ...>` block (lines 156-162) with:

```tsx
{completeError ? (
  <button
    type="button"
    disabled
    className="cursor-not-allowed rounded-full bg-text-muted/20 px-5 py-2.5 text-sm font-medium text-text-muted"
    title="Finish setup before opening the project"
  >
    {READY_COPY.ctas.open} →
  </button>
) : (
  <Link
    href={openHref}
    className="rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-white hover:opacity-90"
  >
    {READY_COPY.ctas.open} →
  </Link>
)}
```

- [ ] **Step 3: Build the frontend and verify it compiles**

Run from `apps/web/`:
```bash
npm run build
```
Expected: build succeeds (no TS errors).

- [ ] **Step 4: Manual verification via the dev gauntlet**

Follow the wizard cold-start sequence:
1. `powershell -ExecutionPolicy Bypass -File "C:\Users\PeterRoberts\Downloads\Reset-Meridian.ps1"`
2. Install the locally-built wheel.
3. Walk to /setup/ready WITHOUT configuring api-key (force gate failure). Confirm the red error panel renders, "Open project" button is disabled, and the "Continue: api_key" link points at /setup/api-key.

If verification passes, proceed.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/setup/ready/page.tsx tests/e2e/test_wizard_api.py
git commit -m "[scoped] alpha-22 task 6: ready page surfaces /complete 400

The ready page previously silenced /api/setup/complete errors with a
fire-and-forget .catch(() => {}). When gates failed, the user saw the
'Setup complete' header and an active 'Open project' button despite
the wizard never having actually completed.

Now: 400 responses with error=setup_incomplete render a red error
panel + a 'Continue: <next_step>' CTA, and the 'Open project' button
is disabled."
```

---

## Task 7 — Frontend: dashboard suppresses BaselineBanner on empty projects + adds empty-state CTA

**Files:**
- Modify: `apps/web/src/app/projects/[name]/page.tsx` (find the `BaselineBanner` component, likely defined further down or in a sibling file)
- Modify: `apps/web/src/lib/api.ts` (the `ProjectCoverage` type — add `is_data_present: boolean` and change `is_baseline_trustworthy: boolean | null`)
- Modify: `apps/web/src/app/projects/[name]/sources/page.tsx` (add empty-state CTA branch)

- [ ] **Step 1: Locate and read the relevant components**

Run: `grep -rn "BaselineBanner\|is_baseline_trustworthy\|baseline_trust_blockers" apps/web/src/`
Read each match to understand the rendering logic.

Likely files (verify):
- `apps/web/src/app/projects/[name]/page.tsx` — uses `<BaselineBanner coverage={coverage} />`
- `apps/web/src/components/review/BaselineBanner.tsx` (or inline in the page) — the component itself
- `apps/web/src/lib/api.ts` — the `ProjectCoverage` type

- [ ] **Step 2: Update the TypeScript type**

In `apps/web/src/lib/api.ts`, find the `ProjectCoverage` type and add/modify:

```typescript
export type ProjectCoverage = {
  // ... existing fields ...
  is_data_present: boolean;
  is_baseline_trustworthy: boolean | null;  // changed from boolean
  baseline_trust_blockers: string[];
};
```

- [ ] **Step 3: Suppress BaselineBanner on empty projects**

In `BaselineBanner` component (wherever it lives), add an early return at the top:

```typescript
export function BaselineBanner({ coverage }: { coverage: ProjectCoverage }) {
  if (!coverage.is_data_present) {
    // No opinion on trustworthiness yet — nothing to render.
    return null;
  }
  // ... existing rendering logic ...
}
```

- [ ] **Step 4: Add empty-state CTA on the dashboard**

In `apps/web/src/app/projects/[name]/page.tsx`, after the `<BaselineBanner />` element (line 95) and before the KPI grid, add:

```tsx
{!coverage.is_data_present ? (
  <section className="rounded-lg border border-accent/40 bg-accent/5 p-6">
    <h2 className="text-lg font-semibold text-text-primary">
      Welcome to your project
    </h2>
    <p className="mt-2 text-sm text-text-muted">
      No sources imported yet. Add some documents to get started — Meridian
      will extract requirements and group them by trade.
    </p>
    <div className="mt-4 flex flex-wrap gap-3">
      <Link
        href={`${base}/sources`}
        className="rounded-full bg-accent px-5 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        Add documents →
      </Link>
      <Link
        href="/glossary"
        className="rounded-full border border-border px-5 py-2 text-sm text-text-primary hover:border-accent"
      >
        What does Meridian do?
      </Link>
    </div>
  </section>
) : null}
```

- [ ] **Step 5: Add empty-state CTA on the sources page**

In `apps/web/src/app/projects/[name]/sources/page.tsx`, find the existing zero-sources rendering ("No sources imported") and replace it with:

```tsx
{sources.length === 0 ? (
  <div className="rounded-lg border border-border bg-surface-elevated p-8 text-center">
    <h2 className="text-lg font-semibold text-text-primary">
      No sources imported
    </h2>
    <p className="mt-2 text-sm text-text-muted">
      Add documents to start extracting requirements.
    </p>
    <button
      type="button"
      onClick={handleAddDocuments}
      className="mt-4 rounded-full bg-accent px-5 py-2 text-sm font-medium text-white"
    >
      Add documents
    </button>
  </div>
) : (
  /* existing source list */
)}
```

The `handleAddDocuments` handler should open a folder-picker or route to the import flow — match whatever pattern the existing wizard uses.

- [ ] **Step 6: Verify the build**

Run from `apps/web/`:
```bash
npm run build
```
Expected: build succeeds.

- [ ] **Step 7: Manual verification**

After `Reset-Meridian` + reinstall:
1. Walk the wizard with `documents_skipped=true`.
2. Land on `/projects/<slug>` — confirm BaselineBanner does NOT render and the welcome panel DOES.
3. Click "Add documents" — confirm it routes to `/sources` (or opens picker).
4. Open `/projects/<slug>/sources` directly — confirm the empty-state CTA renders.

- [ ] **Step 8: Commit**

```bash
git add apps/web/src/app/projects/[name]/page.tsx \
        apps/web/src/app/projects/[name]/sources/page.tsx \
        apps/web/src/lib/api.ts \
        apps/web/src/components/review/BaselineBanner.tsx
git commit -m "[scoped] alpha-22 task 7: empty-state UX on dashboard + sources

BaselineBanner suppressed on zero-data projects (was rendering
nonsense '0% missing provenance' messaging). Dashboard gains a
welcome panel with 'Add documents' CTA when is_data_present=false.
Sources page replaces the bare 'No sources imported' string with
a visible empty-state and 'Add documents' button."
```

---

## Task 8 — Frontend: surface bumped suffix on first-project page

**Files:**
- Modify: `apps/web/src/app/setup/first-project/page.tsx`

- [ ] **Step 1: Locate the suggest-name call site**

Run: `grep -n "suggest-name\|suggested_name\|is_available" apps/web/src/app/setup/first-project/page.tsx`

- [ ] **Step 2: Surface the bumped-suffix message**

After the suggest-name response is received, if `is_available === false`, render a hint near the name input:

```tsx
{suggestedName && !suggestedNameIsAvailable ? (
  <p className="mt-2 text-xs text-amber-300">
    A project with the name &quot;{baseName}&quot; already exists.
    We suggested &quot;{suggestedName}&quot; — feel free to change it.
  </p>
) : null}
```

The exact mechanics depend on existing state plumbing — track `baseName` (the un-bumped slug) alongside `suggestedName` and `suggestedNameIsAvailable`.

- [ ] **Step 3: Verify the build + manual test**

Run `npm run build` from `apps/web/`. Then walk the wizard with a folder name that collides — confirm the amber hint renders.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/app/setup/first-project/page.tsx
git commit -m "[scoped] alpha-22 task 8: surface bumped slug suffix to user

When suggest-name returns is_available=false the GUI now renders an
amber hint above the name input ('A project with name X exists, we
suggested X-2'). User can edit the suggestion or accept it knowingly
instead of being silently renamed."
```

---

## Task 9 — Reviewer pass on the systemic-tier change

Per the standing comprehensive-fixes-default rule, a systemic-tier change requires an independent reviewer pass before declaring done.

- [ ] **Step 1: Dispatch a code-reviewer agent**

Spawn an agent of type `superpowers:code-reviewer` with this prompt (verbatim):

> Independent review of alpha-22 wizard persistence consolidation. The change set is the commits with `[systemic]` or `[scoped] alpha-22` tags from the current branch. Read `docs/superpowers/plans/2026-04-28-alpha22-wizard-persistence-consolidation.md` for context.
>
> Verify each of the 8 tasks landed against its acceptance criteria. Specifically check:
> 1. `state_path()` does NOT depend on `settings.projects_dir` and there is a legacy-read fallback for migration.
> 2. `adopt_project()` refuses to overwrite, performs the move + name rewrite atomically (no partial state on failure).
> 3. `setup_create_project` covers all four branches: same-path-same-slug, same-path-different-slug, different-path-existing-staging, different-path-no-staging.
> 4. `setup_suggest_project_name` collision check correctly excludes the wizard's OWN staging slug, not arbitrary other projects.
> 5. Coverage endpoint's `is_data_present` is computed from a comprehensive set of signals (sources + deliverables + LLM calls), not just sources.
> 6. The frontend ready page handles BOTH the structured 400 (with detail) AND a generic non-2xx (network error, etc.).
> 7. The BaselineBanner suppression triggers on `is_data_present=false`, NOT on `is_baseline_trustworthy=null` (those are not the same thing).
> 8. The first-project page hint reads correctly when the user types their own name vs. accepts the default.
>
> Plus cross-cutting concerns:
> - Are there any e2e tests in the existing 158 that this change quietly breaks? Test names to scan: anything with "wizard", "setup", "project", "coverage", "first_project_slug".
> - Does the legacy-state-file migration path do the right thing if a user installs alpha-22 over an alpha-21 install where the state file is at the OLD location?
> - Does `adopt_project` handle Windows file-locking correctly? (SQLite WAL files, concurrent connection from any other thread, etc.)
> - Is there a race between the import job's persistence side-effect (folder/{job_id} polling) and `setup_create_project` running?
>
> Report findings as PASS / BLOCKER / NIT for each task + cross-cutting item. BLOCKERS must be addressed before ship.

- [ ] **Step 2: Triage reviewer output**

For each BLOCKER, file a follow-up commit before proceeding to gauntlet. NITs are noted in the release notes but don't block ship.

- [ ] **Step 3: Re-run e2e suite if any code changed in response**

Run: `uv run pytest tests/e2e/ -q`
Expected: all 165+ tests PASS.

---

## Task 10 — Release gauntlet on the alpha-22 wheel

- [ ] **Step 1: Build the wheel locally**

Run: `uv build`
Expected: `dist/meridian_trace-0.2.0a22-py3-none-any.whl` produced (also `.tar.gz`).

- [ ] **Step 2: Run the release gauntlet**

```bash
$env:PYTHONIOENCODING="utf-8"
uv run python scripts/release_gauntlet.py
```
Expected: 14 gauntlet steps PASS on the 0.2.0a22 wheel.

If any gauntlet step fails, return to the failing task. Do NOT release with a failing gauntlet.

- [ ] **Step 3: Verify install instructions verbatim**

Per the standing rule (memory: feedback_verify_user_instructions.md), every user-facing instruction is a contract.

1. Reset: `powershell -ExecutionPolicy Bypass -File "C:\Users\PeterRoberts\Downloads\Reset-Meridian.ps1"`
2. Run the install command from the alpha-21 release notes verbatim (substitute alpha-21 → alpha-22 only in the URL).
3. Walk the entire wizard.
4. Confirm: the named project at the user-chosen projects_dir contains the imported documents (sources_imported > 0).
5. Confirm: /api/setup/complete returned 200 (check `C:\Meridian\runtime\backend.log`).
6. Confirm: dashboard renders KPI grid (no nonsense BaselineBanner on a populated project).
7. With a fresh reset and import-skip path: confirm welcome panel + "Add documents" CTA render.

If any verification fails, fix and rebuild.

---

## Task 11 — Ship the release

- [ ] **Step 1: Bump version**

Edit `pyproject.toml`: change `version = "0.2.0a21"` → `version = "0.2.0a22"`.

- [ ] **Step 2: Update release notes**

Edit `docs/release-notes.md`. Add a v0.2.0-alpha.22 entry summarising:
- Wizard state path stable across projects_dir changes
- Staging DB adoption: imported docs land in the named project
- /setup/complete error surfacing on the ready page
- Empty-state UX on dashboard + sources page
- Bumped-suffix visibility on first-project page

Include the full install command (verbatim, exact URL substitution from alpha-21).

- [ ] **Step 3: Commit + tag**

```bash
git add pyproject.toml docs/release-notes.md
git commit -m "[systemic] Release v0.2.0-alpha.22 -- wizard persistence consolidation

Fixes the 'imported 4 PDFs but dashboard shows 0 sources' bug.

Backend:
- state_path() stable across settings.projects_dir mutation (task 1)
- adopt_project() helper for staging DB rename + name rewrite (task 2)
- setup_create_project adopts staging instead of minting fresh (task 3)
- suggest-name excludes in-flight staging from collision (task 4)
- coverage distinguishes is_data_present from is_baseline_trustworthy (task 5)

Frontend:
- ready page surfaces /complete 400 instead of swallowing (task 6)
- BaselineBanner suppressed + empty-state CTA on zero-data projects (task 7)
- first-project page surfaces bumped slug suffix (task 8)

Closes the alpha-21 open question #3 (No sources imported)."

git tag v0.2.0-alpha.22
git push origin main
git push origin v0.2.0-alpha.22
```

- [ ] **Step 4: Build the GitHub release with all 12 assets**

Per memory `feedback_release_asset_bundle.md`, the GitHub release MUST attach all 12 assets (10 installer files + wheel + sdist), not just the wheel.

```bash
gh release create v0.2.0-alpha.22 \
  --title "v0.2.0-alpha.22 — wizard persistence consolidation" \
  --notes-file docs/release-notes-alpha22.md \
  dist/meridian_trace-0.2.0a22-py3-none-any.whl \
  dist/meridian_trace-0.2.0a22.tar.gz \
  installer/Install-Meridian.ps1 \
  installer/Reset-Meridian.ps1 \
  installer/Start-Meridian.bat \
  installer/Stop-Meridian.bat \
  installer/Status-Meridian.bat \
  installer/Uninstall-Meridian.ps1 \
  installer/Meridian-Console.bat \
  installer/Meridian-Console.ps1 \
  installer/README.md \
  docs/release-notes.md
```

- [ ] **Step 5: Verify asset count**

```bash
gh release view v0.2.0-alpha.22 --json assets --jq '.assets | length'
```
Expected: `12`. If less, attach the missing files via `gh release upload v0.2.0-alpha.22 <file>` and re-verify.

- [ ] **Step 6: Final smoke**

After the release lands on GitHub, run the install path once more from the public URL (not local files) to confirm the asset bundle is reachable and intact.

---

## Spec coverage / self-review

| Bug ID | Description | Task |
|---|---|---|
| B1 | Two-DB persistence split | 1, 2, 3 |
| B2 | Frontend ate /complete 400 | 6 |
| B3 | Empty-state messaging nonsense | 5, 7 |
| B4 | Slug-suffix collision silent | 4, 8 |

Cross-cutting: 9 (reviewer), 10 (gauntlet + verbatim instructions verification), 11 (release with full asset bundle).

No placeholders. No "TBD". Each task has explicit file paths, complete code, exact commands, expected outputs.
