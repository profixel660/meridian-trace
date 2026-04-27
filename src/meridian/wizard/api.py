"""FastAPI router exposing the GUI setup wizard.

Mounted by :mod:`meridian.api.main` at the root prefix (paths begin
``/setup``). The Tauri-bundled SPA's first-run wizard (Stream B) is the
canonical consumer.

DEFERRED: see §3.2 — every ``/setup/*`` endpoint is intentionally PUBLIC
(no ``Depends(require_session)``). The wizard runs PRE-AUTH; a "team
edition" tightening (require an admin token to access setup once initial
setup is done) is a future operator decision recorded in
``OVERNIGHT_REPORT.md`` §3.2. Until then, anyone with HTTP access to the
host can drive setup. This is acceptable for v1 because the wizard runs
on localhost inside Tauri.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, HTTPException, Request, status

from meridian.config import settings
from meridian.db.connection import connect
from meridian.ingest import ingest_file
from meridian.ingest.dispatcher import walk_directory
from meridian.logging import get_logger
from meridian.projects import _slugify, create_project, project_db_path
from meridian.wizard.models import (
    ApiKeyRequest,
    ApiKeyResponse,
    FolderImportJobStatusResponse,
    FolderImportRequest,
    FolderScanRequest,
    FolderScanResponse,
    FolderSkipEntry,
    ImportJobResponse,
    ImportJobStatusResponse,
    ImportRequest,
    ImportSkipRequest,
    ImportSkipResponse,
    ProjectCreateRequest,
    ProjectCreateResponse,
    SetupCompleteResponse,
    SetupDefaultsResponse,
    SetupStateResponse,
    SuggestNameRequest,
    SuggestNameResponse,
)
from meridian.wizard.state import (
    _KEYRING_ACCOUNT,
    _KEYRING_SERVICE,
    OnboardingState,
    WizardState,
    load_wizard_state,
    mark_documents_imported,
    mark_documents_skipped,
    mark_first_project,
    mark_setup_complete,
    save_wizard_state,
    set_api_key_configured,
    validate_anthropic_key_str,
)

_log = get_logger("meridian.wizard")

# --------------------------------------------------------------------------
# Rate limiting — same shape as meridian/auth/login_api.py: in-memory
# sliding-window bucket keyed by client IP. 10 attempts / 5 minutes.
# Applied to /setup/api-key only; the other endpoints are cheap and
# state-changes are user-driven.
# --------------------------------------------------------------------------

_RATE_LIMIT_WINDOW_SECONDS = 5 * 60
_RATE_LIMIT_MAX_ATTEMPTS = 10

_rate_buckets: dict[str, deque[float]] = {}
_rate_lock = Lock()


def _client_ip(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def _check_rate_limit(ip: str) -> bool:
    now = time.monotonic()
    cutoff = now - _RATE_LIMIT_WINDOW_SECONDS
    with _rate_lock:
        bucket = _rate_buckets.setdefault(ip, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_MAX_ATTEMPTS:
            return False
        bucket.append(now)
        return True


# --------------------------------------------------------------------------
# Import-job tracker — minimal in-memory job board.
#
# Reuses meridian.ingest.dispatcher.ingest_file directly. The tracker
# spawns a daemon thread per request; each call is short (single-digit
# seconds for typical PDFs) so the GUI's poll loop completes in 1-3
# pings. The job records are never persisted — if the user reloads the
# wizard mid-import the GUI restarts the import (idempotent because
# ingest_file dedupes on content_hash).
# --------------------------------------------------------------------------


class _ImportJob:
    __slots__ = (
        "_persisted",
        "completed",
        "current_file",
        "deduped",
        "errors",
        "id",
        "imported",
        "status",
        "total",
    )

    def __init__(self, total: int) -> None:
        self.id = str(uuid.uuid4())
        self.status = "pending"
        self.total = total
        self.completed = 0
        self.imported = 0
        self.deduped = 0
        self.errors: list[str] = []
        self.current_file: str | None = None
        self._persisted = False


_jobs: dict[str, _ImportJob] = {}
_jobs_lock = Lock()


def _run_import_job(job: _ImportJob, *, db_path: Path, paths: Iterable[str]) -> None:
    """Worker body — drives ingest_file over each path, updates the job record."""
    job.status = "running"
    try:
        # Long busy_timeout: another process (e.g. CLI) might hold the
        # write lock. 30s mirrors the existing ingest endpoint.
        conn = connect(db_path, busy_timeout_ms=30000)
    except Exception as exc:  # pragma: no cover — extremely defensive
        job.status = "failed"
        job.errors.append(f"Could not open project DB: {exc}")
        return

    try:
        for raw in paths:
            path = Path(raw).expanduser()
            job.current_file = str(path)
            try:
                if not path.exists():
                    job.errors.append(f"File not found: {path}")
                    job.completed += 1
                    continue
                result = ingest_file(
                    conn,
                    file_path=path,
                    project_root=settings.project_root,
                )
                if result.deduped:
                    job.deduped += 1
                else:
                    job.imported += 1
            except Exception as exc:  # noqa: BLE001 — surface every error to UI
                job.errors.append(f"{Path(raw).name}: {exc}")
            finally:
                job.completed += 1
    finally:
        conn.close()
        job.current_file = None

    job.status = "failed" if job.errors and job.imported == 0 and job.deduped == 0 else "succeeded"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _state_to_response(state: WizardState) -> SetupStateResponse:
    return SetupStateResponse(
        complete=state.is_complete,
        api_key_set=state.api_key_set,
        first_project_slug=state.first_project_slug,
        first_project_name=state.gui_first_project_name,
        first_project_dir=state.gui_first_project_dir,
        documents_imported=state.documents_imported,
        documents_skipped=state.documents_skipped,
        next_step=state.next_step,  # type: ignore[arg-type]
    )


def _ensure_writeable(target: Path) -> None:
    """Create the directory if needed; raise HTTPException 400 on failure."""
    try:
        target.mkdir(parents=True, exist_ok=True)
        # Probe write permission with a transient file. Avoids leaving
        # litter on success.
        probe = target / ".meridian-writeable-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "projects_dir_not_writeable",
                "message": (
                    f"Folder isn't writeable — try a different one. ({exc})"
                ),
            },
        ) from exc


def _invalid_key_message(detail: str) -> str:
    """PM-language message for an invalid-key outcome."""
    # We do not surface the raw exception text — it can include 401 bodies
    # with internal hostnames. The PM-targeted message stays stable.
    _log.info("wizard.api_key.invalid", detail=detail[:200])
    return (
        "Anthropic rejected this key. Check the key starts with 'sk-ant-' "
        "and that the account has billing set up."
    )


def _unable_to_verify_message(detail: str) -> str:
    _log.info("wizard.api_key.unable_to_verify", detail=detail[:200])
    return (
        "Couldn't reach Anthropic to check the key (network or SDK issue). "
        "Saved it anyway — the first real extraction will surface any "
        "auth errors clearly."
    )


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

wizard_router = APIRouter(prefix="/setup", tags=["setup"])


@wizard_router.get("/state", response_model=SetupStateResponse)
def setup_state() -> SetupStateResponse:
    """Return the current wizard state.

    Public — no auth required (pre-setup, no user / token exists yet).
    Reads from ``<projects_dir>/_meridian/onboarding_state.json``. A user
    who started in CLI then opens the GUI sees CLI-driven progress
    correctly reflected in ``next_step``.
    """
    return _state_to_response(load_wizard_state())


@wizard_router.get("/defaults", response_model=SetupDefaultsResponse)
def setup_defaults() -> SetupDefaultsResponse:
    """Server-side defaults the frontend pre-fills into form fields.

    Returns ``projects_dir`` resolved from the same ``_meridian_home()``
    chain the rest of the backend uses (MERIDIAN_HOME env, then
    ``C:\\Meridian`` on Windows when present, then ``~/Meridian``), with
    ``/projects`` appended. Always a real, valid filesystem path —
    never a placeholder like ``C:\\Users\\<you>\\...`` (the alpha-5 bug
    class). The frontend submits this verbatim, so the round-trip must
    survive Pydantic + the OS path validators.
    """
    from meridian.config import _meridian_home  # local import — narrow surface

    home = _meridian_home()
    return SetupDefaultsResponse(projects_dir=str(home / "projects"))


@wizard_router.post(
    "/api-key",
    response_model=ApiKeyResponse,
    responses={
        429: {"description": "Too many validation attempts from this IP."},
    },
)
def setup_api_key(req: ApiKeyRequest, request: Request) -> ApiKeyResponse:
    """Validate + persist an Anthropic API key.

    Three-outcome (per user MEMORY: pass/fail/borderline). On 'valid' or
    'unable_to_verify' the key is persisted to the OS keychain via the
    same store the rest of Meridian uses (see
    ``meridian.auth.secrets.default_store``). On 'invalid' nothing is
    persisted and the wizard's ``api_key_configured`` flag stays False.
    """
    ip = _client_ip(request)
    if not _check_rate_limit(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many validation attempts. Try again in "
                f"{_RATE_LIMIT_WINDOW_SECONDS // 60} minutes."
            ),
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW_SECONDS)},
        )

    outcome, detail = validate_anthropic_key_str(req.key)

    if outcome == "invalid":
        # Do NOT persist; do NOT mark the step complete. Surface the
        # PM-language message; log the underlying detail for ops.
        return ApiKeyResponse(
            outcome="invalid",
            message=_invalid_key_message(detail),
        )

    # Persist on valid OR unable_to_verify. The CLI wizard never persists,
    # but the GUI is the durable-install path and re-prompting on every
    # launch is the worst-possible UX (per UX-discoverability MEMORY).
    _persist_api_key(req.key)

    state = load_wizard_state()
    set_api_key_configured(state)

    if outcome == "valid":
        return ApiKeyResponse(outcome="valid", message="Key validated.")
    return ApiKeyResponse(
        outcome="unable_to_verify",
        message=_unable_to_verify_message(detail),
    )


def _persist_api_key(key: str) -> None:
    """Stash the key in the OS keychain via the ``keyring`` module.

    Uses a wizard-specific service name (``meridian.api_key``) so it
    doesn't collide with the TOTP-secret service (``meridian.totp``).
    Falls back to logging-only if keyring is unavailable — the env-var
    override (``ANTHROPIC_API_KEY``) remains a valid path for advanced
    users and the wizard already accepts that as a separate code path.
    """
    try:
        import keyring  # noqa: PLC0415

        keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, key)
    except Exception as exc:  # noqa: BLE001 — keyring backends vary wildly
        _log.warning(
            "wizard.api_key.keyring_unavailable",
            error=f"{type(exc).__name__}: {exc}",
        )


@wizard_router.post(
    "/projects",
    response_model=ProjectCreateResponse,
    responses={
        400: {"description": "Projects directory is not writeable."},
        409: {"description": "A project with this slug already exists."},
    },
)
def setup_create_project(req: ProjectCreateRequest) -> ProjectCreateResponse:
    """Create the user's first project.

    Honours ``projects_dir`` from the request: this lets the GUI's
    folder-picker drive where projects live. We override
    ``settings.data_dir`` for the rest of this process so subsequent
    endpoints (import, etc.) target the same location.
    """
    target_dir = Path(req.projects_dir).expanduser()
    _ensure_writeable(target_dir)

    # Process-wide: subsequent project_db_path() calls (in import / status
    # endpoints) need to resolve against the GUI-chosen directory.
    settings.data_dir = target_dir

    db_path = project_db_path(req.slug)
    if db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "slug_exists",
                "existing_db_path": str(db_path),
            },
        )

    try:
        _project_id, db_path = create_project(name=req.name)
    except FileExistsError as exc:  # race: created between our exists() check and create
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "slug_exists", "existing_db_path": str(db_path)},
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
        db_path=str(db_path),
    )


@wizard_router.post("/import", response_model=ImportJobResponse)
def setup_import(req: ImportRequest) -> ImportJobResponse:
    """Kick off a background import job; return a job_id immediately."""
    db_path = project_db_path(req.project_slug)
    if not db_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {req.project_slug}",
        )

    job = _ImportJob(total=len(req.paths))
    with _jobs_lock:
        _jobs[job.id] = job

    thread = threading.Thread(
        target=_run_import_job,
        kwargs={"job": job, "db_path": db_path, "paths": list(req.paths)},
        daemon=True,
        name=f"wizard-import-{job.id[:8]}",
    )
    thread.start()
    return ImportJobResponse(job_id=job.id)


@wizard_router.get(
    "/import/{job_id}",
    response_model=ImportJobStatusResponse,
)
def setup_import_status(job_id: str) -> ImportJobStatusResponse:
    """Poll an import job's progress.

    Side-effect on terminal status: when the job first hits ``succeeded``
    we update the persisted ``documents_imported`` counter. This ensures
    that even if the GUI never polls again the count is durable (e.g.
    user closes wizard before the success status is rendered).
    """
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    # On terminal-state transition, fold the import count into wizard state.
    # Use a once-per-job sentinel by removing the job from the dict — a
    # subsequent poll will get a fresh 'succeeded' snapshot via a re-add
    # below. Simpler: track a flag on the job object.
    if job.status == "succeeded" and not job._persisted:
        if job.imported > 0:
            state = load_wizard_state()
            mark_documents_imported(state, count=job.imported)
        # mark persisted regardless, even if imported==0 (all-deduped) —
        # the subsequent poll should not re-mark.
        job._persisted = True

    return ImportJobStatusResponse(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        total=job.total,
        completed=job.completed,
        imported=job.imported,
        deduped=job.deduped,
        errors=list(job.errors),
    )


@wizard_router.post(
    "/import/skip",
    response_model=ImportSkipResponse,
)
def setup_import_skip(req: ImportSkipRequest) -> ImportSkipResponse:
    """Mark the document-import step skipped. Idempotent."""
    state = load_wizard_state()
    if state.first_project_slug is None or state.first_project_slug != req.project_slug:
        # Defensive: skipping the step only makes sense after the project
        # has been created. Return 400 so the GUI surfaces it rather than
        # silently advancing.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot skip document import before creating the first project."
            ),
        )
    mark_documents_skipped(state)
    return ImportSkipResponse(acknowledged=True)


@wizard_router.post(
    "/complete",
    response_model=SetupCompleteResponse,
)
def setup_complete() -> SetupCompleteResponse:
    """Idempotent: stamp setup as complete and return the final state."""
    state = load_wizard_state()
    # Defensive: only allow completion when the gates are satisfied. The
    # GUI shouldn't normally hit this branch (the Finish button is gated
    # in the wizard flow), but a misbehaving client deserves a clear 400
    # rather than a confused 'complete' state.
    if not state._has_required_gates():  # noqa: SLF001 — internal helper, intentional
        # Three-outcome: surface the missing gate as a hint.
        next_step = state.next_step
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "setup_incomplete",
                "next_step": next_step,
                "message": (
                    f"Setup not finishable yet — next step is '{next_step}'."
                ),
            },
        )
    mark_setup_complete(state)
    return SetupCompleteResponse(**_state_to_response(load_wizard_state()).model_dump())


# --------------------------------------------------------------------------
# Folder-import endpoints (round-18 / Stream A)
#
# PMs do not think in files; they think in project folders. The
# /setup/import-folder/{scan,POST,GET} trio lets the GUI render
# "we'll ingest these 47 files from <folder>; press Import" instead of
# forcing the user through a per-file picker.
# --------------------------------------------------------------------------


def _validate_folder_path(raw: str) -> Path:
    """Resolve ``raw`` to an existing directory or raise HTTP 400.

    Three failure modes — each surfaced with a distinct error code so the
    GUI can render specific guidance:

    * ``folder_not_found``     — path does not exist on disk.
    * ``folder_not_a_directory`` — path exists but is a regular file.
    * ``folder_access_denied`` — Errno 13 / PermissionError raised while
      probing existence (e.g. Windows-elevated parent directory).
    """
    candidate = Path(raw).expanduser()
    try:
        exists = candidate.exists()
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "folder_access_denied",
                "message": f"Cannot access this folder — permission denied. ({exc})",
            },
        ) from exc
    except OSError as exc:
        # Cover other Errno-13-adjacent failures (Windows long-path,
        # network timeout on a UNC share, etc.).
        if getattr(exc, "errno", None) == 13:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "folder_access_denied",
                    "message": f"Cannot access this folder — permission denied. ({exc})",
                },
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "folder_not_found",
                "message": f"Cannot read this folder. ({exc})",
            },
        ) from exc
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "folder_not_found",
                "message": f"Folder does not exist: {candidate}",
            },
        )
    if not candidate.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "folder_not_a_directory",
                "message": (
                    f"Path is not a directory — pick a folder, not a file: {candidate}"
                ),
            },
        )
    return candidate


@wizard_router.post(
    "/import-folder/scan",
    response_model=FolderScanResponse,
    responses={
        400: {
            "description": (
                "folder_path missing, not a directory, or unreadable. The "
                "detail body's `error` field is one of folder_not_found, "
                "folder_not_a_directory, folder_access_denied."
            )
        },
    },
)
def setup_import_folder_scan(req: FolderScanRequest) -> FolderScanResponse:
    """Walk ``folder_path`` recursively and return a manifest.

    Pure scan — no DB writes, no LLM calls, no ingest. Reuses
    :func:`meridian.ingest.dispatcher.walk_directory` so the canonical
    extension-set lives next to ``ingest_file``.
    """
    folder = _validate_folder_path(req.folder_path)
    walked = walk_directory(folder)
    return FolderScanResponse(
        folder_path=walked.folder_path,
        folder_name=walked.folder_name,
        files_by_kind=walked.files_by_kind,
        skipped=[
            FolderSkipEntry(path=s.path, reason=s.reason)  # type: ignore[arg-type]
            for s in walked.skipped
        ],
        total_ingestable=walked.total_ingestable,
    )


@wizard_router.post(
    "/import-folder",
    response_model=ImportJobResponse,
    responses={
        400: {"description": "folder_path is missing, not a directory, or unreadable."},
    },
)
def setup_import_folder(req: FolderImportRequest) -> ImportJobResponse:
    """Walk ``folder_path`` and queue every supported file for ingestion.

    Auto-creates the project if it does not yet exist (the alpha-2 swapped
    step order asks for the folder BEFORE the project name; the folder name
    becomes the default project name and a project record is created here so
    a downstream rename on /setup/first-project remains optional). Idempotent
    — content_hash dedup is applied per-file in
    :func:`meridian.ingest.ingest_file`; calling this twice on the same
    folder produces a job whose ``deduped`` count equals the file count.
    """
    folder = _validate_folder_path(req.folder_path)
    slug = _slugify(req.project_name)
    db_path = project_db_path(slug)
    if not db_path.exists():
        # Side-effect: project is created on the fly. Mirrors the setup_create_project
        # path so the wizard state stays consistent (first_project_slug stamped).
        _project_id, db_path = create_project(name=req.project_name)
        state = load_wizard_state()
        mark_first_project(
            state,
            name=req.project_name,
            slug=slug,
            projects_dir=str(db_path.parent),
        )

    walked = walk_directory(folder)
    paths: list[str] = []
    for kind_paths in walked.files_by_kind.values():
        paths.extend(kind_paths)

    job = _ImportJob(total=len(paths))
    with _jobs_lock:
        _jobs[job.id] = job

    thread = threading.Thread(
        target=_run_import_job,
        kwargs={"job": job, "db_path": db_path, "paths": paths},
        daemon=True,
        name=f"wizard-folder-import-{job.id[:8]}",
    )
    thread.start()
    return ImportJobResponse(job_id=job.id)


@wizard_router.get(
    "/import-folder/{job_id}",
    response_model=FolderImportJobStatusResponse,
)
def setup_import_folder_status(job_id: str) -> FolderImportJobStatusResponse:
    """Poll a folder-import job. Same shape as ``/setup/import/{job_id}``
    plus a ``current_file`` field for in-flight progress."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    # Same persistence side-effect as /setup/import/{job_id}: on the first
    # poll that observes a 'succeeded' state, fold the import count into
    # the durable wizard state. Re-polls are no-ops.
    if job.status == "succeeded" and not job._persisted:
        if job.imported > 0:
            state = load_wizard_state()
            mark_documents_imported(state, count=job.imported)
        job._persisted = True

    return FolderImportJobStatusResponse(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        total=job.total,
        completed=job.completed,
        imported=job.imported,
        deduped=job.deduped,
        failed=list(job.errors),
        current_file=job.current_file,
    )


# --------------------------------------------------------------------------
# /setup/projects/suggest-name (round-18 / Stream A)
#
# When the GUI's folder picker fires, the wizard wants to suggest a project
# name = folder basename, slugified per ``meridian.projects._slugify`` so
# the suggestion matches what the SQLite filename will actually be. This
# endpoint also detects collision and bumps a numeric suffix until unique
# so the user is never offered a name that will 409 on create.
# --------------------------------------------------------------------------


@wizard_router.post(
    "/projects/suggest-name",
    response_model=SuggestNameResponse,
    responses={
        400: {"description": "folder_path is missing, not a directory, or unreadable."},
    },
)
def setup_suggest_project_name(req: SuggestNameRequest) -> SuggestNameResponse:
    """Suggest a slugified project name from a folder basename.

    Returns ``is_available=True`` when the naive (un-suffixed) slug is
    free, ``False`` when the server had to bump the suffix because a
    project at that slug already exists.
    """
    folder = _validate_folder_path(req.folder_path)
    base = _slugify(folder.name)
    if not project_db_path(base).exists():
        return SuggestNameResponse(suggested_name=base, is_available=True)

    # Bump suffix until unique. Cap at a sane number to avoid a runaway loop
    # if someone has 10k projects all sharing a basename — at that point the
    # GUI needs to surface the situation, not silently pick -10001.
    for n in range(2, 10001):
        candidate = f"{base}-{n}"
        if not project_db_path(candidate).exists():
            return SuggestNameResponse(suggested_name=candidate, is_available=False)

    # Pathological: the user has 10k+ collisions. Surface a clear error
    # rather than spin or fabricate. The same 400-shape pattern as
    # _validate_folder_path above.
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


__all__ = ["wizard_router"]


# --------------------------------------------------------------------------
# Re-exports for tests — keeps test imports tidy.
# --------------------------------------------------------------------------

# OnboardingState surfaces here for symmetry with the CLI module's import
# path. Tests sometimes import it via meridian.wizard.api when they want to
# stay inside the wizard package's namespace.
_ = (OnboardingState, save_wizard_state)
